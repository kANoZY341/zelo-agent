"""Secret-safe diagnostics for bounded computer-navigation qualification."""

from __future__ import annotations

from urllib.parse import urlsplit

_RESULT_KEYS = (
    "action",
    "agent_instance_id",
    "browser_snapshot",
    "browser_read",
    "browser_navigate",
    "files_list",
    "files_read",
    "completed_at",
)
_NAVIGATION_KEYS = (
    "endpoint_id",
    "url",
    "title",
    "text",
    "text_chars",
    "truncated",
    "elapsed_ms",
    "challenge_present",
)
_SAFE_TASK_STATUSES = frozenset(
    {"pending", "running", "succeeded", "failed", "cancelled", "interrupted"}
)
_SAFE_TASK_ERRORS = frozenset(
    {
        "computer_action_exceeds_agent_spec",
        "computer_action_unsupported",
        "computer_agent_id_invalid",
        "computer_backend_not_configured",
        "computer_capability_denied",
        "computer_control_request_missing",
        "computer_endpoint_invalid",
        "computer_execution_spec_invalid",
        "computer_execution_spec_stale",
        "computer_execution_spec_unavailable",
        "computer_human_control_active",
        "computer_identity_mismatch",
        "computer_navigation_authority_denied",
        "computer_navigation_endpoint_unavailable",
        "computer_navigation_final_origin_denied",
        "computer_navigation_url_invalid",
        "computer_not_running",
        "computer_permission_denied",
        "computer_permission_unavailable",
        "computer_profile_unavailable",
        "computer_read_endpoint_forbidden",
        "computer_response_empty",
        "computer_response_invalid",
        "computer_response_too_large",
        "computer_supervisor_unavailable",
        "computer_timeout",
        "computer_unavailable",
        "execution_timeout",
        "worker_error",
    }
)
_HTTP_ERROR_STATUSES = frozenset(
    {"400", "401", "403", "404", "409", "422", "429", "500", "502", "503", "504"}
)
_SAFE_STAGES = frozenset(
    {
        "inspect_pinned_images",
        "check_unique_resource_names",
        "create_internal_network",
        "verify_internal_network",
        "preflight_loopback_and_proxy_binds",
        "create_ephemeral_supervisor_credentials",
        "write_private_supervisor_config",
        "start_supervisor_container",
        "initialize_isolated_core_services",
        "bind_core_egress_proxy",
        "wait_for_supervisor_api",
        "create_test_project",
        "create_test_agent_instance",
        "grant_test_browser_permission",
        "start_openbot_computer",
        "inspect_openbot_computer",
        "construct_named_navigation_task",
        "submit_named_navigation_task",
        "claim_named_navigation_task",
        "run_named_navigation_task",
        "read_navigation_task_state",
        "navigation_task_failed",
        "navigation_result_missing",
        "navigation_result_projection_mismatch",
        "navigation_endpoint_invalid",
        "navigation_final_url_invalid",
        "navigation_title_invalid",
        "navigation_page_text_invalid",
        "navigation_elapsed_time_invalid",
        "navigation_metadata_invalid",
    }
)
_SAFE_EXCEPTION_TYPES = frozenset(
    {
        "RuntimeError",
        "OSError",
        "ValueError",
        "TypeError",
        "KeyError",
        "TimeoutError",
        "ComputerBackendError",
        "ComputerRuntimeError",
        "DomainError",
        "OperationalError",
        "IntegrityError",
    }
)


def safe_failure_label(stage: str, error: BaseException) -> str:
    """Keep a fixed stage and allowlisted exception class, never its message."""
    safe_stage = stage if stage in _SAFE_STAGES else "unknown_stage"
    error_type = type(error).__name__
    safe_type = error_type if error_type in _SAFE_EXCEPTION_TYPES else "UnexpectedError"
    return f"{safe_stage}:{safe_type}"


def _safe_json_type(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    return "other"


def _safe_task_error_code(error: object) -> str | None:
    if error is None:
        return None
    if not isinstance(error, str):
        return "unrecognized_error"
    if error in _SAFE_TASK_ERRORS:
        return error
    for prefix in ("computer_http_", "computer_supervisor_http_"):
        suffix = error.removeprefix(prefix)
        if error.startswith(prefix) and suffix in _HTTP_ERROR_STATUSES:
            return error
    return "unrecognized_error"


def diagnose_navigation_task(
    status: object,
    error: object,
    result: object,
) -> dict[str, object]:
    """Return allowlisted task/result diagnostics without page or credential values."""
    safe_status = status if isinstance(status, str) and status in _SAFE_TASK_STATUSES else "unknown"
    task_error_code = _safe_task_error_code(error)
    root_type = _safe_json_type(result)
    top = result if isinstance(result, dict) else {}
    navigation = top.get("browser_navigate")
    nav = navigation if isinstance(navigation, dict) else {}
    shape = {
        "root_type": root_type,
        "top_level": {key: _safe_json_type(top[key]) for key in _RESULT_KEYS if key in top},
        "unexpected_top_level_key_count": sum(key not in _RESULT_KEYS for key in top),
        "navigation_type": _safe_json_type(navigation),
        "navigation_fields": {
            key: _safe_json_type(nav[key]) for key in _NAVIGATION_KEYS if key in nav
        },
        "unexpected_navigation_field_count": sum(key not in _NAVIGATION_KEYS for key in nav),
    }

    final_url = nav.get("url")
    try:
        parsed_url = urlsplit(final_url) if isinstance(final_url, str) else None
        final_url_https = parsed_url is not None and parsed_url.scheme == "https"
        final_url_host_matches = parsed_url is not None and parsed_url.hostname == "example.com"
        final_url_port_matches = parsed_url is not None and parsed_url.port in {None, 443}
        final_url_has_no_userinfo = (
            parsed_url is not None and parsed_url.username is None and parsed_url.password is None
        )
    except ValueError:
        final_url_https = False
        final_url_host_matches = False
        final_url_port_matches = False
        final_url_has_no_userinfo = False

    title = nav.get("title")
    page_text = nav.get("text")
    elapsed_ms = nav.get("elapsed_ms")
    truncated = nav.get("truncated")
    challenge_present = nav.get("challenge_present")
    page_title_characters = len(title) if isinstance(title, str) else None
    page_text_characters = len(page_text) if isinstance(page_text, str) else None
    comparisons = {
        "action_matches": top.get("action") == "browser_navigate",
        "endpoint_id_matches": nav.get("endpoint_id") == "example_site",
        "final_url_is_https": final_url_https,
        "final_url_host_matches": final_url_host_matches,
        "final_url_port_matches": final_url_port_matches,
        "final_url_has_no_userinfo": final_url_has_no_userinfo,
        "final_url_within_limit": isinstance(final_url, str) and len(final_url) <= 2048,
        "page_title_is_string": isinstance(title, str),
        "page_title_within_limit": isinstance(title, str) and len(title) <= 512,
        "page_text_is_string": isinstance(page_text, str),
        "page_text_within_limit": isinstance(page_text, str) and len(page_text) <= 6000,
        "elapsed_ms_is_valid": (
            isinstance(elapsed_ms, int)
            and not isinstance(elapsed_ms, bool)
            and 0 <= elapsed_ms <= 300000
        ),
        "truncated_is_boolean": isinstance(truncated, bool),
        "challenge_present_is_boolean": isinstance(challenge_present, bool),
    }

    required_navigation_keys = set(_NAVIGATION_KEYS) - {"text_chars"}
    navigation_types_match = (
        isinstance(navigation, dict)
        and required_navigation_keys.issubset(navigation)
        and isinstance(nav.get("endpoint_id"), str)
        and isinstance(final_url, str)
        and isinstance(title, str)
        and isinstance(page_text, str)
        and isinstance(truncated, bool)
        and isinstance(elapsed_ms, int)
        and not isinstance(elapsed_ms, bool)
        and isinstance(challenge_present, bool)
    )
    projection_matches = (
        isinstance(result, dict)
        and top.get("action") == "browser_navigate"
        and navigation_types_match
    )
    if safe_status != "succeeded":
        failure_stage = "navigation_task_failed"
    elif result is None:
        failure_stage = "navigation_result_missing"
    elif not projection_matches:
        failure_stage = "navigation_result_projection_mismatch"
    elif not comparisons["endpoint_id_matches"]:
        failure_stage = "navigation_endpoint_invalid"
    elif not all(
        comparisons[key]
        for key in (
            "final_url_is_https",
            "final_url_host_matches",
            "final_url_port_matches",
            "final_url_has_no_userinfo",
            "final_url_within_limit",
        )
    ):
        failure_stage = "navigation_final_url_invalid"
    elif not comparisons["page_title_within_limit"]:
        failure_stage = "navigation_title_invalid"
    elif not comparisons["page_text_within_limit"]:
        failure_stage = "navigation_page_text_invalid"
    elif not comparisons["elapsed_ms_is_valid"]:
        failure_stage = "navigation_elapsed_time_invalid"
    elif not comparisons["truncated_is_boolean"] or not comparisons["challenge_present_is_boolean"]:
        failure_stage = "navigation_metadata_invalid"
    else:
        failure_stage = None
    return {
        "failure_stage": failure_stage,
        "diagnostics": {
            "task_status": safe_status,
            "task_error_code": task_error_code,
            "result_schema": shape,
            "comparisons": comparisons,
            "page_title_characters": page_title_characters,
            "page_text_characters": page_text_characters,
        },
    }
