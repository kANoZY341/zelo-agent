"""Pure operator launcher checks; no optional verifier execution or native claims."""

import base64
import dataclasses
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
