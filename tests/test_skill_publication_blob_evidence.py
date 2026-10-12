"""Immutable reviewed Git tree+blob evidence, with no provider writes."""

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
    store, _agent, claim, request, transport, remote = _remote_fixture(
        database, proposal_case, tmp_path
    )
    rev = store.get(claim.proposal_id).proposal.proposed_revision
    data = skill_procedure_artifact_bytes(rev)
    blob_oid = hashlib.sha1(
        f"blob {len(data)}\0".encode() + data, usedforsecurity=False
    ).hexdigest()
    reviewed_path = (
        f"skills/procedures/{rev.source_skill_id}/{rev.procedure_id}/"
        f"v{rev.version}-{rev.content_sha256}.json"
    )
    base = f"/repos/{request.publication.repository}"
    compare = {
        "status": "ahead",
        "base_commit": {"sha": request.publication.pr_base_sha},
        "head_commit": {"sha": request.publication.pr_head_sha},
        "files": [{"filename": reviewed_path, "status": "added", "sha": blob_oid}],
    }
    tree_oids = [char * 40 for char in ("a", "b", "c", "d", "e")]
    commit = {
        "sha": request.publication.pr_head_sha,
        "tree": {"sha": tree_oids[0]},
    }
    paths = reviewed_path.split("/")
    assert len(paths) == 5
    tree_calls = []
    for index, segment in enumerate(paths):
        leaf = index == len(paths) - 1
        tree_calls.append(
            (
                "GET",
                base + f"/git/trees/{tree_oids[index]}",
                {
                    "sha": tree_oids[index],
                    "truncated": False,
                    "tree": [
                        {
                            "path": segment,
                            "type": "blob" if leaf else "tree",
                            "mode": "100644" if leaf else "040000",
                            "sha": blob_oid if leaf else tree_oids[index + 1],
                        }
                    ],
                },
            )
        )
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
                base
                + f"/compare/{request.publication.pr_base_sha}...{request.publication.pr_head_sha}",
                compare,
            ),
            (
                "GET",
                base + f"/git/commits/{request.publication.pr_head_sha}",
                commit,
            ),
            *tree_calls,
            ("GET", base + f"/git/blobs/{blob_oid}", blob),
            ("GET", base + f"/pulls/{request.publication.pr_number}", final),
        ]
    )
    indexes = {"compare": 3, "commit": 4, "trees": (5, 6, 7, 8, 9), "blob": 10, "final": 11}
    assert len(transport.responses) == 12
    return store, claim, request, transport, SkillPublicationBlobEvidenceService(remote), indexes


def test_immutable_head_tree_and_sha256_prove_exact_regular_file(database, proposal_case, tmp_path):
    store, claim, request, transport, service, _indexes = _arrange(
        database, proposal_case, tmp_path
    )
    result = asyncio.run(service.verify(claim.id, request, OPERATOR))
    assert isinstance(result, SkillPublicationBlobEvidence)
    assert result.claim_id == claim.id
    assert result.head_sha == request.publication.pr_head_sha
    expected_revision = store.get(claim.proposal_id).proposal.proposed_revision
    assert result.artifact_sha256 == skill_procedure_artifact_sha256(expected_revision)
    assert result.immutable_base_head_compare_verified is True
    assert result.exact_head_tree_regular_file_mode_verified is True
    assert result.git_blob_oid_and_sha256_verified is True
    assert result.no_github_write_authority is True
    assert result.no_runtime_skill_authority is True
    assert len(transport.calls) == 12 and transport.responses == []
    assert all(method == "GET" for method, _, _ in transport.calls)
    assert store.get_promotion_link(claim.proposal_id) is None


@pytest.mark.parametrize("mutation", ("second", "wrong_path", "deleted", "invalid_oid"))
def test_immutable_comparison_refuses_extra_or_wrong_files(
    database, proposal_case, tmp_path, mutation
):
    _store, claim, request, transport, service, idx = _arrange(database, proposal_case, tmp_path)
    files = transport.responses[idx["compare"]][2]["files"]
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


@pytest.mark.parametrize("mutation", ("base", "compare_head", "status", "head_commit", "root_tree"))
def test_wrong_immutable_comparison_or_head_commit_denied(
    database, proposal_case, tmp_path, mutation
):
    _store, claim, request, transport, service, idx = _arrange(database, proposal_case, tmp_path)
    if mutation == "base":
        transport.responses[idx["compare"]][2]["base_commit"]["sha"] = "f" * 40
    elif mutation == "compare_head":
        transport.responses[idx["compare"]][2]["head_commit"]["sha"] = "f" * 40
    elif mutation == "status":
        transport.responses[idx["compare"]][2]["status"] = "identical"
    elif mutation == "head_commit":
        transport.responses[idx["commit"]][2]["sha"] = "f" * 40
    else:
        transport.responses[idx["commit"]][2]["tree"]["sha"] = "not-an-oid"
    with pytest.raises(
        DeveloperError, match="blob_(files_not_exclusive|compare_invalid|commit_changed)"
    ):
        asyncio.run(service.verify(claim.id, request, OPERATOR))
    assert all(method == "GET" for method, _, _ in transport.calls)


@pytest.mark.parametrize("mutation", ("symlink", "submodule", "missing", "truncated", "wrong_blob"))
def test_head_tree_rejects_wrong_file_mode_or_membership(
    database, proposal_case, tmp_path, mutation
):
    _store, claim, request, transport, service, idx = _arrange(database, proposal_case, tmp_path)
    leaf = transport.responses[idx["trees"][-1]][2]
    if mutation == "symlink":
        leaf["tree"][0]["mode"] = "120000"
    elif mutation == "submodule":
        leaf["tree"][0]["type"] = "commit"
        leaf["tree"][0]["mode"] = "160000"
    elif mutation == "missing":
        leaf["tree"][0]["path"] = "other.json"
    elif mutation == "truncated":
        leaf["truncated"] = True
    else:
        leaf["tree"][0]["sha"] = "f" * 40
    with pytest.raises(DeveloperError, match="skill_publication_blob_(tree_.*|tree_blob_changed)"):
        asyncio.run(service.verify(claim.id, request, OPERATOR))
    assert len(transport.calls) == 10
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
def test_corrupt_or_oversized_immutable_blob_denied(
    database, proposal_case, tmp_path, field, value, error
):
    _store, claim, request, transport, service, idx = _arrange(database, proposal_case, tmp_path)
    transport.responses[idx["blob"]][2][field] = value
    with pytest.raises(DeveloperError, match=f"blob_{error}"):
        asyncio.run(service.verify(claim.id, request, OPERATOR))
    assert len(transport.calls) == 11
    assert all(method == "GET" for method, _, _ in transport.calls)


def test_git_oid_mismatch_denied_even_when_sha256_content_matches(
    database, proposal_case, tmp_path
):
    _store, claim, request, transport, service, idx = _arrange(database, proposal_case, tmp_path)
    other_oid = "f" * 40
    transport.responses[idx["compare"]][2]["files"][0]["sha"] = other_oid
    transport.responses[idx["trees"][-1]][2]["tree"][0]["sha"] = other_oid
    _method, _endpoint, blob = transport.responses[idx["blob"]]
    blob["sha"] = other_oid
    transport.responses[idx["blob"]] = (
        "GET",
        f"/repos/{request.publication.repository}/git/blobs/{other_oid}",
        blob,
    )
    with pytest.raises(DeveloperError, match="skill_publication_blob_git_oid_invalid"):
        asyncio.run(service.verify(claim.id, request, OPERATOR))
    assert len(transport.calls) == 11


def test_multiline_github_blob_encoding_succeeds(database, proposal_case, tmp_path):
    _store, claim, request, transport, service, idx = _arrange(database, proposal_case, tmp_path)
    blob = transport.responses[idx["blob"]][2]
    encoded = blob["content"]
    blob["content"] = "\n".join(encoded[i : i + 60] for i in range(0, len(encoded), 60))
    assert asyncio.run(
        service.verify(claim.id, request, OPERATOR)
    ).exact_head_tree_regular_file_mode_verified
    assert len(transport.calls) == 12


def test_final_mutable_pr_drift_caught_after_immutable_checks(database, proposal_case, tmp_path):
    _store, claim, request, transport, service, idx = _arrange(database, proposal_case, tmp_path)
    transport.responses[idx["final"]][2]["head"]["sha"] = "f" * 40
    with pytest.raises(DeveloperError, match="skill_publication_remote_pr_changed"):
        asyncio.run(service.verify(claim.id, request, OPERATOR))
    assert len(transport.calls) == 12
    assert all(method == "GET" for method, _, _ in transport.calls)


def test_unauthenticated_caller_cannot_read_provider(database, proposal_case, tmp_path):
    _store, claim, request, transport, service, _idx = _arrange(database, proposal_case, tmp_path)
    with pytest.raises(PermissionError, match="operator_required"):
        asyncio.run(service.verify(claim.id, request, "ahmad"))
    assert transport.calls == []
