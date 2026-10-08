"""Manual REA target qualifier: source/bytes pinned, isolated, never Core authority."""

import json
import os
from pathlib import Path

import pytest

from qualification import rea_static_mcp as fixed
from qualification import rea_target_admission as admission
from qualification import rea_verified_target_static as proof
from scripts import qualify_rea_verified_target as cli
from tests.test_rea_static_mcp import IMAGE_ID, _valid_mcp_result


@pytest.fixture
def admitted(tmp_path, monkeypatch):
    inbox = tmp_path / "inbox"
    snapshots = tmp_path / "snapshots"
    proof_root = tmp_path / "qualification"
    runtime = tmp_path / "runtime"
    for root in (inbox, snapshots, proof_root, runtime):
        root.mkdir(mode=0o700)
    monkeypatch.setattr(admission, "INBOX", inbox)
    monkeypatch.setattr(admission, "STAGING", snapshots)
    monkeypatch.setattr(proof, "PROOF_ROOT", proof_root)
    monkeypatch.setattr(proof, "RUNTIME", runtime)
    source = inbox / "chosen-only"
    source.mkdir(mode=0o700)
    (source / "index.js").write_bytes(b"function triple(a){return a*3}\n")
    (source / "package.json").write_bytes(b'{"name":"approved-private"}\n')
    accepted = admission.admit_target(source.name, operator_opt_in=True)
    yield accepted, snapshots, proof_root, runtime
    # Our readonly snapshots belong only to this test fixture.
    for root, dirs, _ in os.walk(snapshots):
        Path(root).chmod(0o700)
        for name in dirs:
            (Path(root) / name).chmod(0o700)


def test_exact_manual_profile_is_pinned_and_denies_all_core_access():
    p = proof._policy()
    assert p["manual_operator_opt_in_only"] is True
    assert p["network_enabled"] is False
    assert p["target_code_execution_enabled"] is False
    assert p["core_task_registered"] is False
    assert p["core_worker_registered"] is False
    assert p["core_tool_grants"] == []
    assert p["core_approval"] is False
    assert p["maximum_files"] == 128
    assert p["maximum_total_bytes"] == 4 * 1024 * 1024
    assert p["only_fixed_fixture_is_prior_qualified"] is True
    assert p["exclusive_manual_run"] is True
    assert p["refuse_unreconciled_owned_staging"] is True


def test_modified_profile_and_symlink_are_refused(tmp_path, monkeypatch):
    altered = tmp_path / "policy.json"
    altered.write_bytes(proof.OPERATOR_PROFILE.read_bytes() + b" ")
    monkeypatch.setattr(proof, "OPERATOR_PROFILE", altered)
    with pytest.raises(fixed.ReaStaticRefusal, match="rea_target_profile_identity_invalid"):
        proof._policy()
    alias = tmp_path / "link.json"
    alias.symlink_to(altered)
    monkeypatch.setattr(proof, "OPERATOR_PROFILE", alias)
    with pytest.raises(fixed.ReaStaticRefusal, match="rea_target_profile_invalid"):
        proof._policy()


def test_consent_is_required_before_source_access_or_runtime(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("No profile, source, Docker, receipt or network before consent")

    monkeypatch.setattr(proof, "_policy", forbidden)
    monkeypatch.setattr(proof, "_copy_and_verify", forbidden)
    with pytest.raises(fixed.ReaStaticRefusal, match="rea_target_explicit_opt_in_required"):
        proof.analyze_verified_static_target("target-" + "a" * 32, "f" * 64)
    with pytest.raises(fixed.ReaStaticRefusal, match="rea_target_explicit_opt_in_required"):
        proof.analyze_verified_static_target("target-" + "a" * 32, "f" * 64, operator_opt_in=1)


def test_cli_refuses_without_opt_in_and_never_accepts_host_paths(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["rea", "--snapshot-id", "/etc/passwd"])
    assert cli.main() == 2
    assert json.loads(capsys.readouterr().out) == {
        "status": "refused",
        "reason": "explicit_opt_in_required",
    }
    monkeypatch.setattr(
        "sys.argv", ["rea", "--allow-native-rea-target-probe", "--snapshot-id", "/etc/passwd"]
    )
    assert cli.main() == 1
    assert json.loads(capsys.readouterr().out) == {
        "status": "refused",
        "reason": "rea_manual_target_qualification_failed",
    }
    monkeypatch.setattr("sys.argv", ["rea", "--allow-native-rea-target-probe", "--target", "/etc"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2


def test_copied_bytes_are_independently_bound_to_admission_receipt(admitted):
    received, _, proof_root, _ = admitted
    stage = proof_root / "owned-staging"
    stage.mkdir(mode=0o700)
    summary, copied_sha, count, size = proof._copy_and_verify(
        received["snapshot_id"], received["receipt_sha256"], stage
    )
    assert summary == received
    assert len(copied_sha) == 64
    assert count == 2
    assert size == received["total_bytes"]
    for item in (stage / "target").rglob("*"):
        assert item.stat().st_mode & 0o777 == (0o500 if item.is_dir() else 0o400)
    assert (stage / "target").stat().st_mode & 0o777 == 0o500
    assert (stage / "target" / "index.js").read_text() == "function triple(a){return a*3}\n"
    proof._mode_tree(stage / "target", make_readonly=False)
    assert (stage / "target").stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize("mutation", ("wrong_receipt", "modified_file", "unsealed", "symlink"))
def test_changed_snapshot_is_refused_before_copied_target_can_be_used(admitted, mutation):
    result, snapshots, root, _ = admitted
    snapshot = snapshots / result["snapshot_id"]
    if mutation == "wrong_receipt":
        digest = "f" * 64
    else:
        digest = result["receipt_sha256"]
    if mutation == "modified_file":
        path = snapshot / "files" / "index.js"
        path.chmod(0o600)
        path.write_bytes(b"function triple(a){return a*4}\n")
    if mutation == "unsealed":
        (snapshot / "files" / "index.js").chmod(0o600)
    if mutation == "symlink":
        parent = snapshot / "files"
        parent.chmod(0o700)
        path = parent / "index.js"
        path.unlink()
        path.symlink_to("package.json")
        parent.chmod(0o500)
    stage = root / "another"
    stage.mkdir(mode=0o700)
    with pytest.raises((admission.AdmissionRefusal, fixed.ReaStaticRefusal)):
        proof._copy_and_verify(result["snapshot_id"], digest, stage)


def test_same_size_changed_staged_copy_fails_before_runtime(admitted, monkeypatch):
    result, _, root, _ = admitted
    original = proof.fixed.stage_source

    def tamper(source, stage):
        copied = original(source, stage)
        (stage / "target" / "index.js").write_bytes(b"function triple(a){return a*4}\n")
        return copied

    monkeypatch.setattr(proof.fixed, "stage_source", tamper)
    stage = root / "copy-corrupt"
    stage.mkdir(mode=0o700)
    with pytest.raises(fixed.ReaStaticRefusal, match="rea_target_copy_identity_mismatch"):
        proof._copy_and_verify(result["snapshot_id"], result["receipt_sha256"], stage)


def _fake_native(monkeypatch, tmp_path, proof_root, *, cleanup_refused=False):
    docker_bin = tmp_path / "fake-docker"
    docker_bin.write_text("not executable in tests")
    monkeypatch.setattr(fixed, "DOCKER_BIN", docker_bin)
    monkeypatch.setattr(fixed, "trusted_profile", lambda _runtime: {"node_image_id": IMAGE_ID})
    uid, gid = os.geteuid(), os.getegid()
    assert uid > 0 and gid > 0

    class FakeProcess:
        pass

    commands = []

    def fake_popen(argv, **kwargs):
        assert f"--user={uid}:{gid}" in argv
        commands.append(argv)
        assert kwargs["start_new_session"] is True
        assert kwargs["env"]["PATH"] == "/usr/bin:/bin"
        assert kwargs["env"]["LANG"] == "C"
        assert all("SECRET" not in key for key in kwargs["env"])
        stage = Path(kwargs["cwd"])
        target = stage / "target"
        assert target.stat().st_mode & 0o777 == 0o500
        assert (target / "index.js").stat().st_mode & 0o777 == 0o400
        return FakeProcess()

    monkeypatch.setattr(proof.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(proof, "_mcp_static_proof", lambda _process, _deadline: _valid_mcp_result())

    def fake_close(_process, cidfile, _env, _token):
        if cleanup_refused:
            cidfile.write_text("a" * 64)
            raise fixed.ReaStaticRefusal("rea_owned_container_cleanup_unproven")

    monkeypatch.setattr(fixed, "_close_owned_process", fake_close)
    return commands


def test_manual_static_target_is_probed_only_with_exact_isolated_docker_policy(
    admitted, tmp_path, monkeypatch
):
    result, _, proof_root, _ = admitted
    commands = _fake_native(monkeypatch, tmp_path, proof_root)
    report = proof.analyze_verified_static_target(
        result["snapshot_id"], result["receipt_sha256"], operator_opt_in=True
    )
    assert report.schema == "zelo-rea-manual-static-target-proof-v1"
    assert report.snapshot_id == result["snapshot_id"]
    assert report.receipt_sha256 == result["receipt_sha256"]
    assert report.content_sha256 == result["content_sha256"]
    assert report.core_approval is False
    assert report.core_worker_registered is False
    assert report.core_tool_grants == ()
    assert report.core_task_created is False
    assert report.evidence.input_files == result["file_count"]
    assert report.evidence.input_bytes == result["total_bytes"]
    assert report.evidence.provider_id == "rea-javascript-application"
    assert len(commands) == 1
    command = commands[0]
    for required in (
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--pull=never",
        "--memory=384m",
        "--memory-swap=384m",
        "--pids-limit=64",
        "--cpus=1",
    ):
        assert required in command
    assert not any("--privileged" in item or "network=host" in item for item in command)
    assert command[-2:] == ("/rea/scripts/rea.mjs", "mcp")
    assert sum("target=/target,readonly" in item for item in command) == 1
    assert sum("target=/rea,readonly" in item for item in command) == 1
    assert list(proof_root.glob("rea-target-*-*")) == []


def test_cleanup_uncertainty_retains_exact_owned_cid_and_private_bytes(
    admitted, tmp_path, monkeypatch
):
    result, _, proof_root, _ = admitted
    _fake_native(monkeypatch, tmp_path, proof_root, cleanup_refused=True)
    with pytest.raises(fixed.ReaStaticRefusal, match="rea_owned_container_cleanup_unproven"):
        proof.analyze_verified_static_target(
            result["snapshot_id"], result["receipt_sha256"], operator_opt_in=True
        )
    retained = list(proof_root.glob("rea-target-*-*"))
    assert len(retained) == 1
    assert (retained[0] / "owned.cid").read_text() == "a" * 64
    assert (retained[0] / "owner.json").is_file()
    assert (retained[0] / "target" / "index.js").is_file()
    proof._mode_tree(retained[0] / "target", make_readonly=False)


def test_unauthorized_catalog_drift_does_not_launch_target(admitted, tmp_path, monkeypatch):
    result, _, proof_root, _ = admitted
    _fake_native(monkeypatch, tmp_path, proof_root)

    def denial():
        raise fixed.ReaStaticRefusal("rea_target_catalog_authority_changed")

    monkeypatch.setattr(proof, "_require_inert_catalog", denial)
    with pytest.raises(fixed.ReaStaticRefusal, match="rea_target_catalog_authority_changed"):
        proof.analyze_verified_static_target(
            result["snapshot_id"], result["receipt_sha256"], operator_opt_in=True
        )
    assert list(proof_root.glob("rea-target-*-*")) == []


def test_cli_never_leaks_exception_text_or_untrusted_paths(monkeypatch, capsys):
    def raise_sensitive(*_args, **_kwargs):
        raise fixed.ReaStaticRefusal("/private/file/secret.js")

    monkeypatch.setattr(cli, "analyze_verified_static_target", raise_sensitive)
    monkeypatch.setattr(
        "sys.argv",
        [
            "probe",
            "--allow-native-rea-target-probe",
            "--snapshot-id",
            "target-" + "c" * 32,
            "--receipt-sha256",
            "d" * 64,
        ],
    )
    assert cli.main() == 1
    assert json.loads(capsys.readouterr().out) == {
        "status": "refused",
        "reason": "rea_manual_target_qualification_failed",
    }


def test_transport_calls_exact_one_static_tool_no_other_mcp_permissions(monkeypatch):
    requests = []

    class FakeTransport:
        def __init__(self, process, deadline):
            assert process is not None
            assert deadline > 0

        def call(self, message):
            requests.append(message)
            if message.get("id") == 1:
                return {
                    "protocolVersion": fixed.PROTOCOL,
                    "serverInfo": {"name": "rea", "version": "6.0.0"},
                }
            if message.get("id") == 2:
                return _valid_mcp_result()
            return {}

    monkeypatch.setattr(fixed, "_McpTransport", FakeTransport)
    result = proof._mcp_static_proof(object(), 99999.0)
    assert result == _valid_mcp_result()
    assert [m["method"] for m in requests] == [
        "initialize",
        "notifications/initialized",
        "tools/call",
    ]
    assert requests[-1]["params"] == {
        "name": "analyze_javascript_application",
        "arguments": {"input_path": "/target", "format": "directory"},
    }
    assert not any("/etc" in str(m) or "run_native_binary" in str(m) for m in requests)


@pytest.mark.parametrize(
    "bad_init",
    [
        {"protocolVersion": "fake", "serverInfo": {"name": "rea", "version": "6.0.0"}},
        {"protocolVersion": fixed.PROTOCOL, "serverInfo": {"name": "rea", "version": "0"}},
        [],
    ],
)
def test_transport_refuses_wrong_upstream_identity(monkeypatch, bad_init):
    class FakeTransport:
        def __init__(self, process, deadline):
            pass

        def call(self, message):
            if message.get("id") == 1:
                return bad_init
            pytest.fail("Must refuse before invoking a tool with unverified MCP identity")

    monkeypatch.setattr(fixed, "_McpTransport", FakeTransport)
    with pytest.raises(fixed.ReaStaticRefusal, match="rea_mcp_identity_mismatch"):
        proof._mcp_static_proof(object(), 99999.0)


def test_staging_identity_failure_cleans_owned_bytes_before_docker(admitted, monkeypatch, tmp_path):
    result, snapshots, proof_root, _ = admitted
    _fake_native(monkeypatch, tmp_path, proof_root)
    contents = snapshots / result["snapshot_id"] / "files" / "index.js"
    contents.chmod(0o600)
    contents.write_text("function triple(a){return a*9}\n")

    def no_launch(*_args, **_kwargs):
        pytest.fail("Changed target must never reach Docker")

    monkeypatch.setattr(proof.subprocess, "Popen", no_launch)
    with pytest.raises(admission.AdmissionRefusal):
        proof.analyze_verified_static_target(
            result["snapshot_id"], result["receipt_sha256"], operator_opt_in=True
        )
    assert not list(proof_root.glob("rea-target-*-*"))


def test_qualified_probe_will_not_create_an_endpoint_or_core_task(client):
    assert (
        client.post("/integration-candidates/rea/manual-target-probe", json={}).status_code == 404
    )
    assert (
        client.get(
            "/integration-candidates/rea",
            headers={"Authorization": "Bearer test-only-operator-token-not-a-real-credential"},
        ).json()["execution_enabled"]
        is False
    )


def test_runtime_symlink_escape_refuses_before_any_target_or_docker(
    admitted, tmp_path, monkeypatch
):
    result, _, proof_root, _ = admitted
    _fake_native(monkeypatch, tmp_path, proof_root)

    def refuse_unsafe_runtime(_runtime):
        raise fixed.ReaStaticRefusal("rea_runtime_symlink_escape")

    def forbid_launch(*_args, **_kwargs):
        pytest.fail("Unsafe runtime must not start Docker")

    monkeypatch.setattr(fixed, "trusted_profile", refuse_unsafe_runtime)
    monkeypatch.setattr(proof.subprocess, "Popen", forbid_launch)
    with pytest.raises(fixed.ReaStaticRefusal, match="rea_runtime_symlink_escape"):
        proof.analyze_verified_static_target(
            result["snapshot_id"], result["receipt_sha256"], operator_opt_in=True
        )
    assert not list(proof_root.glob("rea-target-*-*"))


def test_trusted_source_runtime_alias_is_canonicalized_before_proof(
    admitted, tmp_path, monkeypatch
):
    """A local alias must resolve before digest checks or Docker mount construction."""

    result, _, proof_root, _ = admitted
    commands = _fake_native(monkeypatch, tmp_path, proof_root)
    physical = tmp_path / "prebuilt-rea-source"
    physical.mkdir(mode=0o700)
    alias = tmp_path / "runtime-v6"
    alias.symlink_to(physical, target_is_directory=True)
    monkeypatch.setattr(proof, "RUNTIME", alias)
    verified = []

    def verify_physical(path):
        verified.append(path)
        assert path == physical.resolve(strict=True)
        assert not path.is_symlink()
        return {"node_image_id": IMAGE_ID}

    monkeypatch.setattr(fixed, "trusted_profile", verify_physical)
    report = proof.analyze_verified_static_target(
        result["snapshot_id"], result["receipt_sha256"], operator_opt_in=True
    )
    assert report.core_worker_registered is False
    assert len(verified) == 1
    assert len(commands) == 1
    assert any(
        f"source={physical.resolve(strict=True)},target=/rea,readonly" in argument
        for argument in commands[0]
    )


def test_exclusive_manual_lock_serializes_local_qualifications(tmp_path, monkeypatch):
    private = tmp_path / "qualification"
    private.mkdir(mode=0o700)
    monkeypatch.setattr(proof, "PROOF_ROOT", private)
    with proof._exclusive_manual_run():
        with pytest.raises(fixed.ReaStaticRefusal, match="rea_target_qualification_busy"):
            with proof._exclusive_manual_run():
                pytest.fail("Second manual REA proof cannot acquire a held lock")
    # The lock is released when the first process exits normally.
    with proof._exclusive_manual_run():
        assert (private / ".rea-target-static.lock").stat().st_mode & 0o777 == 0o600


def test_stale_manual_docker_staging_blocks_new_invocation(tmp_path, monkeypatch):
    private = tmp_path / "qualification"
    private.mkdir(mode=0o700)
    monkeypatch.setattr(proof, "PROOF_ROOT", private)
    retained = private / ("rea-target-" + "a" * 32 + "-unreconciled")
    retained.mkdir(mode=0o700)
    with pytest.raises(fixed.ReaStaticRefusal, match="rea_target_unreconciled_prior_run"):
        with proof._exclusive_manual_run():
            pytest.fail("Retained prior run may still own a container")
    retained.rmdir()
    with proof._exclusive_manual_run():
        pass


def test_symlinked_lock_file_is_never_followed(tmp_path, monkeypatch):
    private = tmp_path / "qualification"
    private.mkdir(mode=0o700)
    monkeypatch.setattr(proof, "PROOF_ROOT", private)
    unrelated = tmp_path / "unrelated"
    unrelated.write_bytes(b"not a REA lock")
    (private / ".rea-target-static.lock").symlink_to(unrelated)
    with pytest.raises(fixed.ReaStaticRefusal, match="rea_target_qualification_lock_unavailable"):
        with proof._exclusive_manual_run():
            pytest.fail("Never follow a replacement lock symlink")
    assert unrelated.read_bytes() == b"not a REA lock"


def test_failed_owned_cleanup_blocks_next_manual_probe(admitted, tmp_path, monkeypatch):
    result, _, proof_root, _ = admitted
    commands = _fake_native(monkeypatch, tmp_path, proof_root, cleanup_refused=True)
    with pytest.raises(fixed.ReaStaticRefusal, match="rea_owned_container_cleanup_unproven"):
        proof.analyze_verified_static_target(
            result["snapshot_id"], result["receipt_sha256"], operator_opt_in=True
        )
    assert len(commands) == 1
    assert len(list(proof_root.glob("rea-target-*-*"))) == 1
    with pytest.raises(fixed.ReaStaticRefusal, match="rea_target_unreconciled_prior_run"):
        proof.analyze_verified_static_target(
            result["snapshot_id"], result["receipt_sha256"], operator_opt_in=True
        )
    assert len(commands) == 1
