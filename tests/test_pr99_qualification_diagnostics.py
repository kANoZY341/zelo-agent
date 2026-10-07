import json

import pytest
from pydantic import ValidationError

from app.computer_worker import ComputerRuntime
from app.schemas import ComputerRunResult
from app.tasks import _project_task_result
from qualification.computer_navigation_diagnostics import (
    diagnose_navigation_task,
    safe_failure_label,
)

SECRET = "master-token-must-never-appear-in-diagnostics"


def canonical_result():
    worker_result = {
        "action": "browser_navigate",
        "agent_instance_id": "12345678-1234-4234-8234-123456789012",
        "browser_navigate": {
            "endpoint_id": "example_site",
            "url": "https://example.com/",
            "title": "Canonical bounded title",
            "text": f"Readable sample {SECRET}",
            "truncated": False,
            "elapsed_ms": 12,
            "challenge_present": False,
        },
        "completed_at": "2026-10-05T00:00:00+00:00",
    }
    return ComputerRunResult.model_validate(worker_result).model_dump(mode="json")


def test_success_result_matches_runner_task_response_contract():
    result = diagnose_navigation_task("succeeded", None, canonical_result())

    assert result["failure_stage"] is None
    diagnostics = result["diagnostics"]
    assert diagnostics["task_status"] == "succeeded"
    assert diagnostics["task_error_code"] is None
    assert diagnostics["result_schema"]["top_level"]["browser_navigate"] == "object"
    assert diagnostics["result_schema"]["navigation_fields"]["text"] == "string"
    assert diagnostics["page_text_characters"] == len(f"Readable sample {SECRET}")
    assert diagnostics["comparisons"] == {
        "action_matches": True,
        "endpoint_id_matches": True,
        "final_url_is_https": True,
        "final_url_host_matches": True,
        "final_url_port_matches": True,
        "final_url_has_no_userinfo": True,
        "final_url_within_limit": True,
        "page_title_is_string": True,
        "page_title_within_limit": True,
        "page_text_is_string": True,
        "page_text_within_limit": True,
        "elapsed_ms_is_valid": True,
        "truncated_is_boolean": True,
        "challenge_present_is_boolean": True,
    }
    assert SECRET not in json.dumps(diagnostics)
    assert "Example Domain" not in json.dumps(diagnostics)


def test_failed_task_retains_allowlisted_error_code_only():
    result = diagnose_navigation_task(
        "failed",
        "computer_supervisor_http_503",
        {"private_error": SECRET},
    )

    assert result["failure_stage"] == "navigation_task_failed"
    assert result["diagnostics"]["task_status"] == "failed"
    assert result["diagnostics"]["task_error_code"] == "computer_supervisor_http_503"
    assert result["diagnostics"]["result_schema"]["unexpected_top_level_key_count"] == 1
    assert "private_error" not in json.dumps(result)
    assert SECRET not in json.dumps(result)

    hidden = diagnose_navigation_task("failed", SECRET, None)
    assert hidden["diagnostics"]["task_error_code"] == "unrecognized_error"
    assert SECRET not in json.dumps(hidden)


def test_revoked_task_status_and_code_are_retained_safely():
    result = diagnose_navigation_task("cancelled", "computer_execution_spec_invalid", None)

    assert result["failure_stage"] == "navigation_task_failed"
    assert result["diagnostics"]["task_status"] == "cancelled"
    assert result["diagnostics"]["task_error_code"] == "computer_execution_spec_invalid"


def test_missing_result_is_distinct_from_failed_task():
    result = diagnose_navigation_task("succeeded", None, None)

    assert result["failure_stage"] == "navigation_result_missing"
    assert result["diagnostics"]["result_schema"]["root_type"] == "null"


def test_runner_text_normalizer_allows_empty_and_caps_output():
    assert ComputerRuntime._string("", 6000, truncate=True) == ("", False)
    normalized, truncated = ComputerRuntime._string("x" * 6001, 6000, truncate=True)
    assert len(normalized) == 6000
    assert truncated is True


def test_runner_schema_rejects_text_over_6000_characters():
    result = canonical_result()
    result["browser_navigate"]["text"] = "x" * 6001

    with pytest.raises(ValidationError):
        ComputerRunResult.model_validate(result)


def test_empty_optional_title_and_text_follow_runner_schema_contract():
    result = canonical_result()
    result["browser_navigate"]["title"] = ""
    result["browser_navigate"]["text"] = ""
    diagnosis = diagnose_navigation_task("succeeded", None, result)

    assert diagnosis["failure_stage"] is None
    comparisons = diagnosis["diagnostics"]["comparisons"]
    assert comparisons["page_title_is_string"] is True
    assert comparisons["page_title_within_limit"] is True
    assert comparisons["page_text_is_string"] is True
    assert comparisons["page_text_within_limit"] is True
    assert diagnosis["diagnostics"]["page_title_characters"] == 0
    assert diagnosis["diagnostics"]["page_text_characters"] == 0


def test_oversized_navigation_text_is_detected_without_emitting_content():
    result = canonical_result()
    result["browser_navigate"]["text"] = "x" * 6001
    diagnosis = diagnose_navigation_task("succeeded", None, result)

    assert diagnosis["failure_stage"] == "navigation_page_text_invalid"
    assert diagnosis["diagnostics"]["page_text_characters"] == 6001
    assert diagnosis["diagnostics"]["comparisons"]["page_text_within_limit"] is False
    assert "x" * 100 not in json.dumps(diagnosis)


def test_projected_result_is_detected_as_a_contract_mismatch():
    projection, truncated = _project_task_result("computer_run", canonical_result(), 100_000)

    assert truncated is True
    result = diagnose_navigation_task("succeeded", None, projection)

    assert result["failure_stage"] == "navigation_result_projection_mismatch"
    shape = result["diagnostics"]["result_schema"]
    assert shape["top_level"]["browser_navigate"] == "object"
    assert shape["navigation_fields"]["text_chars"] == "integer"
    assert "text" not in shape["navigation_fields"]
    assert all(result["diagnostics"]["comparisons"].values()) is False
    assert SECRET not in json.dumps(result)


class LeakyError(Exception):
    pass


def test_failure_label_redacts_exception_message_and_unknown_stage():
    label = safe_failure_label("run_named_navigation_task", LeakyError(SECRET))
    assert label == "run_named_navigation_task:UnexpectedError"
    assert SECRET not in label
