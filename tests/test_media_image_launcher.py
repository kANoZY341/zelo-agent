"""Pure transfer/profile/report checks; no guest or copied package execution."""

import asyncio
import copy
import dataclasses
import json
import resource
from pathlib import Path

import pytest

from qualification import media_image as m
from qualification.media_image_guest import GUEST_BOOTSTRAP, GUEST_CASES, IMAGE_ROOT, READY_PATH
from qualification.media_preparation import _expected
from scripts import qualify_media_image as q
from tests.media_wheel_fixtures import inspect, make


@pytest.fixture
def manifest():
    return _expected(inspect(make()))


def scanned(manifest, case="copy", probes=None):
    return {
        "status": "image_scanned",
        "case": case,
        "manifest": json.loads(json.dumps(dataclasses.asdict(manifest))),
        "digest": manifest.digest,
        "after_manifest": json.loads(json.dumps(dataclasses.asdict(manifest))),
        "after_digest": manifest.digest,
        "probes": {} if probes is None else probes,
        "imports_absent": True,
        "environment_closed": True,
    }


def test_guest_compiles_without_executing():
    compile(GUEST_BOOTSTRAP, "fixed-image-scanner", "exec")
    assert "__import__(" not in GUEST_BOOTSTRAP
    assert "find_spec(name)" in GUEST_BOOTSTRAP
    assert "from PIL" not in GUEST_BOOTSTRAP and "import PIL" not in GUEST_BOOTSTRAP


def test_each_file_has_explicit_mode_fd_and_fixed_destination(manifest):
    handles = tuple((item, 100 + i) for i, item in enumerate(manifest.files))
    argv = q._argv(handles, manifest, "copy", Path("/host/public-sentinel"))
    readonly = argv.index("--remount-ro")
    positions = [i for i, value in enumerate(argv) if value == "--file"]
    assert len(positions) == len(handles)
    for index, (item, fd) in zip(positions, handles, strict=True):
        assert argv[index - 2 : index + 3] == [
            "--perms",
            f"{item.mode:04o}",
            "--file",
            str(fd),
            IMAGE_ROOT + "/" + item.path,
        ]
        assert index < readonly
    chmods = [i for i, value in enumerate(argv) if value == "--chmod"]
    assert len(chmods) == len(manifest.directories)
    assert all(argv[i + 1] == "0500" and i > max(positions) and i < readonly for i in chmods)
    assert "--unshare-net" in argv and "--share-net" not in argv
    assert argv[argv.index("--cap-drop") + 1] == "ALL"
    assert "--bind-fd" not in argv
    cancelled = q._argv(handles, manifest, "cancel", Path("/host/public-sentinel"), 240)
    index = cancelled.index("--bind-fd")
    assert cancelled[index + 1 : index + 3] == ["240", READY_PATH]


def test_source_closure_only_mounts_fixed_scanner_modules(manifest):
    argv = q._argv((), manifest, "copy", Path("/host/public-sentinel"))
    binds = [argv[i + 1 : i + 3] for i, value in enumerate(argv) if value == "--ro-bind"]
    source = [pair for pair in binds if pair[1].startswith(q.SOURCE_ROOT)]
    assert len(source) == len(q.SOURCE_FILES)
    assert {Path(pair[1]).name for pair in source} == set(q.SOURCE_FILES)
    assert not any(
        name in q.SOURCE_FILES
        for name in ("media_image.py", "media_publisher.py", "application.py")
    )
    assert q.confinement_policy()["package_path_on_sys_path"] is False


@pytest.mark.parametrize(
    "field",
    [
        "status",
        "case",
        "manifest",
        "digest",
        "after_manifest",
        "after_digest",
        "imports_absent",
        "environment_closed",
    ],
)
def test_report_rejects_any_wrong_bound_field(manifest, field):
    value = scanned(manifest)
    assert q._accepted(value, manifest, "copy", "net:[10]")
    value[field] = "wrong"
    assert not q._accepted(value, manifest, "copy", "net:[10]")
    assert not q._accepted(scanned(manifest) | {"extra": True}, manifest, "copy", "net:[10]")


@pytest.mark.parametrize(
    "kind", ["file_hash", "file_mode", "file_size", "directory_mode", "roles", "policy"]
)
def test_complete_manifest_changes_refused(manifest, kind):
    value = scanned(manifest)
    changed = value["manifest"]
    if kind.startswith("file_"):
        changed["files"][0][kind.removeprefix("file_").replace("hash", "sha256")] = "wrong"
    elif kind == "directory_mode":
        changed["directories"][0][1] = 0o700
    else:
        changed[kind] = []
    assert not q._accepted(value, manifest, "copy", "net:[10]")


def test_mutations_require_all_independent_readonly_results(manifest):
    probes = dict.fromkeys(q.MUTATIONS, True)
    value = scanned(manifest, "mutations", probes)
    assert q._accepted(value, manifest, "mutations", "net:[10]")
    for name in probes:
        changed = copy.deepcopy(value)
        changed["probes"][name] = False
        assert not q._accepted(changed, manifest, "mutations", "net:[10]")
    assert not q._accepted(scanned(manifest, "mutations", {}), manifest, "mutations", "net:[10]")


@pytest.mark.parametrize(
    "case,probes",
    [
        ("host_file", {"inaccessible": True}),
        ("imports", {"PIL": True, "app": True}),
        ("memory", {"refused": True}),
    ],
)
def test_other_probes_require_complete_exact_result(manifest, case, probes):
    assert q._accepted(scanned(manifest, case, probes), manifest, case, "net:[10]")
    assert not q._accepted(scanned(manifest, case, {}), manifest, case, "net:[10]")
    assert not q._accepted(
        scanned(manifest, case, probes | {"extra": True}), manifest, case, "net:[10]"
    )


def test_capacity_checks_fixed_count_and_owned_entry_types(tmp_path):
    q._proof_capacity(tmp_path)
    for i in range(3):
        (tmp_path / f"image-proof-{i}").mkdir(mode=0o700)
    q._proof_capacity(tmp_path)
    (tmp_path / "image-proof-3").mkdir(mode=0o700)
    with pytest.raises(q.QualificationRefused, match="count"):
        q._proof_capacity(tmp_path)


def test_setup_limit_refusal_does_not_raise_any_shared_limit(monkeypatch):
    monkeypatch.setattr(q.resource, "getrlimit", lambda *a: (1, 1))
    monkeypatch.setattr(q.resource, "setrlimit", lambda *a: pytest.fail("limit changed"))
    with pytest.raises(q.QualificationRefused, match="operator_limits"):
        q._operator_limits()


def test_fixed_profile_and_no_limits_changed_by_import():
    limit = resource.getrlimit(resource.RLIMIT_NOFILE)
    policy = q.confinement_policy()
    assert policy["setup_inherited_limits"]["fds"] == 256
    assert policy["scanner_limits"]["fds"] == 64
    assert policy["package_import_or_execution"] is False
    assert policy["aggregate_host_tmpfs_memory_enforced"] is False
    assert set(policy["environment"]) == {"PATH", "LANG", "LC_ALL", "TMPDIR"}
    policy["environment"].clear()
    assert q.confinement_policy()["environment"]
    assert resource.getrlimit(resource.RLIMIT_NOFILE) == limit
    assert len(GUEST_CASES) == 12 and len(set(GUEST_CASES)) == 12


def test_seal_controls_on_synthetic_bytes_do_not_execute_contents():
    data = make(add={"PIL/empty.bin": b""})
    with m._seal_image(data, inspect(data)) as image:
        handles = image._begin_consumer()
        try:
            controls = q._seal_controls(handles)
            assert controls["files"] == len(handles)
            assert controls["write"] == controls["grow"] == len(handles)
            assert (
                controls["shrink"]
                == controls["shared_write_mmap"]
                == sum(item.size > 0 for item, _ in handles)
            )
        finally:
            image._end_consumer(True)
    assert image.cleanup_confirmed


def test_boolean_integer_alias_is_not_a_complete_manifest():
    data = make(add={"PIL/empty.bin": b""})
    manifest = _expected(inspect(data))
    value = scanned(manifest)
    empty = next(item for item in value["manifest"]["files"] if item["size"] == 0)
    empty["size"] = False
    assert value["manifest"] == json.loads(json.dumps(dataclasses.asdict(manifest)))
    assert not q._accepted(value, manifest, "copy", "net:[10]")


@pytest.mark.parametrize(
    "case,key", [("host_file", "inaccessible"), ("imports", "PIL"), ("memory", "refused")]
)
def test_probe_truth_values_are_strict_booleans(manifest, case, key):
    probes = (
        {"inaccessible": True}
        if case == "host_file"
        else {"PIL": True, "app": True}
        if case == "imports"
        else {"refused": True}
    )
    probes[key] = 1
    assert not q._accepted(scanned(manifest, case, probes), manifest, case, "net:[10]")


def test_setup_failure_uses_only_fixed_missing_mount(manifest):
    argv = q._argv((), manifest, "setup_failure", Path("/host/proof/sentinel"))
    index = argv.index("/host/proof/absent-setup-source")
    assert argv[index - 1 : index + 2] == [
        "--ro-bind",
        "/host/proof/absent-setup-source",
        "/opt/absent-setup-source",
    ]
    command = argv[argv.index("-c") :]
    assert command[2] == "copy"


def test_repeated_cancel_control_waits_for_operation_cleanup(monkeypatch):
    async def scenario():
        cleaned = asyncio.Event()
        release = asyncio.Event()
        cleaning = asyncio.Event()

        async def ready(*args):
            return None

        async def child():
            try:
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                await release.wait()
                cleaned.set()

        monkeypatch.setattr(q.shared, "_ready", ready)
        operation = asyncio.create_task(child())
        control = asyncio.create_task(q._repeated_cancel(operation, 100, q.time.monotonic() + 2))
        await cleaning.wait()
        await asyncio.sleep(0)
        assert not control.done() and not cleaned.is_set()
        release.set()
        assert await control == (None, "operator_repeated_cancelled")
        assert operation.done() and cleaned.is_set()

    asyncio.run(scenario())
