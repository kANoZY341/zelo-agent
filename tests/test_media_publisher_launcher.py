"""Pure operator launcher checks; no optional verifier execution or native claims."""

import asyncio
import base64
import dataclasses
import errno
import json
import os
from pathlib import Path

import pytest

from qualification import media_publisher as m
from qualification.media_publisher_guest import GUEST_CASES
from scripts import qualify_media_publisher as q

RAW = (
    Path(__file__).resolve().parents[1] / "qualification/fixtures/pillow-publisher-provenance.json"
).read_bytes()


def test_fixed_namespace_arguments_and_environment():
    argv = q._argv(41, 42, "positive", Path("/host/public-sentinel"))
    for source, target in (("/proc/self/fd/41", m.TOOL_ROOT), ("/proc/self/fd/42", m.ROOT_FILE)):
        index = argv.index(source)
        assert argv[index - 1] == "--ro-bind" and argv[index + 1] == target
        assert index < argv.index("--remount-ro")
    assert argv[argv.index("--cap-drop") + 1] == "ALL"
    assert {
        "--unshare-user",
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-net",
        "--unshare-uts",
    }.issubset(argv)
    assert "--share-net" not in argv
    command_index = max(i for i, value in enumerate(argv) if value == "/usr/bin/python3.12")
    assert argv[command_index + 1 : command_index + 4] == ["-I", "-S", "-B"]
    policy = q.confinement_policy()
    assert set(policy["environment"]) == {"PATH", "LANG", "LC_ALL", "TMPDIR"}
    assert policy["runtime_authority"] is False
    assert "no-hard-io-deadline" in policy["filesystem_preflight"]
    policy["environment"].clear()
    assert q.confinement_policy()["environment"]


@pytest.mark.parametrize("case", GUEST_CASES)
def test_closed_controls_only_change_expected_inputs(case):
    value = q._mutate(RAW, case)
    changed = {
        "signature",
        "certificate",
        "promise",
        "proof_hash",
        "checkpoint",
        "integrated_time",
        "subject_hash",
        "subject_name",
        "extra_attestation",
    }
    assert (value != RAW) == (case in changed)
    if case == "extra_attestation":
        with pytest.raises(m.PublisherError, match="count"):
            m._envelope(value)
    elif case in changed:
        m._envelope(value)
    if case in ("subject_hash", "subject_name"):
        original = json.loads(RAW)["attestation_bundles"][0]["attestations"][0]
        mutated = json.loads(value)["attestation_bundles"][0]["attestations"][0]
        assert original["envelope"]["signature"] == mutated["envelope"]["signature"]
        assert base64.b64decode(original["envelope"]["statement"]) != base64.b64decode(
            mutated["envelope"]["statement"]
        )


@pytest.mark.parametrize(
    "case", ["wrong_issuer", "wrong_repository", "wrong_commit", "wrong_workflow"]
)
def test_identity_controls_change_one_claim(case):
    expected = m._identity_values("positive")
    actual = m._identity_values(case)
    assert sum(a != b for a, b in zip(actual, expected, strict=True)) == 1
    with pytest.raises(m.PublisherError, match="control"):
        m._identity_values("caller-policy")


def test_result_binding_checks_complete_receipt():
    receipt = m._verify(RAW, lambda a: (m.DSSE_TYPE, base64.b64decode(a["envelope"]["statement"])))
    value = {
        "status": "attributed",
        "attribution": dataclasses.asdict(receipt),
        "digest": receipt.digest,
    }
    assert q._accepted(value, RAW)
    for name in value["attribution"]:
        changed = json.loads(json.dumps(value))
        changed["attribution"][name] = "wrong"
        assert not q._accepted(changed, RAW)
    assert not q._accepted(None, RAW)
    assert not q._accepted(value | {"extra": True}, RAW)
    assert not q._accepted(value | {"digest": "0" * 64}, RAW)


def test_private_read_and_exclusive_write(tmp_path, monkeypatch):
    monkeypatch.setattr(q, "ROOT", tmp_path)
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o700)
    path = runtime / "evidence"
    q._save(path, b"fixed")
    assert q._read(path, 5) == b"fixed"
    with pytest.raises(FileExistsError):
        q._save(path, b"replacement")
    with pytest.raises(q.QualificationRefused):
        q._read(path, 4)
    with pytest.raises(q.QualificationRefused, match="pin"):
        q._read(path, 5, "0" * 64)
    link = runtime / "link"
    link.symlink_to(path)
    with pytest.raises(q.QualificationRefused):
        q._read(link, 5)
    os.chmod(runtime, 0o777)
    with pytest.raises(q.QualificationRefused, match="runtime"):
        q._read(path, 5)


def test_proof_capacity_and_entries(tmp_path):
    q._proof_capacity(tmp_path)
    for i in range(31):
        (tmp_path / f"publisher-proof-{i}").mkdir(mode=0o700)
    q._proof_capacity(tmp_path)
    (tmp_path / "publisher-proof-31").mkdir(mode=0o700)
    with pytest.raises(q.QualificationRefused, match="count"):
        q._proof_capacity(tmp_path)


@pytest.mark.parametrize("kind", ["file", "symlink", "mode", "name"])
def test_invalid_proof_entries_refused(tmp_path, kind):
    path = tmp_path / ("unexpected" if kind == "name" else "publisher-proof-test")
    if kind == "file":
        path.write_bytes(b"fixed")
    elif kind == "symlink":
        path.symlink_to(tmp_path)
    else:
        path.mkdir(mode=0o755 if kind == "mode" else 0o700)
    with pytest.raises(q.QualificationRefused, match="entry"):
        q._proof_capacity(tmp_path)


@pytest.mark.parametrize(
    "case",
    [
        "signature",
        "certificate",
        "wrong_issuer",
        "wrong_repository",
        "wrong_commit",
        "wrong_workflow",
    ],
)
@pytest.mark.parametrize(
    "code",
    [
        "media_publisher_verifier_setup_refused",
        "media_publisher_guest_required",
        "media_publisher_tool_manifest",
        "media_publisher_root",
        "media_publisher_count",
    ],
)
def test_infrastructure_failures_never_pass_crypto_controls(case, code):
    assert not q._negative_accepted(case, {"status": "refused", "code": code})
    assert q._negative_accepted(
        case, {"status": "refused", "code": "media_publisher_crypto_refused"}
    )


def test_root_and_cardinality_controls_require_the_specific_gate():
    assert q._negative_accepted("wrong_root", {"status": "refused", "code": "media_publisher_root"})
    assert q._negative_accepted(
        "extra_attestation", {"status": "refused", "code": "media_publisher_count"}
    )
    for case in ("wrong_root", "extra_attestation"):
        assert not q._negative_accepted(
            case, {"status": "refused", "code": "media_publisher_crypto_refused"}
        )


@pytest.mark.parametrize(
    "change",
    [
        {"namespace": "net:[10]"},
        {"interfaces": ["lo", "eth0"]},
        {"route_rows": 1},
        {"connect_errno": errno.ECONNREFUSED},
        {"connect_errno": None},
        {"enforced": False},
        {"extra": True},
    ],
)
def test_network_rejection_needs_namespace_interface_and_route_evidence(change):
    value = {
        "status": "bounded_probe",
        "probe": "network",
        "enforced": True,
        "namespace": "net:[11]",
        "interfaces": ["lo"],
        "route_rows": 0,
        "connect_errno": errno.ENETUNREACH,
    }
    assert q._network_accepted(value, "net:[10]")
    assert not q._network_accepted(value | change, "net:[10]")
    assert not q._network_accepted(
        {"status": "bounded_probe", "probe": "network", "enforced": True}, "net:[10]"
    )


def test_external_and_repeated_cancellation_settles_operation_before_return():
    async def scenario():
        started = asyncio.Event()
        waiting = asyncio.Event()
        cleaning = asyncio.Event()
        release = asyncio.Event()
        finished = asyncio.Event()

        async def child():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                await release.wait()
                finished.set()

        async def pre_cancel_wait():
            waiting.set()
            await asyncio.Event().wait()

        operation = asyncio.create_task(child())
        owner = asyncio.create_task(q._own_operation(operation, cancel_wait=pre_cancel_wait))
        await started.wait()
        await waiting.wait()
        owner.cancel()
        await cleaning.wait()
        owner.cancel()
        await asyncio.sleep(0)
        assert not owner.done() and not finished.is_set()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await owner
        assert operation.done() and finished.is_set()

    asyncio.run(scenario())


def test_control_cancellation_requires_ready_signal_then_settles():
    async def scenario():
        started = asyncio.Event()
        readiness = asyncio.Event()
        cleaned = asyncio.Event()

        async def child():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()

        operation = asyncio.create_task(child())
        owner = asyncio.create_task(q._own_operation(operation, cancel_wait=readiness.wait))
        await started.wait()
        await asyncio.sleep(0)
        assert not operation.done()
        readiness.set()
        assert await owner == (None, "operator_cancelled")
        assert operation.done() and cleaned.is_set()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "code",
    ["media_publisher_root", "media_publisher_crypto_refused", "media_publisher_tool_manifest"],
)
def test_verifier_preserves_fixed_categories(code):
    def refused(*args):
        raise m.PublisherError(code)

    with pytest.raises(m.PublisherError, match=code):
        m._verify(RAW, refused)
