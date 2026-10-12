"""Read-only Git blob proof of exact reviewed learned-skill artifact bytes."""

import asyncio
import base64
import hashlib

import pytest

from app.auth import OperatorPrincipal
from app.developer_process import DeveloperError
from app.skill_learning import skill_procedure_artifact_bytes, skill_procedure_artifact_sha256
from app.skill_publication_blob_evidence import (
    SkillPublicationBlobEvidence,
    SkillPublicationBlobEvidenceService,
)
from tests import test_promotion_merge as merge_tests
from tests import test_skill_learning_store as learning_store_tests
from tests.test_skill_publication_remote_evidence import _remote_fixture

OPERATOR = OperatorPrincipal()


@pytest.fixture
def proposal_case(database, settings):
    return learning_store_tests.proposal_case.__wrapped__(database, settings)


def _arrange(database, proposal_case, tmp_path):
    store, agent, claim, request, transport, remote = _remote_fixture(
        database, proposal_case, tmp_path
    )
    rev = store.get(claim.proposal_id).proposal.proposed_revision
    data = skill_procedure_artifact_bytes(rev)
    blob_oid = hashlib.sha1(
        f"blob {len(data)}\0".encode() + data, usedforsecurity=False
    ).hexdigest()
    base = f"/repos/{request.publication.repository}"
    files = [
        {
            "filename": f"skills/procedures/{rev.source_skill_id}/{rev.procedure_id}/"
            f"v{rev.version}-{rev.content_sha256}.json",
            "status": "added",
            "sha": blob_oid,
        }
    ]
    blob = {
        "sha": blob_oid,
        "size": len(data),
        "encoding": "base64",
        "content": base64.b64encode(data).decode("ascii"),
    }
    final = merge_tests.open_pr(request)
    final["draft"] = False
    transport.responses.extend(
        [
            (
                "GET",
                base + f"/pulls/{request.publication.pr_number}/files?per_page=100&page=1",
                files,
            ),
            ("GET", base + f"/git/blobs/{blob_oid}", blob),
            ("GET", base + f"/pulls/{request.publication.pr_number}", final),
        ]
    )
    return store, claim, request, transport, SkillPublicationBlobEvidenceService(remote)


def test_exact_provider_blob_is_cryptographically_bound_to_core_artifact(
    database, proposal_case, tmp_path
):
    store, claim, request, transport, service = _arrange(database, proposal_case, tmp_path)
    result = asyncio.run(service.verify(claim.id, request, OPERATOR))
    assert isinstance(result, SkillPublicationBlobEvidence)
    assert result.claim_id == claim.id
    assert result.head_sha == request.publication.pr_head_sha
    expected_revision = store.get(claim.proposal_id).proposal.proposed_revision
    assert result.artifact_sha256 == skill_procedure_artifact_sha256(expected_revision)
    assert result.git_blob_oid_and_sha256_verified is True
    assert result.git_tree_mode_not_verified is True
    assert result.no_github_write_authority is True
    assert result.no_runtime_skill_authority is True
    assert len(transport.calls) == 6 and transport.responses == []
    assert all(method == "GET" for method, _, _ in transport.calls)
    assert store.get_promotion_link(claim.proposal_id) is None


@pytest.mark.parametrize("mutation", ("second", "wrong_path", "deleted", "invalid_oid"))
def test_unexpected_pr_files_refused_without_fetching_blob(
    database, proposal_case, tmp_path, mutation
):
    _store, claim, request, transport, service = _arrange(database, proposal_case, tmp_path)
    files = transport.responses[3][2]
    if mutation == "second":
        files.append({**files[0], "filename": "another.json"})
    elif mutation == "wrong_path":
        files[0]["filename"] = "skills/other.json"
    elif mutation == "deleted":
        files[0]["status"] = "removed"
    else:
        files[0]["sha"] = "not-a-git-oid"
    with pytest.raises(DeveloperError, match="blob_(files_not_exclusive|pr_file_changed)"):
        asyncio.run(service.verify(claim.id, request, OPERATOR))
    assert len(transport.calls) == 4
    assert all(method == "GET" for method, _, _ in transport.calls)


@pytest.mark.parametrize(
    ("field", "value", "error"),
    (
        ("content", "***", "encoding_invalid"),
        ("content", base64.b64encode(b"other").decode("ascii"), "content_changed"),
        ("content", "a" * 90_001, "response_invalid"),
        ("encoding", "plain", "response_invalid"),
        ("sha", "f" * 40, "response_invalid"),
        ("size", True, "response_invalid"),
    ),
)
def test_modified_or_malformed_provider_blob_refuses(
    database, proposal_case, tmp_path, field, value, error
):
    _store, claim, request, transport, service = _arrange(database, proposal_case, tmp_path)
    transport.responses[4][2][field] = value
    with pytest.raises(DeveloperError, match=f"blob_{error}"):
        asyncio.run(service.verify(claim.id, request, OPERATOR))
    assert len(transport.calls) == 5
    assert all(method == "GET" for method, _, _ in transport.calls)


def test_changed_final_pr_head_denied_after_byte_attestation(database, proposal_case, tmp_path):
    _store, claim, request, transport, service = _arrange(database, proposal_case, tmp_path)
    transport.responses[5][2]["head"]["sha"] = "f" * 40
    with pytest.raises(DeveloperError, match="skill_publication_remote_pr_changed"):
        asyncio.run(service.verify(claim.id, request, OPERATOR))
    assert len(transport.calls) == 6
    assert all(method == "GET" for method, _, _ in transport.calls)


def test_untrusted_pr_candidate_does_not_allow_ownerless_blob_checks(
    database, proposal_case, tmp_path
):
    _store, claim, request, transport, service = _arrange(database, proposal_case, tmp_path)
    with pytest.raises(PermissionError, match="operator_required"):
        asyncio.run(service.verify(claim.id, request, "ahmad"))
    assert transport.calls == []


def test_git_blob_object_id_mismatch_refused_even_when_sha256_content_matches(
    database, proposal_case, tmp_path
):
    _store, claim, request, transport, service = _arrange(database, proposal_case, tmp_path)
    other_oid = "f" * 40
    transport.responses[3][2][0]["sha"] = other_oid
    _method, endpoint, blob = transport.responses[4]
    assert endpoint.endswith("/git/blobs/" + blob["sha"])
    blob["sha"] = other_oid
    transport.responses[4] = (
        "GET",
        f"/repos/{request.publication.repository}/git/blobs/{other_oid}",
        blob,
    )
    with pytest.raises(DeveloperError, match="skill_publication_blob_git_oid_invalid"):
        asyncio.run(service.verify(claim.id, request, OPERATOR))
    assert len(transport.calls) == 5
    assert all(method == "GET" for method, _, _ in transport.calls)


def test_standard_multiline_github_base64_blob_accepted(database, proposal_case, tmp_path):
    _store, claim, request, transport, service = _arrange(database, proposal_case, tmp_path)
    blob = transport.responses[4][2]
    encoded = blob["content"]
    blob["content"] = "\n".join(encoded[i : i + 60] for i in range(0, len(encoded), 60))
    assert asyncio.run(service.verify(claim.id, request, OPERATOR)).git_blob_oid_and_sha256_verified
    assert len(transport.calls) == 6
    assert all(method == "GET" for method, _, _ in transport.calls)
