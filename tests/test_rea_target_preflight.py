"""Offline target preflight binds inert metadata without crossing into Core."""

import json
import os
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from qualification import rea_target_admission as admission
from qualification import rea_target_preflight as preflight
from qualification.rea_source_vendor import ReaVendorRefusal
from qualification.rea_target_preflight import ReaPreflightInput, ReaTargetPreflight
from scripts import preflight_rea_target as cli
from tests.conftest import AUTH

# The live vendor inventory is shared with mutation tests in test_rea_source_vendor.
pytestmark = pytest.mark.xdist_group("rea_vendor_inventory")


@pytest.fixture
def admitted_target(tmp_path, monkeypatch):
    inbox = tmp_path / "inbox"
    staging = tmp_path / "staging"
    inbox.mkdir(mode=0o700)
    staging.mkdir(mode=0o700)
    monkeypatch.setattr(admission, "INBOX", inbox)
    monkeypatch.setattr(admission, "STAGING", staging)
    target = inbox / "review-target"
    target.mkdir(mode=0o700)
    (target / "index.js").write_text("export const n = 1;\n", encoding="utf-8")
    (target / "package.json").write_text('{"name":"review"}\n', encoding="utf-8")
    receipt = admission.admit_target("review-target", operator_opt_in=True)
    yield receipt, staging
    for base, dirs, files in os.walk(staging, topdown=False):
        os.chmod(base, 0o700)
        for name in files:
            (Path(base) / name).chmod(0o600)
        for name in dirs:
            (Path(base) / name).chmod(0o700)


def request_for(receipt: dict) -> ReaPreflightInput:
    return ReaPreflightInput(
        snapshot_id=receipt["snapshot_id"], receipt_sha256=receipt["receipt_sha256"]
    )


def run_cli(monkeypatch, capsys, *arguments: str) -> tuple[int, dict]:
    monkeypatch.setattr(sys, "argv", ["preflight_rea_target.py", *arguments])
    status = cli.main()
    return status, json.loads(capsys.readouterr().out)


def test_verified_snapshot_binds_source_catalog_but_never_execution(admitted_target):
    receipt, _ = admitted_target
    request = request_for(receipt)
    with pytest.raises(PermissionError, match="rea_preflight_explicit_review_required"):
        preflight.prepare_rea_target_preflight(request)
    result = preflight.prepare_rea_target_preflight(request, operator_opt_in=True)
    payload = result.model_dump(mode="json")
    assert payload["schema_version"] == preflight.SCHEMA
    assert payload["snapshot_id"] == receipt["snapshot_id"]
    assert payload["receipt_sha256"] == receipt["receipt_sha256"]
    assert payload["content_sha256"] == receipt["content_sha256"]
    assert payload["file_count"] == receipt["file_count"] == 2
    assert payload["total_bytes"] == receipt["total_bytes"]
    assert payload["tool_name"] == "analyze_javascript_application"
    assert payload["tool_qualification"] == "fixed_fixture_only_not_target_qualified"
    assert payload["source_commit"] == preflight.EXPECTED_COMMIT
    assert payload["source_manifest_sha256"] == preflight.EXPECTED_MANIFEST_SHA256
    assert payload["tool_catalog_sha256"] == preflight.EXPECTED_CATALOG_SHA256
    assert payload["manual_operator_opt_in"] is True
    for key in ("execution_enabled", "core_approval", "runtime_qualified", "worker_registered"):
        assert payload[key] is False
    assert payload["tool_grants"] == []
    assert len(payload["review_fingerprint"]) == 64
    assert result == preflight.prepare_rea_target_preflight(request, operator_opt_in=True)
    assert (
        ReaTargetPreflight.model_validate_json(json.dumps(payload), strict=True).review_fingerprint
        == payload["review_fingerprint"]
    )


def test_cli_needs_opt_in_and_allows_no_paths_or_tool_override(
    admitted_target, monkeypatch, capsys
):
    receipt, _ = admitted_target
    args = ("--snapshot-id", receipt["snapshot_id"], "--receipt-sha256", receipt["receipt_sha256"])
    status, payload = run_cli(monkeypatch, capsys, *args)
    assert status == 2
    assert payload == {"status": "refused", "reason": "explicit_opt_in_required"}
    status, payload = run_cli(monkeypatch, capsys, "--allow-offline-rea-preflight", *args)
    assert status == 0
    assert payload["snapshot_id"] == receipt["snapshot_id"]
    assert payload["execution_enabled"] is False
    assert payload["worker_registered"] is False
    assert payload["tool_grants"] == []
    assert "path" not in payload
    assert "source_files" not in payload
    with pytest.raises(SystemExit) as error:
        run_cli(monkeypatch, capsys, "--allow-offline-rea-preflight", *args, "--tool", "run_native")
    assert error.value.code == 2


def test_untrusted_input_never_selects_host_paths(client, admitted_target, monkeypatch, capsys):
    receipt, _ = admitted_target
    for invalid in (
        {"snapshot_id": "../../etc/passwd", "receipt_sha256": receipt["receipt_sha256"]},
        {"snapshot_id": receipt["snapshot_id"], "receipt_sha256": "0" * 64, "tool": "other"},
    ):
        with pytest.raises(ValidationError):
            ReaPreflightInput.model_validate(invalid, strict=True)
    assert (
        client.post(
            "/integration-candidates/rea/preflight",
            json={"snapshot_id": receipt["snapshot_id"]},
            headers=AUTH,
        ).status_code
        == 404
    )
    status, payload = run_cli(
        monkeypatch,
        capsys,
        "--allow-offline-rea-preflight",
        "--snapshot-id",
        "../../etc/passwd",
        "--receipt-sha256",
        receipt["receipt_sha256"],
    )
    assert status == 2
    assert payload == {"status": "refused", "reason": "rea_preflight_unavailable"}


def test_changed_snapshot_or_receipt_is_refused_without_leaking_paths(
    admitted_target, monkeypatch, capsys
):
    receipt, staging = admitted_target
    args = (
        "--allow-offline-rea-preflight",
        "--snapshot-id",
        receipt["snapshot_id"],
        "--receipt-sha256",
        "f" * 64,
    )
    status, payload = run_cli(monkeypatch, capsys, *args)
    assert status == 2
    assert payload == {"status": "refused", "reason": "rea_preflight_unavailable"}
    path = staging / receipt["snapshot_id"] / "files" / "index.js"
    path.chmod(0o600)
    path.write_text("export const n = 2;\n", encoding="utf-8")
    with pytest.raises(admission.AdmissionRefusal):
        preflight.prepare_rea_target_preflight(request_for(receipt), operator_opt_in=True)
    status, payload = run_cli(
        monkeypatch,
        capsys,
        "--allow-offline-rea-preflight",
        "--snapshot-id",
        receipt["snapshot_id"],
        "--receipt-sha256",
        receipt["receipt_sha256"],
    )
    assert status == 2
    assert payload == {"status": "refused", "reason": "rea_preflight_unavailable"}
    assert str(staging) not in json.dumps(payload)


def test_source_identity_fails_closed(admitted_target, monkeypatch, capsys):
    receipt, _ = admitted_target

    def unavailable():
        raise ReaVendorRefusal("internal_local_path_must_not_leak")

    monkeypatch.setattr(preflight, "verify_vendored_rea_source", unavailable)
    status, payload = run_cli(
        monkeypatch,
        capsys,
        "--allow-offline-rea-preflight",
        "--snapshot-id",
        receipt["snapshot_id"],
        "--receipt-sha256",
        receipt["receipt_sha256"],
    )
    assert status == 2
    assert payload == {"status": "refused", "reason": "rea_preflight_unavailable"}


def test_fingerprint_tamper_and_permission_flags_remain_denied(admitted_target):
    receipt, _ = admitted_target
    result = preflight.prepare_rea_target_preflight(request_for(receipt), operator_opt_in=True)
    changed = result.model_dump(mode="json")
    changed["file_count"] += 1
    with pytest.raises(ValidationError, match="rea_target_preflight_fingerprint_invalid"):
        ReaTargetPreflight.model_validate_json(json.dumps(changed), strict=True)
    changed = result.model_dump(mode="json")
    changed["execution_enabled"] = True
    with pytest.raises(ValidationError):
        ReaTargetPreflight.model_validate_json(json.dumps(changed), strict=True)


def test_catalog_identity_refusal_cannot_be_used_as_permission(
    admitted_target, monkeypatch, capsys
):
    from app.rea_tool_inventory import ReaCatalogRefusal

    receipt, _ = admitted_target

    def changed_catalog():
        raise ReaCatalogRefusal("untrusted_metadata_must_not_leak")

    monkeypatch.setattr(preflight, "rea_tool_inventory", changed_catalog)
    status, output = run_cli(
        monkeypatch,
        capsys,
        "--allow-offline-rea-preflight",
        "--snapshot-id",
        receipt["snapshot_id"],
        "--receipt-sha256",
        receipt["receipt_sha256"],
    )
    assert status == 2
    assert output == {"status": "refused", "reason": "rea_preflight_unavailable"}
    with pytest.raises(PermissionError, match="rea_preflight_explicit_review_required"):
        preflight.prepare_rea_target_preflight(request_for(receipt), operator_opt_in=1)
