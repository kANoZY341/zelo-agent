"""Offline/fake-only tests for the synthetic Media native-reader operator."""

import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from qualification import media_reader_container as container
from qualification import media_reader_fixture as fixture
from qualification import media_reader_image as image
from scripts import qualify_media_native_reader as q


def run(coro):
    return asyncio.run(coro)


def test_policy_is_synthetic_and_non_authoritative():
    value = q.policy()
    assert value["schema"] == "zelo-media-native-reader-synthetic-policy-v1"
    assert value["fixture_cases"] == list(fixture.CASES)
    assert value["aggregate_cgroup_required"] is True
    assert value["actual_package_provenance"] is False
    assert value["image_decoder_authority"] is False
    assert value["runtime_authority"] is False


def test_copy_context_uses_only_fixed_retained_inputs(tmp_path, monkeypatch):
    real_root = Path(__file__).resolve().parents[1]
    root = tmp_path / "repo"
    fixtures = root / "qualification/fixtures"
    fixtures.mkdir(parents=True)
    lock_raw = (real_root / "qualification/fixtures/media-reader-elf-target-lock.json").read_bytes()
    lock_path = fixtures / "media-reader-elf-target-lock.json"
    lock_path.write_bytes(lock_raw)
    lock_path.chmod(0o600)

    source_root = root / "runtime/media-package-reader-bootstrap-20261004"
    source_root.mkdir(parents=True, mode=0o700)
    fake_lock = {"files": []}
    expected = {}
    for index in range(8):
        raw = (f"synthetic-retained-input-{index}\n").encode()
        name = f"{index:02d}.binary.reference"
        digest = hashlib.sha256(raw).hexdigest()
        fake_lock["files"].append({"relative": name, "bytes": len(raw), "sha256": digest})
        path = source_root / name
        path.write_bytes(raw)
        path.chmod(0o600)
        expected[name] = raw
    synthetic_manifest = image.manifest(fake_lock)

    monkeypatch.setattr(q, "ROOT", root)
    monkeypatch.setattr(q.image, "manifest", lambda lock: synthetic_manifest)

    attempt = tmp_path / "attempt"
    attempt.mkdir(mode=0o700)
    value, manifest = q._copy_context(attempt, "c" * 64)
    assert value.context == attempt / "build"
    assert len(manifest.files) == 8
    assert set(path.name for path in value.context.iterdir()) == {
        "Dockerfile",
        *(row.source for row in manifest.files),
    }
    dockerfile = value.dockerfile.read_bytes()
    assert dockerfile.startswith(b"FROM scratch\n")
    assert b"RUN " not in dockerfile and b"ADD " not in dockerfile
    assert b"ENTRYPOINT " not in dockerfile and b"CMD " not in dockerfile
    for row in manifest.files:
        raw = (value.context / row.source).read_bytes()
        assert raw == expected[row.source]
        assert len(raw) == row.bytes
        assert hashlib.sha256(raw).hexdigest() == row.sha256


def test_discovery_and_cleanup_remove_only_exact_owned_resources(monkeypatch):
    identity = "c" * 64
    cid = "d" * 64
    iid = "sha256:" + "e" * 64
    state = {"container": True, "image": True, "removed": []}

    def docker(argv, environment, timeout=20):
        assert environment == {"DOCKER_CONFIG": "/private"}
        words = tuple(argv[2:])
        if words[:2] == ("container", "ls"):
            return ((cid + "\n") if state["container"] else "").encode()
        if words[:2] == ("image", "ls"):
            return ((iid + "\n") if state["image"] else "").encode()
        if words[:2] == ("container", "inspect"):
            return json.dumps(
                [
                    {
                        "Id": cid,
                        "Config": {"Labels": {container.LEASE_LABEL: identity}},
                    }
                ]
            ).encode()
        if words[:2] == ("image", "inspect"):
            return json.dumps(
                [
                    {
                        "Id": iid,
                        "Config": {"Labels": {container.LEASE_LABEL: identity}},
                    }
                ]
            ).encode()
        if words[:3] == ("container", "rm", "--force"):
            state["container"] = False
            state["removed"].append(("container", words[-1]))
            return b""
        if words[:2] == ("image", "rm"):
            state["image"] = False
            state["removed"].append(("image", words[-1]))
            return b""
        raise AssertionError(words)

    monkeypatch.setattr(q, "_docker", docker)
    assert q._cleanup_owned(identity, {"DOCKER_CONFIG": "/private"}) is True
    assert state["removed"] == [("container", cid), ("image", iid)]


def test_lost_build_ack_can_still_clean_owned_image(monkeypatch):
    identity = "a" * 64
    iid = "sha256:" + "b" * 64
    state = {"image": True, "removed": False}

    def docker(argv, environment, timeout=20):
        words = tuple(argv[2:])
        if words[:2] == ("container", "ls"):
            return b""
        if words[:2] == ("image", "ls"):
            return ((iid + "\n") if state["image"] else "").encode()
        if words[:2] == ("image", "inspect"):
            return json.dumps(
                [{"Id": iid, "Config": {"Labels": {container.LEASE_LABEL: identity}}}]
            ).encode()
        if words[:2] == ("image", "rm"):
            state["image"] = False
            state["removed"] = True
            return b""
        raise AssertionError(words)

    monkeypatch.setattr(q, "_docker", docker)
    assert q._cleanup_owned(identity, {"DOCKER_CONFIG": "/private"}) is True
    assert state == {"image": False, "removed": True}


def test_cleanup_refuses_foreign_label_without_removal(monkeypatch):
    identity = "a" * 64
    iid = "sha256:" + "b" * 64
    removed = []

    def docker(argv, environment, timeout=20):
        words = tuple(argv[2:])
        if words[:2] == ("container", "ls"):
            return b""
        if words[:2] == ("image", "ls"):
            return (iid + "\n").encode()
        if words[:2] == ("image", "inspect"):
            return json.dumps(
                [{"Id": iid, "Config": {"Labels": {container.LEASE_LABEL: "f" * 64}}}]
            ).encode()
        if words[:2] == ("image", "rm"):
            removed.append(iid)
            return b""
        raise AssertionError(words)

    monkeypatch.setattr(q, "_docker", docker)
    assert q._cleanup_owned(identity, {"DOCKER_CONFIG": "/private"}) is False
    assert removed == []


def test_matrix_requires_positive_cgroup_evidence(monkeypatch, tmp_path):
    lease = SimpleNamespace(identity="c" * 64)
    contract_value = container.ReaderContainerContract("sha256:" + "a" * 64, "b" * 64)
    calls = []

    async def case(lease_arg, contract_arg, attempt, name, probe=None):
        calls.append(name)
        if probe is not None:
            await probe({"State": {"Pid": 43210}})
        return {"case": name, "status": "ok"}

    async def control(lease_arg, contract_arg, attempt, kind):
        return {"case": kind, "status": "controlled-refusal"}

    monkeypatch.setattr(q, "_run_case", case)
    monkeypatch.setattr(q, "_terminal_control", control)
    monkeypatch.setattr(
        q.cgroup,
        "snapshot",
        lambda pid: {
            "schema": "zelo-media-reader-cgroup-v1",
            "aggregate_cgroup_verified": True,
            "runtime_authority": False,
        },
    )
    results, evidence = run(q._matrix(lease, contract_value, tmp_path))
    assert calls == list(fixture.CASES)
    assert len(results) == 8
    assert [row["case"] for row in results[-4:]] == [
        "stdout_limit",
        "timeout",
        "cancel",
        "cancel_repeated",
    ]
    assert evidence["aggregate_cgroup_verified"] is True


def test_matrix_refuses_missing_or_bad_cgroup(monkeypatch, tmp_path):
    lease = SimpleNamespace(identity="c" * 64)
    contract_value = container.ReaderContainerContract("sha256:" + "a" * 64, "b" * 64)

    async def case(lease_arg, contract_arg, attempt, name, probe=None):
        if probe is not None:
            await probe({"State": {"Pid": 43210}})
        return {"case": name}

    async def control(lease_arg, contract_arg, attempt, kind):
        return {"case": kind}

    monkeypatch.setattr(q, "_run_case", case)
    monkeypatch.setattr(q, "_terminal_control", control)
    monkeypatch.setattr(
        q.cgroup,
        "snapshot",
        lambda pid: {"aggregate_cgroup_verified": False},
    )
    with pytest.raises(ValueError, match="reader_native_cgroup_missing"):
        run(q._matrix(lease, contract_value, tmp_path))


def test_read_exact_rejects_hash_drift(tmp_path):
    path = tmp_path / "fixed"
    path.write_bytes(b"abc")
    path.chmod(0o600)
    with pytest.raises(ValueError, match="reader_native_input_hash"):
        q._read_exact(path, 10, digest="0" * 64)


def test_image_manifest_contract_uses_current_bootstrap_proof():
    lock_raw = (q.ROOT / "qualification/fixtures/media-reader-elf-target-lock.json").read_bytes()
    lock = image.load_lock(lock_raw)
    manifest = image.manifest(lock)
    value = image.contract("sha256:" + "a" * 64, manifest)
    assert value.bootstrap_proof_sha256 == container.BOOTSTRAP_PROOF_SHA256
    assert value.bootstrap_lock_sha256 == container.BOOTSTRAP_LOCK_SHA256


def test_no_product_imports():
    names = {
        "media_reader_setup",
        "media_reader_cgroup",
        "qualify_media_native_reader",
    }
    found = []
    for path in Path("app").rglob("*.py"):
        text = path.read_text()
        if any(name in text for name in names):
            found.append(path)
    assert found == []


@pytest.mark.parametrize("fault", ["foreign", "mode", "oversize", "entries"])
def test_old_attempts_are_bounded_and_never_repaired(tmp_path, monkeypatch, fault):
    root = tmp_path / "repo"
    base = root / q.PROOF_BASE
    base.mkdir(parents=True, mode=0o700)
    monkeypatch.setattr(q, "ROOT", root)
    old = base / (q.PREFIX + "old")
    old.mkdir(mode=0o700)
    if fault == "foreign":
        (old / "foreign").write_bytes(b"x")
        (old / "foreign").chmod(0o600)
    elif fault == "mode":
        (old / "policy.json").write_bytes(b"{}")
        (old / "policy.json").chmod(0o644)
    elif fault == "oversize":
        path = old / "policy.json"
        with path.open("wb") as stream:
            stream.truncate(2097153)
        path.chmod(0o600)
    else:
        for name in ("policy.json", "cases.json"):
            path = old / name
            path.write_bytes(b"{}")
            path.chmod(0o600)
        extra = old / "extra"
        extra.write_bytes(b"x")
        extra.chmod(0o600)
    with pytest.raises(ValueError) as refused:
        q._attempt_directory()
    assert str(refused.value) in {"reader_native_old_entry", "media_publisher_path"}
    assert old.exists()


def test_historical_attempt1_refusal_is_preserved_and_counted(tmp_path, monkeypatch):
    real_root = Path(__file__).resolve().parents[1]
    root = tmp_path / "repo"
    base = root / q.PROOF_BASE
    base.mkdir(parents=True, mode=0o700)
    monkeypatch.setattr(q, "ROOT", root)
    monkeypatch.setattr(q.shared, "ROOT", root)
    old = base / q.reconcile.ATTEMPT_NAME
    old.mkdir(mode=0o700)
    config = old / "docker-config"
    config.mkdir(mode=0o700)
    for target, source in (
        ("policy.json", "qualification/fixtures/media-native-reader-attempt1-policy.json"),
        ("cases.json", "qualification/fixtures/media-native-reader-attempt1-cases.json"),
    ):
        path = old / target
        path.write_bytes((real_root / source).read_bytes())
        path.chmod(0o600)
    created = q._attempt_directory()
    assert created.parent == base
    assert created.name.startswith(q.PREFIX)
    assert created.name != old.name
    assert old.exists()
    assert (old / "docker-config").is_dir()
    assert not list((old / "docker-config").iterdir())


@pytest.mark.parametrize(
    "kind,family",
    [
        ("stdout_limit", "output-limit"),
        ("timeout", "timeout"),
        ("cancel", "cancelled"),
        ("cancel_repeated", "cancelled"),
    ],
)
def test_terminal_controls_require_exact_refusal_family(monkeypatch, tmp_path, kind, family):
    class Handle:
        def __init__(
            self,
            lease,
            contract_value,
            attempt,
            profile,
            *,
            runtime_probe=None,
            stdout_limit=fixture.STDOUT_BYTES,
            **kwargs,
        ):
            self.probe = runtime_probe
            self.limit = stdout_limit

        async def stream(self, payload, deadline):
            async with asyncio.timeout_at(deadline):
                if self.probe is not None:
                    await self.probe({"State": {"Pid": 43210}})
                if self.limit < fixture.STDOUT_BYTES:
                    raise q.QualificationError("process_output_limit")
                return SimpleNamespace(stdout=b"", stderr=b"", exit_code=0)

        async def stop_and_confirm_cleanup(self):
            return True

    monkeypatch.setattr(q, "DockerReaderHandle", Handle)
    lease = SimpleNamespace(identity="c" * 64)
    contract_value = container.ReaderContainerContract("sha256:" + "a" * 64, "b" * 64)
    result = run(q._terminal_control(lease, contract_value, tmp_path, kind))
    assert result == {
        "case": kind,
        "status": "controlled-refusal",
        "error_family": family,
        "cleanup_confirmed": True,
        "runtime_authority": False,
    }


def test_stdout_control_refuses_unrelated_exception(monkeypatch, tmp_path):
    class Handle:
        def __init__(self, *args, **kwargs):
            pass

        async def stream(self, payload, deadline):
            raise q.QualificationError("reader_image_refused")

        async def stop_and_confirm_cleanup(self):
            return True

    monkeypatch.setattr(q, "DockerReaderHandle", Handle)
    with pytest.raises(q.QualificationError, match="reader_image_refused"):
        run(
            q._terminal_control(
                SimpleNamespace(identity="c" * 64),
                container.ReaderContainerContract("sha256:" + "a" * 64, "b" * 64),
                tmp_path,
                "stdout_limit",
            )
        )


@pytest.mark.parametrize("kind", ["container", "image"])
def test_lease_preflight_refuses_existing_owned_resource(monkeypatch, kind):
    calls = []

    def discover(which, identity, environment):
        calls.append(which)
        assert which == kind
        return ("d" * 64,) if which == "container" else ("sha256:" + "e" * 64,)

    monkeypatch.setattr(q, "_discover", discover)
    with pytest.raises(ValueError, match="reader_native_lease_collision"):
        q._require_empty_lease(kind, "c" * 64, {"DOCKER_CONFIG": "/private"})
    assert calls == [kind]


@pytest.mark.parametrize("kind", ["container", "image"])
def test_lease_preflight_accepts_empty_namespace(monkeypatch, kind):
    calls = []

    def discover(which, identity, environment):
        calls.append(which)
        return ()

    monkeypatch.setattr(q, "_discover", discover)
    q._require_empty_lease(kind, "c" * 64, {"DOCKER_CONFIG": "/private"})
    assert calls == [kind]


def test_docker_readiness_accepts_linux_amd64_runc(monkeypatch, tmp_path):
    root = tmp_path / "repo"
    runtime = root / "runtime"
    runtime.mkdir(parents=True, mode=0o755)
    monkeypatch.setattr(q, "ROOT", root)
    seen = []

    def docker(argv, environment, timeout=20):
        seen.append((tuple(argv), dict(environment), timeout))
        if "events" in argv:
            return b""
        return json.dumps(
            {
                "Os": "linux",
                "Arch": "amd64",
                "Version": "29.1.3",
                "Components": [
                    {"Name": "Engine", "Version": "29.1.3"},
                    {"Name": "runc", "Version": "1.3.4-0ubuntu1~24.04.1"},
                ],
            }
        ).encode()

    monkeypatch.setattr(q, "_docker", docker)
    value = q._docker_readiness()
    assert value["server_os"] == "linux"
    assert value["server_arch"] == "amd64"
    assert value["server_version"] == "29.1.3"
    assert value["runc_version"].startswith("1.3.4")
    assert value["quiet_window_seconds"] == 2
    assert value["daemon_events_observed"] == 0
    assert value["mutating_command"] is False
    assert value["runtime_authority"] is False
    assert seen[0][0][-3:] == ("version", "--format", "{{json .Server}}")
    assert seen[1][0][2] == "events"
    assert "--filter" not in seen[1][0]
    assert seen[0][1]["HOME"] == "/nonexistent"
    assert not list(runtime.glob("native-reader-readiness-*"))


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("Os", "windows", "reader_native_readiness_identity"),
        ("Arch", "arm64", "reader_native_readiness_identity"),
        ("Version", "", "reader_native_readiness_identity"),
    ],
)
def test_docker_readiness_refuses_wrong_server(monkeypatch, tmp_path, field, value, code):
    root = tmp_path / "repo"
    (root / "runtime").mkdir(parents=True, mode=0o755)
    monkeypatch.setattr(q, "ROOT", root)
    server = {
        "Os": "linux",
        "Arch": "amd64",
        "Version": "29.1.3",
        "Components": [{"Name": "runc", "Version": "1.3.4"}],
    }
    server[field] = value
    monkeypatch.setattr(q, "_docker", lambda *a, **k: json.dumps(server).encode())
    with pytest.raises(ValueError, match=code):
        q._docker_readiness()


def test_docker_readiness_refuses_any_daemon_event_before_admission(monkeypatch, tmp_path):
    root = tmp_path / "repo"
    (root / "runtime").mkdir(parents=True, mode=0o755)
    monkeypatch.setattr(q, "ROOT", root)

    def docker(argv, environment, timeout=20):
        if "events" in argv:
            return b'{"Type":"image","Action":"tag","Actor":{"Attributes":{"project":"other"}}}\n'
        return json.dumps(
            {
                "Os": "linux",
                "Arch": "amd64",
                "Version": "29.1.3",
                "Components": [{"Name": "runc", "Version": "1.3.4"}],
            }
        ).encode()

    class ForbiddenAdmission:
        def __init__(self, *args, **kwargs):
            pytest.fail("admission touched while Docker daemon was active")

    monkeypatch.setattr(q, "_docker", docker)
    monkeypatch.setattr(q, "AdmissionController", ForbiddenAdmission)
    with pytest.raises(ValueError, match="reader_native_daemon_busy"):
        run(q._qualify())
    assert not (root / q.PROOF_BASE).exists()


def test_docker_readiness_failure_precedes_admission(monkeypatch):
    def refused():
        raise ValueError("reader_native_readiness_identity")

    class ForbiddenAdmission:
        def __init__(self, *args, **kwargs):
            pytest.fail("admission touched before readiness")

    monkeypatch.setattr(q, "_docker_readiness", refused)
    monkeypatch.setattr(q, "AdmissionController", ForbiddenAdmission)
    with pytest.raises(ValueError, match="reader_native_readiness_identity"):
        run(q._qualify())


def test_attempt_directory_accepts_only_exact_retained_attempt1_and_allocates_second(
    tmp_path, monkeypatch
):
    real_root = Path(__file__).resolve().parents[1]
    root = tmp_path / "repo"
    base = root / q.PROOF_BASE
    base.mkdir(parents=True, mode=0o700)
    monkeypatch.setattr(q, "ROOT", root)
    monkeypatch.setattr(q.shared, "ROOT", root)

    old = base / q.reconcile.ATTEMPT_NAME
    old.mkdir(mode=0o700)
    for source_name, target_name in (
        (q.reconcile.FIXTURE_POLICY, "policy.json"),
        (q.reconcile.FIXTURE_CASES, "cases.json"),
    ):
        path = old / target_name
        path.write_bytes((real_root / source_name).read_bytes())
        path.chmod(0o600)
    config = old / "docker-config"
    config.mkdir(mode=0o700)

    created = q._attempt_directory()
    assert created.parent == base
    assert created.name.startswith(q.PREFIX)
    assert created.name != q.reconcile.ATTEMPT_NAME
    assert old.exists()
    assert (old / "policy.json").read_bytes() == (
        real_root / q.reconcile.FIXTURE_POLICY
    ).read_bytes()
    assert (old / "cases.json").read_bytes() == (real_root / q.reconcile.FIXTURE_CASES).read_bytes()
    assert config.is_dir() and not list(config.iterdir())

    with pytest.raises(ValueError, match="reader_native_attempt_limit"):
        q._attempt_directory()
