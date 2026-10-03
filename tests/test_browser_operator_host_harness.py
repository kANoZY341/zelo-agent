"""Offline safety checks: no external network, Docker or host preparation."""

import asyncio
import json
import stat
import sys
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from scripts import qualify_browser_operator as proof


@pytest.fixture(autouse=True)
def no_git_subprocess(monkeypatch):
    monkeypatch.setattr(proof, "revision", lambda: {"git_commit": "a" * 40, "worktree_clean": True})


def test_opt_in_precedes_all_host_actions(monkeypatch):
    monkeypatch.setattr(
        sys, "argv", ["proof", "--bundle", "/unused", "--expected-bundle-digest", "0" * 64]
    )

    def forbidden(*args, **kwargs):
        pytest.fail("host action reached without explicit opt-in")

    for name in ("prepare", "command", "cleanup_owned_setup", "qualify"):
        monkeypatch.setattr(proof, name, forbidden)
    with pytest.raises(SystemExit) as error:
        proof.main()
    assert error.value.code == 2


def test_settings_ignore_live_environment_and_dotenv(runtime_path, monkeypatch):
    monkeypatch.setenv("ZELO_RUNTIME_DIR", "/live/core")
    monkeypatch.setenv("ZELO_OPERATOR_TOKEN", "live-secret")
    monkeypatch.setenv("ZELO_BROWSER_WORKER_ENDPOINTS", '["evil=https://evil.example"]')
    monkeypatch.setenv("ZELO_PROMOTION_REPOSITORY", "live/repository")
    dotenv = runtime_path / ".env"
    dotenv.write_text("ZELO_OPERATOR_TOKEN=dotenv-secret\nZELO_RUNTIME_DIR=/live/dotenv\n")
    settings = proof.diagnostic_settings(
        runtime_path, runtime_path / "prepared/verified-receipt.json"
    )
    assert settings.runtime_dir == runtime_path / "core"
    assert settings.browser_worker_endpoints == ("example_document=https://example.com",)
    assert settings.operator_token.get_secret_value() == "diagnostic-placeholder-not-a-credential"
    assert settings.promotion_repository is None
    overridden = proof.QualificationSettings(_env_file=dotenv)
    assert overridden.operator_token is None


def test_private_exclusive_bounded_report(tmp_path):
    report = {"status": "failed", "text": "public evidence"}
    path = proof.save_report(tmp_path, report)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_bytes()) == report
    with pytest.raises(FileExistsError):
        proof.save_report(tmp_path, {"status": "passed"})
    assert json.loads(path.read_bytes()) == report
    path.unlink()
    with pytest.raises(ValueError, match="operator_report_limit"):
        proof.save_report(tmp_path, {"text": "x" * proof.REPORT_LIMIT})
    assert not path.exists()
    target = tmp_path / "target"
    target.write_text("unchanged")
    path.symlink_to(target)
    with pytest.raises(FileExistsError):
        proof.save_report(tmp_path, report)
    assert target.read_text() == "unchanged"


def result_task():
    return SimpleNamespace(
        status="succeeded",
        result={
            "endpoint_id": proof.ENDPOINT_ID,
            "text": "Example Domain",
            "truncated": False,
            "nodes": 1,
            "source_origin": proof.ORIGIN,
            "source_path": proof.DOCUMENT_PATH,
            "source_digest": "a" * 64,
            "contract_digest": "b" * 64,
            "broker_policy_digest": "c" * 64,
            "instruction_trust": "none",
            "completed_at": datetime.now(UTC).isoformat(),
        },
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("endpoint_id", "other"),
        ("source_origin", "https://other.example"),
        ("source_path", "/other"),
        ("contract_digest", "d" * 64),
        ("broker_policy_digest", "d" * 64),
        ("text", " "),
        ("nodes", 0),
        ("instruction_trust", "trusted"),
        ("source_digest", "bad"),
    ],
)
def test_evidence_rejects_mismatch(field, value):
    task = result_task()
    task.result[field] = value
    contract = SimpleNamespace(digest="b" * 64, execution=SimpleNamespace(broker_digest="c" * 64))
    with pytest.raises(
        ValueError if field in ("instruction_trust", "source_digest") else RuntimeError
    ):
        proof.validate_result(task, contract)


def test_evidence_accepts_truthful_truncation():
    task = result_task()
    task.result["truncated"] = True
    contract = SimpleNamespace(digest="b" * 64, execution=SimpleNamespace(broker_digest="c" * 64))
    assert proof.validate_result(task, contract)["truncated"] is True
    task.status = "failed"
    with pytest.raises(RuntimeError, match="operator_task_failed"):
        proof.validate_result(task, contract)


@pytest.mark.parametrize("confirmed", [False, True])
def test_failure_cleanup_preserves_unknown_runtime_image(runtime_path, monkeypatch, confirmed):
    owner, image = "a" * 32, "sha256:" + "b" * 64
    cleaned = []

    def prepare(source, expected, destination, endpoints):
        assert endpoints == proof.ENDPOINTS
        destination.mkdir()
        receipt = destination / "verified-receipt.json"
        receipt.write_text(json.dumps({"contract": {"execution": {"image_identity": image}}}))
        return receipt

    async def exercise(settings, report):
        report["runtime_cleanup_confirmed"] = confirmed
        raise RuntimeError("private failure details must never enter report")

    monkeypatch.setattr(proof, "prepare", prepare)
    monkeypatch.setattr(
        proof,
        "command",
        lambda args: json.dumps([{"Id": image, "Config": {"Labels": {proof.LABEL: owner}}}]),
    )
    monkeypatch.setattr(proof, "exercise", exercise)
    monkeypatch.setattr(
        proof, "cleanup_owned_setup", lambda *args, **kw: cleaned.append((args, kw)) or []
    )
    path, passed = proof.qualify(runtime_path, "0" * 64, runtime_path / "proofs")
    report = json.loads(path.read_bytes())
    assert not passed and report["status"] == ("failed" if confirmed else "cleanup_unproven")
    assert report["failure_type"] == "RuntimeError"
    assert "private failure details" not in path.read_text()
    assert cleaned == [
        ((proof.DOCKER, owner), {"preserve_verified_image": None if confirmed else image})
    ]


def test_untrusted_owner_never_selected_for_cleanup(tmp_path, monkeypatch):
    def prepare(source, expected, destination, endpoints):
        destination.mkdir()
        receipt = destination / "verified-receipt.json"
        receipt.write_text(
            json.dumps({"contract": {"execution": {"image_identity": "sha256:" + "b" * 64}}})
        )
        return receipt

    monkeypatch.setattr(proof, "prepare", prepare)
    monkeypatch.setattr(
        proof,
        "command",
        lambda args: json.dumps([{"Id": "wrong", "Config": {"Labels": {proof.LABEL: "a" * 32}}}]),
    )
    monkeypatch.setattr(
        proof, "cleanup_owned_setup", lambda *a, **kw: pytest.fail("unbound owner cleanup")
    )
    path, passed = proof.qualify(tmp_path, "0" * 64, tmp_path / "proofs")
    assert not passed and not json.loads(path.read_bytes())["external_network_attempted"]


@pytest.mark.parametrize("close_failure", [False, True])
def test_core_failure_closes_runtime_and_database(runtime_path, monkeypatch, close_failure):
    closed = []
    runtime = SimpleNamespace(
        contract=SimpleNamespace(
            digest="b" * 64, execution=SimpleNamespace(image_identity="sha256:" + "c" * 64)
        ),
        admission_directory=runtime_path,
    )

    async def close():
        closed.append("runtime")
        if close_failure:
            raise RuntimeError("cleanup unproven")

    runtime.aclose = close
    monkeypatch.setattr(proof, "configured_browser_runtime", lambda settings: runtime)

    class Database:
        def __init__(self, path):
            pass

        def migrate(self):
            raise RuntimeError("migration failure")

        def close(self):
            closed.append("database")

    monkeypatch.setattr(proof, "Database", Database)
    report = {}
    settings = proof.diagnostic_settings(
        runtime_path, runtime_path / "prepared/verified-receipt.json"
    )
    with pytest.raises(RuntimeError):
        asyncio.run(proof.exercise(settings, report))
    assert closed == ["runtime", "database"]
    assert report["runtime_cleanup_confirmed"] is (not close_failure)


def test_fence_is_retained(tmp_path):
    marker = tmp_path / "active.marker"
    assert proof.admission_clear(tmp_path)
    marker.write_text("unknown owner")
    assert not proof.admission_clear(tmp_path)
    assert marker.read_text() == "unknown owner"


@pytest.mark.parametrize("cleanup_error", [None, "returned", "raised"])
def test_success_requires_reinspection_and_setup_cleanup(runtime_path, monkeypatch, cleanup_error):
    owner, image, digest = "a" * 32, "sha256:" + "b" * 64, "c" * 64
    events = []

    def prepare(source, expected, destination, endpoints):
        events.append("prepare")
        destination.mkdir()
        receipt = destination / "verified-receipt.json"
        receipt.write_text(
            json.dumps(
                {
                    "contract": {"execution": {"image_identity": image}},
                    "image_payload_digest": digest,
                }
            )
        )
        return receipt

    def command(args):
        if args[1:3] == ["image", "inspect"]:
            return json.dumps([{"Id": image, "Config": {"Labels": {proof.LABEL: owner}}}])
        assert args[1:3] == ["ps", "-a"]
        events.append("no_containers")
        return ""

    async def exercise(settings, report):
        events.append("exercise")
        report["runtime_cleanup_confirmed"] = True
        return SimpleNamespace()

    def reinspect(*args):
        events.append("reinspect")
        return digest

    def cleanup(*args, **kwargs):
        events.append("cleanup")
        assert args == (proof.DOCKER, owner) and kwargs == {"preserve_verified_image": None}
        if cleanup_error == "raised":
            raise RuntimeError("do not echo command output")
        return ["image_cleanup_unproven:RuntimeError"] if cleanup_error else []

    for name, value in (
        ("prepare", prepare),
        ("command", command),
        ("exercise", exercise),
        ("reinspect_image", reinspect),
        ("cleanup_owned_setup", cleanup),
    ):
        monkeypatch.setattr(proof, name, value)
    path, passed = proof.qualify(runtime_path, "0" * 64, runtime_path / "proofs")
    report = json.loads(path.read_bytes())
    assert events == ["prepare", "exercise", "no_containers", "reinspect", "cleanup"]
    assert passed is (cleanup_error is None)
    assert report["status"] == ("passed" if cleanup_error is None else "cleanup_unproven")
    assert "do not echo command output" not in path.read_text()


def test_preparation_failure_does_not_start_network_or_select_unknown_owner(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("preparation failed")

    monkeypatch.setattr(proof, "prepare", fail)
    for name in ("command", "cleanup_owned_setup", "exercise"):
        monkeypatch.setattr(
            proof, name, lambda *a, **kw: pytest.fail("post-preparation action reached")
        )
    path, passed = proof.qualify(tmp_path, "0" * 64, tmp_path / "proofs")
    report = json.loads(path.read_bytes())
    assert not passed and not report["external_network_attempted"]
    assert not report["runtime_cleanup_confirmed"]
