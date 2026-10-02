"""Offline reproductions of actual Chromium pipe event ordering, through the full controller."""

import asyncio
import json

import pytest

from qualification.dns import QualificationError
from tests.test_browser_renderer import protocol


def attach_result(event_change=None, reply_session="session", duplicate=False):
    def transform(request, response, raw):
        if request["method"] != "Target.attachToTarget":
            return raw
        event = {
            "method": "Target.attachedToTarget",
            "params": {
                "sessionId": "session",
                "waitingForDebugger": False,
                "targetInfo": {
                    "targetId": "target",
                    "type": "page",
                    "url": "about:blank",
                    "browserContextId": "context",
                },
            },
        }
        if event_change:
            event_change(event)
        response["result"]["sessionId"] = reply_session
        notification = json.dumps(event).encode() + b"\0"
        return notification * (2 if duplicate else 1) + json.dumps(response).encode() + b"\0"

    return asyncio.run(protocol(transform))[0]


def test_actual_chromium_attach_notification_precedes_the_attach_reply():
    assert attach_result().text == "safe"


@pytest.mark.parametrize(
    "change",
    [
        lambda e: e["params"]["targetInfo"].update(targetId="other-target"),
        lambda e: e["params"]["targetInfo"].update(type="worker"),
        lambda e: e["params"]["targetInfo"].update(url="https://other.example"),
        lambda e: e["params"].update(waitingForDebugger=True),
        lambda e: e.update(sessionId="unrequested-parent"),
    ],
)
def test_attachment_notifications_do_not_authorize_another_target(change):
    with pytest.raises(QualificationError):
        attach_result(change)


def test_attachment_result_must_match_announced_session():
    with pytest.raises(QualificationError):
        attach_result(reply_session="different-session")


def test_duplicate_attachment_events_fail_closed():
    with pytest.raises(QualificationError):
        attach_result(duplicate=True)


def document_open_result(change=None, duplicate=False):
    def transform(request, response, raw):
        if request["method"] != "Page.setDocumentContent":
            return raw
        event = {
            "method": "Page.documentOpened",
            "sessionId": "session",
            "params": {"frame": {"id": "frame", "url": "about:blank"}},
        }
        if change:
            change(event)
        return (json.dumps(event).encode() + b"\0") * (2 if duplicate else 1) + raw

    return asyncio.run(protocol(transform))[0]


def test_document_open_notification_is_bound_to_the_supplied_blank_document():
    assert document_open_result().text == "safe"


@pytest.mark.parametrize(
    "change",
    [
        lambda e: e["params"]["frame"].update(id="other-frame"),
        lambda e: e["params"]["frame"].update(url="file:///etc/hosts"),
        lambda e: e["params"]["frame"].update(parentId="other-frame"),
        lambda e: e.update(sessionId="wrong-session"),
        lambda e: e.update(params=[]),
    ],
)
def test_document_open_event_cannot_substitute_a_frame_or_session(change):
    with pytest.raises(QualificationError):
        document_open_result(change)


def test_duplicate_document_open_is_not_a_second_navigation_permission():
    with pytest.raises(QualificationError):
        document_open_result(duplicate=True)


@pytest.mark.parametrize("prefix", ["x" * 10000, "\U0001f600" * 5000])
def test_browser_truncated_node_preview_is_never_reported_complete(prefix):
    from qualification.renderer_protocol import extract_text
    from tests.test_browser_renderer import element, text

    result = extract_text(element(children=[text(prefix + "\u2026")]))
    assert result.truncated is True
    assert result.text == prefix + "\u2026"


def test_short_literal_ellipsis_is_not_assumed_browser_truncation():
    from qualification.renderer_protocol import extract_text
    from tests.test_browser_renderer import element, text

    result = extract_text(element(children=[text("A short sentence\u2026")]))
    assert result.truncated is False


@pytest.mark.parametrize("encoding", ["utf-16", "utf-32"])
def test_pipe_does_not_autodetect_other_json_wire_encodings(encoding):
    from qualification.renderer_protocol import strict_json

    with pytest.raises(QualificationError):
        strict_json('{"id":1,"result":{}}'.encode(encoding))
