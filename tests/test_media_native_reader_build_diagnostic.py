"""Offline end-to-end tests for persistent build-only diagnostic receipts."""

import base64
import hashlib
import json
import stat

from qualification import media_reader_image as image
from qualification import media_reader_setup as setup
from scripts import diagnose_media_native_reader_build as diagnostic
from scripts import qualify_media_native_reader as reader


def test_fake_build_receipt_survives_context_cleanup_and_redacts_bounded_output(
    tmp_path, monkeypatch
):
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o700)
    receipt_dir = runtime / diagnostic.RECEIPT_DIRECTORY
    call_log = tmp_path / "fake-docker-calls.jsonl"
    fake_docker = tmp_path / "fake-docker"

    middle = (
        b"MIDDLE password=private-password token=private-token /home/zelo/private/recipe.json \xff"
    )
    fixed = len(b"START\n") + 3 * len(b"\n") + len(middle) + len(b"END\n")
    filler = 30_000 - fixed
    payload = (
        b"START\n"
        + b"A" * (filler // 2)
        + b"\n"
        + middle
        + b"\n"
        + b"B" * (filler - filler // 2)
        + b"\nEND\n"
    )
    assert len(payload) == 30_000
    encoded = base64.b64encode(payload).decode("ascii")
    fake_docker.write_text(
        "#!/usr/bin/env python3\n"
        "import base64, json, sys\n"
        f"with open({str(call_log)!r}, 'a', encoding='utf-8') as log:\n"
        "    log.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "args = sys.argv[1:]\n"
        "if len(args) > 1 and args[1] == 'build':\n"
        f"    sys.stderr.buffer.write(base64.b64decode({encoded!r}))\n"
        "    raise SystemExit(2)\n"
        "raise SystemExit(0)\n",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)
    monkeypatch.setattr(setup, "DOCKER", str(fake_docker))

    contexts = []

    def fake_copy_context(attempt, lease_identity, runtime_root):
        manifest = image.ImageManifest((), "a" * 64)
        plan = setup.plan(attempt.resolve(), lease_identity, manifest)
        plan.context.mkdir(mode=0o700)
        plan.dockerfile.write_bytes(b"FROM scratch\n")
        plan.dockerfile.chmod(0o600)
        contexts.append(attempt)
        return plan, manifest

    monkeypatch.setattr(reader, "_copy_context", fake_copy_context)

    receipt_path, receipt = diagnostic.run_build_diagnostic(runtime)

    assert receipt_path.is_file()
    assert receipt_path.parent == receipt_dir
    assert stat.S_IMODE(receipt_path.stat().st_mode) == 0o600
    assert json.loads(receipt_path.read_text(encoding="utf-8")) == receipt
    assert receipt["schema"] == diagnostic.SCHEMA
    assert receipt["build_status"] == "failed"
    assert receipt["build_invocations"] == 1
    assert receipt["build_timeout_seconds"] == 90
    assert receipt["network"] == "none"
    assert receipt["pull"] is False
    assert receipt["proof_entrypoint_called"] is False
    assert receipt["proof_ledger_touched"] is False
    assert receipt["cleanup_confirmed"] is True
    assert receipt["cleanup_errors"] == []
    assert receipt["temporary_context_removed"] is True
    assert contexts and all(not context.exists() for context in contexts)
    assert not receipt_path.is_relative_to(contexts[0])

    failure = receipt["build_failure"]
    assert failure["kind"] == "docker_command_exit"
    assert failure["operation"] == "build"
    assert failure["exit_code"] == 2
    assert failure["output_category"] == "unclassified"
    assert failure["stderr_bytes"] == 30_000
    assert failure["stderr_sha256"] == hashlib.sha256(payload).hexdigest()
    excerpt = failure["error_excerpt"]
    assert len(excerpt) <= 512
    assert receipt_path.stat().st_size <= diagnostic.MAX_RECEIPT_BYTES
    assert "START" in excerpt
    assert "MIDDLE" in excerpt
    assert "END" in excerpt
    assert "password=<redacted>" in excerpt
    assert "token=<redacted>" in excerpt
    assert "<path>" in excerpt
    assert "?" in excerpt
    serialized = receipt_path.read_text(encoding="utf-8")
    for secret in ("private-password", "private-token", "/home/zelo/private/recipe.json"):
        assert secret not in serialized
    assert "\ufffd" not in excerpt

    calls = [json.loads(line) for line in call_log.read_text(encoding="utf-8").splitlines()]
    builds = [args for args in calls if len(args) > 1 and args[1] == "build"]
    assert len(builds) == 1
    assert "--network=none" in builds[0]
    assert "--pull=false" in builds[0]
    assert not (runtime / "media-native-reader").exists()


def test_spawn_failure_persists_safe_receipt_and_removes_context(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o700)
    missing_docker = tmp_path / "docker-does-not-exist"
    monkeypatch.setattr(setup, "DOCKER", str(missing_docker))

    def fake_copy_context(attempt, lease_identity, runtime_root):
        manifest = image.ImageManifest((), "b" * 64)
        plan = setup.plan(attempt.resolve(), lease_identity, manifest)
        plan.context.mkdir(mode=0o700)
        plan.dockerfile.write_bytes(b"FROM scratch\n")
        plan.dockerfile.chmod(0o600)
        return plan, manifest

    monkeypatch.setattr(reader, "_copy_context", fake_copy_context)
    monkeypatch.setattr(reader, "_cleanup_owned", lambda identity, environment: True)

    receipt_path, receipt = diagnostic.run_build_diagnostic(runtime)

    assert receipt_path.is_file()
    assert receipt["build_invocations"] == 1
    assert receipt["build_failure"] == {"kind": "file_not_found"}
    assert receipt["cleanup_confirmed"] is True
    assert receipt["temporary_context_removed"] is True
    assert not list(runtime.glob("native-reader-diagnostic-*"))


def test_timeout_and_cleanup_failure_keep_partial_redacted_output(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o700)
    fake_docker = tmp_path / "fake-docker"
    output = b"TIMEOUT password=timeout-secret /home/zelo/timeout/private.json \xff\n"
    fake_docker.write_text(
        "#!/usr/bin/env python3\n"
        "import sys, time\n"
        "args = sys.argv[1:]\n"
        "if len(args) > 1 and args[1] == 'build':\n"
        f"    sys.stderr.buffer.write({output!r})\n"
        "    sys.stderr.flush()\n"
        "    time.sleep(5)\n"
        "raise SystemExit(0)\n",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)
    monkeypatch.setattr(setup, "DOCKER", str(fake_docker))
    monkeypatch.setattr(diagnostic, "BUILD_TIMEOUT_SECONDS", 0.5)

    def fake_copy_context(attempt, lease_identity, runtime_root):
        manifest = image.ImageManifest((), "c" * 64)
        plan = setup.plan(attempt.resolve(), lease_identity, manifest)
        plan.context.mkdir(mode=0o700)
        plan.dockerfile.write_bytes(b"FROM scratch\n")
        plan.dockerfile.chmod(0o600)
        return plan, manifest

    cleanup_calls = []
    monkeypatch.setattr(reader, "_copy_context", fake_copy_context)
    monkeypatch.setattr(
        reader,
        "_cleanup_owned",
        lambda identity, environment: cleanup_calls.append(identity) or False,
    )

    receipt_path, receipt = diagnostic.run_build_diagnostic(runtime)

    assert receipt_path.is_file()
    assert len(cleanup_calls) == 1
    assert receipt["build_failure"]["kind"] == "timeout"
    assert receipt["build_failure"]["stderr_bytes"] == len(output)
    excerpt = receipt["build_failure"]["error_excerpt"]
    assert "TIMEOUT" in excerpt
    assert "password=<redacted>" in excerpt
    assert "<path>" in excerpt
    assert "?" in excerpt
    assert "timeout-secret" not in receipt_path.read_text(encoding="utf-8")
    assert "/home/zelo/timeout/private.json" not in excerpt
    assert receipt["cleanup_confirmed"] is False
    assert receipt["cleanup_errors"] == ["owned_resource_cleanup_unconfirmed"]
    assert receipt["temporary_context_removed"] is True
