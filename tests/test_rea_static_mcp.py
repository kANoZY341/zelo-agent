"""Offline regression gates for the unregistered REA static-only MCP qualifier."""

import copy
import json
import os
from pathlib import Path

import pytest

from qualification.rea_static_mcp import (
    MAX_FILE_BYTES,
    PROTOCOL,
    TOOL,
    ReaStaticRefusal,
    _receipt,
    docker_args,
    fingerprint_modules,
    stage_source,
    trusted_profile,
)

IMAGE_ID = "sha256:" + "a" * 64


def _prepared_fixture(tmp_path):
    source = tmp_path / "approved"
    source.mkdir()
    (source / "package.json").write_text('{"main":"index.js"}', encoding="utf-8")
    (source / "index.js").write_text("module.exports = { ok: true };\n", encoding="utf-8")
    return source


def test_static_snapshot_is_deterministic_without_mutating_original(tmp_path):
    source = _prepared_fixture(tmp_path)
    stage_a = tmp_path / "stage-a"
    stage_b = tmp_path / "stage-b"
    stage_a.mkdir()
    stage_b.mkdir()
    digest_a, files_a, bytes_a = stage_source(source, stage_a)
    digest_b, files_b, bytes_b = stage_source(source, stage_b)
    assert digest_a == digest_b
    assert len(digest_a) == 64
    assert files_a == files_b == 2
    assert bytes_a == bytes_b
    assert (stage_a / "target" / "index.js").read_bytes() == (source / "index.js").read_bytes()
    (source / "index.js").write_text("different", encoding="utf-8")
    assert (stage_a / "target" / "index.js").read_text() != "different"


@pytest.mark.parametrize("bad_name", [".env", "secret.pem", "malicious.exe"])
def test_snapshot_rejects_unapproved_file_types(tmp_path, bad_name):
    source = _prepared_fixture(tmp_path)
    (source / bad_name).write_text("secret", encoding="utf-8")
    stage = tmp_path / "stage"
    stage.mkdir()
    with pytest.raises(ReaStaticRefusal, match="rea_source_(name|extension)_not_allowed"):
        stage_source(source, stage)


def test_snapshot_refuses_symlink_and_oversized_input(tmp_path):
    source = _prepared_fixture(tmp_path)
    outside = tmp_path / "outside.js"
    outside.write_text("do not inspect", encoding="utf-8")
    (source / "linked.js").symlink_to(outside)
    stage = tmp_path / "stage"
    stage.mkdir()
    with pytest.raises(ReaStaticRefusal):
        stage_source(source, stage)
    (source / "linked.js").unlink()
    (source / "index.js").write_bytes(b"x" * (MAX_FILE_BYTES + 1))
    stage_2 = tmp_path / "stage-2"
    stage_2.mkdir()
    with pytest.raises(ReaStaticRefusal, match="rea_source_file_limit"):
        stage_source(source, stage_2)


def test_snapshot_refuses_hardlinks_and_source_parent_symlinks(tmp_path):
    source = _prepared_fixture(tmp_path)
    os.link(source / "index.js", source / "dup.js")
    stage = tmp_path / "stage"
    stage.mkdir()
    with pytest.raises(ReaStaticRefusal, match="rea_source_hardlink_forbidden"):
        stage_source(source, stage)
    alias = tmp_path / "alias"
    alias.symlink_to(source, target_is_directory=True)
    with pytest.raises(ReaStaticRefusal, match="rea_source_symlink_ancestor"):
        stage_source(alias, stage)


def test_docker_contract_exactly_denies_network_and_write_authority(tmp_path):
    runtime = tmp_path / "runtime"
    staging = tmp_path / "staging"
    cid = staging / "owned.cid"
    args = docker_args(runtime, staging, cid, IMAGE_ID, uid=1000, gid=1000, owner_token="a" * 32)
    for required in (
        "run",
        "-i",
        "--rm",
        "--pull=never",
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--pids-limit=64",
        "--memory=384m",
        "--memory-swap=384m",
        "--cpus=1",
        "--user=1000:1000",
        "mcp",
    ):
        assert required in args
    assert f"--cidfile={cid}" in args
    assert f"--name=zelo-rea-static-{'a' * 32}" in args
    assert f"--label=zelo.rea.qualification.run={'a' * 32}" in args
    assert any("--mount=type=bind" in arg and "target=/rea,readonly" in arg for arg in args)
    assert any("--mount=type=bind" in arg and "target=/target,readonly" in arg for arg in args)
    assert "--privileged" not in args
    assert "--network=host" not in args
    assert args[-2:] == (
        "/rea/node_modules/rea-agents/scripts/rea.mjs",
        "mcp",
    )
    with pytest.raises(ReaStaticRefusal, match="rea_root_execution_forbidden"):
        docker_args(runtime, staging, cid, IMAGE_ID, uid=0, gid=1000, owner_token="a" * 32)
    with pytest.raises(ReaStaticRefusal, match="rea_unpinned_image"):
        docker_args(runtime, staging, cid, "node:latest", uid=1000, gid=1000, owner_token="a" * 32)


def _valid_mcp_result():
    subject_sha = "a" * 64
    ident = "ev_" + "b" * 64
    body = {
        "input_path": "/target",
        "root_artifact_sha256": subject_sha,
        "statistics": {"parsed_javascript_files": 1, "findings": 2},
    }
    evidence = {
        "evidence_id": ident,
        "operation": TOOL,
        "normalized_result": body,
        "subject": {
            "local_path": "/target",
            "format": "directory",
            "digest": {"sha256": subject_sha},
        },
        "provider": {"id": "rea-javascript-application", "version": "1"},
        "limitations": ["Static evidence cannot prove runtime behavior."],
    }
    return {"structuredContent": {"result": body, "evidence_id": ident, "evidence": evidence}}


def test_receipt_binds_one_fixed_static_tool_and_untrusted_evidence():
    response = _valid_mcp_result()
    receipt = _receipt(response, "c" * 64, 2, 120)
    assert receipt.evidence_id == response["structuredContent"]["evidence_id"]
    assert receipt.source_snapshot_sha256 == "c" * 64
    assert receipt.rea_subject_sha256 == "a" * 64
    assert receipt.provider_id == "rea-javascript-application"
    assert receipt.module_stats == (("findings", 2), ("parsed_javascript_files", 1))
    assert receipt.input_files == 2
    assert receipt.input_bytes == 120
    assert len(receipt.evidence_sha256) == 64


@pytest.mark.parametrize(
    ("mutate", "error"),
    [
        (lambda x: x.update(isError=True), "rea_analysis_failed"),
        (
            lambda x: x["structuredContent"]["evidence"].update(operation="capture_process"),
            "rea_evidence_identity_invalid",
        ),
        (
            lambda x: x["structuredContent"]["evidence"]["subject"].update(local_path="/etc"),
            "rea_evidence_binding_invalid",
        ),
        (
            lambda x: x["structuredContent"]["evidence"]["provider"].update(id="other"),
            "rea_evidence_binding_invalid",
        ),
        (
            lambda x: x["structuredContent"]["result"]["statistics"].update(findings=-1),
            "rea_evidence_bounds_invalid",
        ),
        (
            lambda x: x["structuredContent"]["evidence"].update(limitations=["X" * 513]),
            "rea_evidence_bounds_invalid",
        ),
    ],
)
def test_refuses_malformed_or_untrusted_mcp_results(mutate, error):
    payload = copy.deepcopy(_valid_mcp_result())
    mutate(payload)
    with pytest.raises(ReaStaticRefusal, match=error):
        _receipt(payload, "c" * 64, 2, 120)


def test_installed_modules_fingerprint_rejects_symlink_escape(tmp_path):
    modules = tmp_path / "modules"
    modules.mkdir()
    (modules / "one.js").write_text("one")
    digest = fingerprint_modules(modules)
    assert len(digest) == 64
    outside = tmp_path / "secret.js"
    outside.write_text("secret")
    (modules / "external.js").symlink_to(outside)
    with pytest.raises(ReaStaticRefusal, match="rea_runtime_symlink_escape"):
        fingerprint_modules(modules)


def test_package_identity_has_no_network_or_runtime_fallback(tmp_path):
    with pytest.raises(ReaStaticRefusal, match="rea_runtime_identity_unavailable"):
        trusted_profile(tmp_path)
    manifest = json.loads(
        (
            Path(__file__).resolve().parents[1] / "integrations" / "rea" / "integration.json"
        ).read_text(encoding="utf-8")
    )
    assert manifest["qualification_profile"]["allowable_tool"] == TOOL
    assert manifest["qualification_profile"]["mcp_protocol_version"] == PROTOCOL
    assert manifest["qualification_profile"]["execution_auto_enabled"] is False
    assert manifest["execution_enabled"] is False
    assert manifest["network_access_enabled"] is False


def test_container_mount_paths_refuse_docker_option_injection(tmp_path):
    from qualification.rea_static_mcp import docker_args

    staging = tmp_path / "staging"
    with pytest.raises(ReaStaticRefusal, match="rea_untrusted_paths"):
        docker_args(
            tmp_path / "runtime,bind-propagation=shared",
            staging,
            staging / "owned.cid",
            IMAGE_ID,
            uid=1000,
            gid=1000,
            owner_token="a" * 32,
        )


def test_evidence_subject_malformed_digest_fails_closed():
    response = _valid_mcp_result()
    response["structuredContent"]["evidence"]["subject"]["digest"] = "fake"
    with pytest.raises(ReaStaticRefusal, match="rea_evidence_digest_invalid"):
        _receipt(response, "c" * 64, 2, 120)


def test_cleanup_refuses_when_owned_container_remains(tmp_path, monkeypatch):
    import subprocess

    from qualification import rea_static_mcp as module

    class FinishedProcess:
        stdin = None
        stdout = None
        stderr = None

        def poll(self):
            return 0

    cidfile = tmp_path / "owned.cid"
    cidfile.write_text("f" * 64)
    commands = []

    def fake_run(argv, **_kwargs):
        commands.append(argv)
        if "ps" in argv:
            return subprocess.CompletedProcess(argv, 0, stdout=b"f" * 64 + b"\n")
        return subprocess.CompletedProcess(argv, 0, stdout=b"")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    with pytest.raises(ReaStaticRefusal, match="rea_owned_container_cleanup_unproven"):
        module._close_owned_process(FinishedProcess(), cidfile, {"PATH": "/usr/bin"}, "a" * 32)
    assert len(commands) == 4
    assert commands[0][-3:] == [
        "--no-trunc",
        "--filter",
        "label=zelo.rea.qualification.run=" + "a" * 32,
    ]
    assert commands[1][-3:] == ["rm", "-f", "f" * 64]
    assert commands[2][-3:] == commands[0][-3:]
    assert commands[3][-3:] == ["--no-trunc", "--filter", "id=" + "f" * 64]
    assert cidfile.read_text() == "f" * 64


@pytest.mark.parametrize("case", ["bad-json", "unmatched-id", "oversize", "timeout"])
def test_mcp_transport_rejects_bad_responses(tmp_path, case, monkeypatch):
    import io
    import time

    from qualification import rea_static_mcp as module

    if case == "bad-json":
        response = b"{invalid}\n"
    elif case == "unmatched-id":
        response = b'{"jsonrpc":"2.0","id":99,"result":{}}\n'
    elif case == "oversize":
        monkeypatch.setattr(module, "MAX_RPC_BYTES", 20)
        response = b'{"jsonrpc":"2.0","id":1,"result":{}}\n'
    else:
        response = b""

    read_fd, write_fd = os.pipe()
    try:
        if response:
            os.write(write_fd, response)
        os.close(write_fd)
        write_fd = -1

        class DummyProcess:
            stdin = io.BytesIO()
            stdout = os.fdopen(read_fd, "rb", buffering=0)
            stderr = None

        instance = DummyProcess()
        deadline = time.monotonic() - 1 if case == "timeout" else time.monotonic() + 2
        transport = module._McpTransport(instance, deadline)
        with pytest.raises(ReaStaticRefusal):
            transport.call({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        instance.stdout.close()
        read_fd = -1
    finally:
        if read_fd >= 0:
            os.close(read_fd)
        if write_fd >= 0:
            os.close(write_fd)


def test_rea_pinned_lockfile_matches_canonical_candidate_manifest():
    import hashlib

    from qualification import rea_static_mcp as module

    manifest = json.loads(module.MANIFEST.read_text(encoding="utf-8"))
    lock_bytes = module.LOCK.read_bytes()
    locked = json.loads(lock_bytes)
    assert hashlib.sha256(lock_bytes).hexdigest() == manifest["package"]["lockfile_sha256"]
    assert locked["lockfileVersion"] == 3
    assert locked["packages"]["node_modules/rea-agents"]["version"] == "5.0.0"
    assert (
        locked["packages"]["node_modules/rea-agents"]["integrity"]
        == manifest["package"]["npm_integrity"]
    )


def test_fixture_setup_refuses_symlinked_ancestor_before_writing(tmp_path, monkeypatch):
    from scripts import qualify_rea_static_mcp as script

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace / "fixtures").symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(script, "NATIVE_FIXTURE_ROOT", workspace)
    with pytest.raises(ReaStaticRefusal, match="rea_fixture_path_unavailable"):
        script._prepare_fixture()
    assert not list(outside.iterdir())


def test_fixture_setup_creates_only_exact_bytes_and_never_overwrites(tmp_path, monkeypatch):
    from scripts import qualify_rea_static_mcp as script

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(script, "NATIVE_FIXTURE_ROOT", workspace)
    script._prepare_fixture()
    script._prepare_fixture()  # Identical retry is safe.
    application = workspace / "fixtures" / "tiny-app"
    assert (application / "index.js").read_bytes() == script.INDEX_JS
    (application / "index.js").write_bytes(b"modified")
    with pytest.raises(ReaStaticRefusal, match="rea_fixture_modified"):
        script._prepare_fixture()
    assert (application / "index.js").read_bytes() == b"modified"


@pytest.mark.parametrize("fail_cleanup", [True, False])
def test_stage_retention_is_tied_to_exact_owned_container_cleanup(
    tmp_path, monkeypatch, fail_cleanup
):
    from qualification import rea_static_mcp as module
    from scripts.qualify_rea_static_mcp import INDEX_JS, PACKAGE_JSON

    workspace = tmp_path / "qualification"
    source = workspace / "fixtures" / "tiny-app"
    source.mkdir(parents=True)
    (source / "index.js").write_bytes(INDEX_JS)
    (source / "package.json").write_bytes(PACKAGE_JSON)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    docker_bin = tmp_path / "docker"
    docker_bin.write_text("fake")
    monkeypatch.setattr(module, "NATIVE_FIXTURE_ROOT", workspace)
    monkeypatch.setattr(module, "NATIVE_RUNTIME", runtime)
    monkeypatch.setattr(module, "DOCKER_BIN", docker_bin)
    monkeypatch.setattr(module, "trusted_profile", lambda _runtime: {"node_image_id": IMAGE_ID})
    monkeypatch.setattr(module.subprocess, "Popen", lambda *_args, **_kwargs: object())

    class FakeTransport:
        def __init__(self, _process, _deadline):
            pass

        def call(self, message):
            if "id" not in message:
                return {}
            if message["id"] == 1:
                return {
                    "protocolVersion": PROTOCOL,
                    "serverInfo": {"name": "rea", "version": "5.0.0"},
                }
            return _valid_mcp_result()

    monkeypatch.setattr(module, "_McpTransport", FakeTransport)

    def fake_cleanup(_process, cidfile, _env, owner_token):
        assert len(owner_token) == 32
        cidfile.write_text("f" * 64)
        if fail_cleanup:
            raise ReaStaticRefusal("rea_owned_container_cleanup_unproven")
        cidfile.unlink()

    monkeypatch.setattr(module, "_close_owned_process", fake_cleanup)
    if fail_cleanup:
        with pytest.raises(ReaStaticRefusal, match="rea_owned_container_cleanup_unproven"):
            module.analyze_static_fixture()
        retained = list(workspace.glob("rea-mcp-*"))
        assert len(retained) == 1
        assert (retained[0] / "owned.cid").read_text() == "f" * 64
        assert (retained[0] / "target" / "index.js").is_file()
    else:
        receipt = module.analyze_static_fixture()
        assert receipt.input_files == 2
        assert not list(workspace.glob("rea-mcp-*"))


def test_missing_cid_reconciles_owner_label_and_fails_closed(tmp_path, monkeypatch):
    import subprocess

    from qualification import rea_static_mcp as module

    class FinishedProcess:
        stdin = None
        stdout = None
        stderr = None

        def poll(self):
            return 0

    cidfile = tmp_path / "owned.cid"
    calls = []

    def fake_run(argv, **_kwargs):
        calls.append(argv)
        if "ps" in argv:
            return subprocess.CompletedProcess(argv, 0, stdout=b"")
        raise AssertionError("No container may be removed without label proof")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    with pytest.raises(ReaStaticRefusal, match="rea_owned_container_cleanup_unproven"):
        module._close_owned_process(FinishedProcess(), cidfile, {"PATH": "/usr/bin"}, "b" * 32)
    assert len(calls) == 1
    assert calls[0][-2:] == ["--filter", "label=zelo.rea.qualification.run=" + "b" * 32]


def test_missing_cid_reconciles_and_removes_only_labelled_container(tmp_path, monkeypatch):
    import subprocess

    from qualification import rea_static_mcp as module

    class FinishedProcess:
        stdin = None
        stdout = None
        stderr = None

        def poll(self):
            return 0

    cidfile = tmp_path / "owned.cid"
    calls = []
    state = {"present": True}

    def fake_run(argv, **_kwargs):
        calls.append(argv)
        if "ps" in argv:
            return subprocess.CompletedProcess(
                argv, 0, stdout=(b"e" * 64 + b"\n") if state["present"] else b""
            )
        if "rm" in argv:
            assert argv[-1] == "e" * 64
            state["present"] = False
            return subprocess.CompletedProcess(argv, 0, stdout=b"")
        raise AssertionError("Unexpected Docker action")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    with pytest.raises(ReaStaticRefusal, match="rea_owned_container_cleanup_unproven"):
        module._close_owned_process(FinishedProcess(), cidfile, {"PATH": "/usr/bin"}, "a" * 32)
    assert len(calls) == 3
    assert "ps" in calls[0]
    assert calls[0][-1] == "label=zelo.rea.qualification.run=" + "a" * 32
    assert calls[1][-3:] == ["rm", "-f", "e" * 64]
    assert "ps" in calls[2]
    assert state["present"] is False


def test_mismatched_cid_and_owner_label_never_remove_other_container(tmp_path, monkeypatch):
    import subprocess

    from qualification import rea_static_mcp as module

    class FinishedProcess:
        stdin = None
        stdout = None
        stderr = None

        def poll(self):
            return 0

    cidfile = tmp_path / "owned.cid"
    cidfile.write_text("f" * 64)
    calls = []

    def fake_run(argv, **_kwargs):
        calls.append(argv)
        if "ps" in argv:
            if "label=" in argv[-1]:
                return subprocess.CompletedProcess(argv, 0, stdout=b"e" * 64 + b"\n")
            return subprocess.CompletedProcess(argv, 0, stdout=b"f" * 64 + b"\n")
        raise AssertionError("Must not remove a CID that is not proven by label")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    with pytest.raises(ReaStaticRefusal, match="rea_owned_container_cleanup_unproven"):
        module._close_owned_process(FinishedProcess(), cidfile, {"PATH": "/usr/bin"}, "d" * 32)
    assert len(calls) == 3
    assert all("rm" not in cmd for cmd in calls)
    assert cidfile.read_text() == "f" * 64


def test_docker_refuses_model_provided_or_invalid_owner_tag(tmp_path):
    staging = tmp_path / "staging"
    with pytest.raises(ReaStaticRefusal, match="rea_owner_identity_invalid"):
        docker_args(
            tmp_path / "runtime",
            staging,
            staging / "owned.cid",
            IMAGE_ID,
            uid=1000,
            gid=1000,
            owner_token="a" * 32 + ",privileged=true",
        )


def test_docker_reconciliation_fails_closed_for_daemon_outage(monkeypatch):
    import subprocess

    from qualification import rea_static_mcp as module

    def fake_run(argv, **_kwargs):
        return subprocess.CompletedProcess(argv, 1, stdout=b"", stderr=b"")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    with pytest.raises(ReaStaticRefusal, match="rea_docker_reconciliation_failed"):
        module._list_docker_containers("label=zelo.rea.qualification.run=" + "c" * 32, {})


def test_docker_reconciliation_fails_closed_on_multiple_ids(monkeypatch):
    import subprocess

    from qualification import rea_static_mcp as module

    def fake_run(argv, **_kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout=b"a" * 64 + b"\n" + b"b" * 64 + b"\n")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    with pytest.raises(ReaStaticRefusal, match="rea_docker_reconciliation_invalid"):
        module._list_docker_containers("label=zelo.rea.qualification.run=" + "c" * 32, {})
