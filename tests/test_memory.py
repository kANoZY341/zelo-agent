"""v0.13 durable memory foundation contracts."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from tests.conftest import AUTH


def _create_personal(client, content: str, **overrides) -> dict:
    body = {
        "scope": {"kind": "personal"},
        "kind": "fact",
        "content": content,
        "provenance": {"kind": "operator"},
        **overrides,
    }
    response = client.post("/memories", headers=AUTH, json=body)
    assert response.status_code == 201, response.text
    return response.json()


def test_personal_memory_create_get_and_list(client):
    created = _create_personal(client, "Zelo memory foundation is append-only.")
    assert created["scope"] == {"kind": "personal", "id": None}
    assert created["status"] == "active"
    assert created["provenance"]["kind"] == "operator"

    fetched = client.get(f"/memories/{created['id']}", headers=AUTH)
    assert fetched.status_code == 200
    assert fetched.json() == created

    page = client.get(
        "/memories",
        headers=AUTH,
        params={"scope_kind": "personal"},
    )
    assert page.status_code == 200, page.text
    assert page.json()["total"] == 1
    assert page.json()["items"][0]["id"] == created["id"]


def test_project_scope_requires_existing_project_and_is_isolated(client):
    missing = client.post(
        "/memories",
        headers=AUTH,
        json={
            "scope": {
                "kind": "project",
                "id": "00000000-0000-0000-0000-000000000099",
            },
            "kind": "decision",
            "content": "Use bounded memory.",
            "provenance": {"kind": "operator"},
        },
    )
    assert missing.status_code == 404

    project = client.post(
        "/projects",
        headers=AUTH,
        json={"name": "Zelo", "summary": "Persistent personal AI."},
    ).json()
    created = client.post(
        "/memories",
        headers=AUTH,
        json={
            "scope": {"kind": "project", "id": project["id"]},
            "kind": "decision",
            "content": "Keep project memory separate from personal memory.",
            "provenance": {"kind": "project", "ref": project["id"]},
        },
    )
    assert created.status_code == 201, created.text

    personal = client.get(
        "/memories",
        headers=AUTH,
        params={"scope_kind": "personal"},
    ).json()
    assert personal["total"] == 0

    project_page = client.get(
        "/memories",
        headers=AUTH,
        params={"scope_kind": "project", "scope_id": project["id"]},
    ).json()
    assert project_page["total"] == 1
    assert project_page["items"][0]["scope"]["id"] == project["id"]


def test_correction_is_append_only_and_hides_replaced_memory_by_default(client):
    original = _create_personal(client, "The preferred UI accent is blue.")
    corrected = _create_personal(
        client,
        "The preferred UI accent is emerald.",
        replaces_id=original["id"],
        replacement_kind="correction",
    )

    old = client.get(f"/memories/{original['id']}", headers=AUTH).json()
    assert old["status"] == "corrected"
    assert old["replaced_by_id"] == corrected["id"]
    assert corrected["status"] == "active"
    assert corrected["replaces_id"] == original["id"]

    active = client.get(
        "/memories",
        headers=AUTH,
        params={"scope_kind": "personal"},
    ).json()
    assert [item["id"] for item in active["items"]] == [corrected["id"]]

    history = client.get(
        "/memories",
        headers=AUTH,
        params={"scope_kind": "personal", "include_inactive": "true"},
    ).json()
    assert history["total"] == 2
    assert {item["status"] for item in history["items"]} == {"active", "corrected"}

    duplicate = client.post(
        "/memories",
        headers=AUTH,
        json={
            "scope": {"kind": "personal"},
            "kind": "fact",
            "content": "Another competing correction.",
            "provenance": {"kind": "operator"},
            "replaces_id": original["id"],
            "replacement_kind": "correction",
        },
    )
    assert duplicate.status_code == 409


def test_supersession_must_stay_in_the_same_scope(client):
    original = _create_personal(client, "A global note.")

    project = client.post(
        "/projects",
        headers=AUTH,
        json={"name": "Memory", "summary": "Scope isolation."},
    ).json()
    cross_scope = client.post(
        "/memories",
        headers=AUTH,
        json={
            "scope": {"kind": "project", "id": project["id"]},
            "kind": "fact",
            "content": "Project replacement.",
            "provenance": {"kind": "operator"},
            "replaces_id": original["id"],
            "replacement_kind": "supersession",
        },
    )
    assert cross_scope.status_code == 409

    superseded = _create_personal(
        client,
        "A newer global note.",
        replaces_id=original["id"],
        replacement_kind="supersession",
    )
    old = client.get(f"/memories/{original['id']}", headers=AUTH).json()
    assert old["status"] == "superseded"
    assert old["replaced_by_id"] == superseded["id"]


def test_expired_memory_is_preserved_but_not_returned_as_active(client):
    expired_at = datetime.now(UTC) - timedelta(minutes=1)
    expired = _create_personal(
        client,
        "Temporary fact.",
        expires_at=expired_at.isoformat(),
    )
    assert expired["status"] == "expired"

    active = client.get(
        "/memories",
        headers=AUTH,
        params={"scope_kind": "personal"},
    ).json()
    assert active["total"] == 0

    history = client.get(
        "/memories",
        headers=AUTH,
        params={"scope_kind": "personal", "include_inactive": "true"},
    ).json()
    assert history["total"] == 1
    assert history["items"][0]["status"] == "expired"


def test_memory_scope_and_replacement_shapes_are_validated(client):
    personal_with_id = client.post(
        "/memories",
        headers=AUTH,
        json={
            "scope": {"kind": "personal", "id": "not-allowed"},
            "kind": "fact",
            "content": "Invalid scope.",
            "provenance": {"kind": "operator"},
        },
    )
    assert personal_with_id.status_code == 422

    missing_project_id = client.get(
        "/memories",
        headers=AUTH,
        params={"scope_kind": "project"},
    )
    assert missing_project_id.status_code == 422

    incomplete_replacement = client.post(
        "/memories",
        headers=AUTH,
        json={
            "scope": {"kind": "personal"},
            "kind": "fact",
            "content": "Invalid replacement.",
            "provenance": {"kind": "operator"},
            "replacement_kind": "correction",
        },
    )
    assert incomplete_replacement.status_code == 422


def test_retrieval_returns_only_active_current_personal_memory(client):
    original = _create_personal(client, "My favorite interface color is blue.")
    corrected = _create_personal(
        client,
        "My favorite interface color is black.",
        replaces_id=original["id"],
        replacement_kind="correction",
    )
    _create_personal(
        client,
        "My favorite interface color is temporary green.",
        expires_at=(datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
    )
    _create_personal(client, "My preferred dessert is caramel.")

    response = client.post(
        "/memories/retrieve",
        headers=AUTH,
        json={
            "query": "what is my favorite interface color?",
            "scopes": [{"kind": "personal"}],
            "limit": 6,
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["retrieval"] == "bounded_lexical_v1"
    assert [item["id"] for item in payload["items"]] == [corrected["id"]]
    assert payload["items"][0]["matched_terms"] == ["favorite", "interface", "color"]


def test_retrieval_does_not_return_single_generic_overlap_for_multi_term_query(client):
    _create_personal(client, "My favorite interface color is black.")
    response = client.post(
        "/memories/retrieve",
        headers=AUTH,
        json={
            "query": "what is my favorite dessert?",
            "scopes": [{"kind": "personal"}],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["items"] == []


def test_retrieval_keeps_project_and_agent_scopes_explicit(client):
    alpha = client.post(
        "/projects",
        headers=AUTH,
        json={"name": "Alpha", "summary": "First project."},
    ).json()
    beta = client.post(
        "/projects",
        headers=AUTH,
        json={"name": "Beta", "summary": "Second project."},
    ).json()

    alpha_memory = client.post(
        "/memories",
        headers=AUTH,
        json={
            "scope": {"kind": "project", "id": alpha["id"]},
            "kind": "decision",
            "content": "The deployment database is SQLite.",
            "provenance": {"kind": "project", "ref": alpha["id"]},
        },
    ).json()
    client.post(
        "/memories",
        headers=AUTH,
        json={
            "scope": {"kind": "project", "id": beta["id"]},
            "kind": "decision",
            "content": "The deployment database is PostgreSQL.",
            "provenance": {"kind": "project", "ref": beta["id"]},
        },
    )
    research_memory = client.post(
        "/memories",
        headers=AUTH,
        json={
            "scope": {"kind": "agent", "id": "research"},
            "kind": "experience",
            "content": "Evidence retrieval should preserve provenance.",
            "provenance": {"kind": "agent", "ref": "research"},
        },
    ).json()

    project_response = client.post(
        "/memories/retrieve",
        headers=AUTH,
        json={
            "query": "deployment database",
            "scopes": [{"kind": "project", "id": alpha["id"]}],
        },
    )
    assert project_response.status_code == 200, project_response.text
    assert [item["id"] for item in project_response.json()["items"]] == [alpha_memory["id"]]

    agent_response = client.post(
        "/memories/retrieve",
        headers=AUTH,
        json={
            "query": "evidence retrieval provenance",
            "scopes": [{"kind": "agent", "id": "research"}],
        },
    )
    assert agent_response.status_code == 200, agent_response.text
    assert [item["id"] for item in agent_response.json()["items"]] == [research_memory["id"]]


def test_retrieval_is_bounded_and_rejects_duplicate_scopes(client):
    long_memory = _create_personal(
        client,
        ("prefix " * 300) + "favorite telescope aperture eighty millimeters",
    )
    response = client.post(
        "/memories/retrieve",
        headers=AUTH,
        json={
            "query": "favorite telescope aperture",
            "scopes": [{"kind": "personal"}],
            "limit": 1,
        },
    )
    assert response.status_code == 200, response.text
    item = response.json()["items"][0]
    assert item["id"] == long_memory["id"]
    assert item["content_truncated"] is True
    assert len(item["content"]) <= 1200
    assert "telescope aperture" in item["content"]

    duplicate = client.post(
        "/memories/retrieve",
        headers=AUTH,
        json={
            "query": "telescope aperture",
            "scopes": [{"kind": "personal"}, {"kind": "personal"}],
        },
    )
    assert duplicate.status_code == 422


def test_retrieval_short_circuits_when_query_has_no_meaningful_terms(client):
    _create_personal(client, "A memory that should not be scanned for empty intent.")
    response = client.post(
        "/memories/retrieve",
        headers=AUTH,
        json={"query": "what is it", "scopes": [{"kind": "personal"}]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["candidate_count"] == 0
    assert response.json()["items"] == []


def test_retrieval_rejects_blank_query(client):
    response = client.post(
        "/memories/retrieve",
        headers=AUTH,
        json={"query": "   ", "scopes": [{"kind": "personal"}]},
    )
    assert response.status_code == 422


def test_memory_routes_require_operator_auth(client):
    client.headers.pop("Authorization")
    response = client.get("/memories", params={"scope_kind": "personal"})
    assert response.status_code in {401, 403}


def test_memory_records_are_database_append_only_and_project_scope_is_guarded(database):
    with pytest.raises(IntegrityError, match="existing Project"):
        with database.transaction() as session:
            session.execute(
                text(
                    """
                    INSERT INTO memory_records (
                        id, scope_kind, scope_id, kind, content,
                        provenance_kind, provenance_note, created_at
                    ) VALUES (
                        '00000000-0000-0000-0000-000000000091',
                        'project',
                        '00000000-0000-0000-0000-000000000099',
                        'fact',
                        'Invalid project memory',
                        'operator',
                        '',
                        '2026-09-29 00:00:00'
                    )
                    """
                )
            )

    with database.transaction() as session:
        session.execute(
            text(
                """
                INSERT INTO memory_records (
                    id, scope_kind, kind, content,
                    provenance_kind, provenance_note, created_at
                ) VALUES (
                    '00000000-0000-0000-0000-000000000092',
                    'personal',
                    'fact',
                    'Immutable memory',
                    'operator',
                    '',
                    '2026-09-29 00:00:00'
                )
                """
            )
        )

    for statement in (
        "UPDATE memory_records SET content='mutated' "
        "WHERE id='00000000-0000-0000-0000-000000000092'",
        "DELETE FROM memory_records WHERE id='00000000-0000-0000-0000-000000000092'",
    ):
        with pytest.raises(IntegrityError, match="append-only"):
            with database.transaction() as session:
                session.execute(text(statement))

    with pytest.raises(IntegrityError, match="id already exists"):
        with database.transaction() as session:
            session.execute(
                text(
                    """
                    INSERT OR REPLACE INTO memory_records (
                        id, scope_kind, kind, content,
                        provenance_kind, provenance_note, created_at
                    ) VALUES (
                        '00000000-0000-0000-0000-000000000092',
                        'personal',
                        'fact',
                        'replacement attempt',
                        'operator',
                        '',
                        '2026-09-29 00:00:01'
                    )
                    """
                )
            )

    with database.read() as session:
        assert (
            session.scalar(
                text(
                    "SELECT content FROM memory_records "
                    "WHERE id='00000000-0000-0000-0000-000000000092'"
                )
            )
            == "Immutable memory"
        )

    with pytest.raises(IntegrityError, match="already exists"):
        with database.transaction() as session:
            session.execute(
                text(
                    """
                    INSERT OR REPLACE INTO memory_records (
                        id, scope_kind, kind, content,
                        provenance_kind, provenance_note, created_at
                    ) VALUES (
                        '00000000-0000-0000-0000-000000000092',
                        'personal',
                        'fact',
                        'Replacement must not overwrite history',
                        'operator',
                        '',
                        '2026-09-29 00:01:00'
                    )
                    """
                )
            )

    with database.read() as session:
        assert (
            session.scalar(
                text(
                    "SELECT content FROM memory_records "
                    "WHERE id='00000000-0000-0000-0000-000000000092'"
                )
            )
            == "Immutable memory"
        )

    with database.transaction() as session:
        session.execute(
            text(
                """
                INSERT INTO projects (
                    id, name, summary, status, created_at, updated_at
                ) VALUES (
                    '00000000-0000-0000-0000-000000000093',
                    'Memory scope project',
                    '',
                    'active',
                    '2026-09-29 00:00:00',
                    '2026-09-29 00:00:00'
                )
                """
            )
        )

    with pytest.raises(IntegrityError, match="same scope"):
        with database.transaction() as session:
            session.execute(
                text(
                    """
                    INSERT INTO memory_records (
                        id, scope_kind, scope_id, kind, content,
                        provenance_kind, provenance_note, replaces_id,
                        replacement_kind, created_at
                    ) VALUES (
                        '00000000-0000-0000-0000-000000000094',
                        'project',
                        '00000000-0000-0000-0000-000000000093',
                        'fact',
                        'Cross-scope replacement',
                        'operator',
                        '',
                        '00000000-0000-0000-0000-000000000092',
                        'correction',
                        '2026-09-29 00:02:00'
                    )
                    """
                )
            )

    with database.read() as session:
        assert (
            session.scalar(
                text(
                    "SELECT COUNT(*) FROM memory_records "
                    "WHERE replaces_id='00000000-0000-0000-0000-000000000092'"
                )
            )
            == 0
        )

    with database.transaction() as session:
        session.execute(
            text(
                """
                INSERT INTO memory_records (
                    id, scope_kind, kind, content,
                    provenance_kind, provenance_note, replaces_id,
                    replacement_kind, created_at
                ) VALUES (
                    '00000000-0000-0000-0000-000000000095',
                    'personal',
                    'fact',
                    'First immutable correction',
                    'operator',
                    '',
                    '00000000-0000-0000-0000-000000000092',
                    'correction',
                    '2026-09-29 00:03:00'
                )
                """
            )
        )

    with pytest.raises(IntegrityError, match="already been replaced"):
        with database.transaction() as session:
            session.execute(
                text(
                    """
                    INSERT OR REPLACE INTO memory_records (
                        id, scope_kind, kind, content,
                        provenance_kind, provenance_note, replaces_id,
                        replacement_kind, created_at
                    ) VALUES (
                        '00000000-0000-0000-0000-000000000096',
                        'personal',
                        'fact',
                        'Must not replace the first correction',
                        'operator',
                        '',
                        '00000000-0000-0000-0000-000000000092',
                        'correction',
                        '2026-09-29 00:04:00'
                    )
                    """
                )
            )

    with database.read() as session:
        replacement = session.execute(
            text(
                "SELECT id, content FROM memory_records "
                "WHERE replaces_id='00000000-0000-0000-0000-000000000092'"
            )
        ).one()
        assert replacement == (
            "00000000-0000-0000-0000-000000000095",
            "First immutable correction",
        )
