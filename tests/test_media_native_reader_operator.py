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


def test_copy_context_uses_only_fixed_retained_inputs(tmp_path):
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
