"""Pinned community source bytes are never executable imports or permissions."""

import json
import shutil

import pytest

from qualification import community_sources as sources


def test_selected_sources_verified_inert_with_licenses():
    catalog = sources.verify_community_sources()
    assert {item.name for item in catalog} == set(sources.EXPECTED_SOURCES)
    assert sum(item.file_count for item in catalog) == 19
    assert all(item.source_only and not item.execution_enabled for item in catalog)
    assert all(not item.worker_registered and item.tools_granted == () for item in catalog)
    assert sources.EXPECTED_SOURCES["open_code_review"] == (
        "alibaba/open-code-review",
        "Apache-2.0",
    )


def test_context_mode_has_no_elastic_licensed_source_copy():
    data = json.loads(sources.MANIFEST.read_text())
    assert data["context_mode"]["license"] == "Elastic-2.0"
    assert data["context_mode"]["copied_files"] == []
    assert not (sources.SOURCE_ROOT / "context_mode").exists()


@pytest.fixture
def copied(tmp_path, monkeypatch):
    dest = tmp_path / "sources"
    shutil.copytree(sources.SOURCE_ROOT, dest)
    monkeypatch.setattr(sources, "SOURCE_ROOT", dest)
    return dest


def test_tampered_source_refused(copied):
    target = copied / "open_code_review" / "internal" / "model" / "review.go"
    target.write_bytes(target.read_bytes() + b"\n")
    with pytest.raises(sources.CommunitySourceRefusal, match="community_source_bytes_changed"):
        sources.verify_community_sources()


def test_symlink_and_extra_file_refused(copied, tmp_path):
    target = copied / "ecc" / "skills" / "agent-eval" / "SKILL.md"
    outside = tmp_path / "outside.md"
    outside.write_bytes(target.read_bytes())
    target.unlink()
    target.symlink_to(outside)
    with pytest.raises(sources.CommunitySourceRefusal):
        sources.verify_community_sources()
    target.unlink()
    target.write_bytes(outside.read_bytes())
    (copied / "opendots" / "extra.ts").write_text("unreviewed")
    with pytest.raises(sources.CommunitySourceRefusal, match="community_unexpected_source_file"):
        sources.verify_community_sources()


def test_manifest_digest_and_declared_authority_fail_closed(tmp_path, monkeypatch):
    original = json.loads(sources.MANIFEST.read_text())
    original["granted_tools"] = ["os.system"]
    fake = tmp_path / "manifest.json"
    fake.write_text(json.dumps(original))
    monkeypatch.setattr(sources, "MANIFEST", fake)
    with pytest.raises(sources.CommunitySourceRefusal, match="community_manifest_digest_changed"):
        sources.verify_community_sources()


def test_no_upstream_sources_are_in_core_runtime_registry():
    from app.agent_factory import default_agent_registry
    from app.skills import default_skill_registry

    assert all(
        not item.agent_id.startswith("community") for item in default_agent_registry().list_agents()
    )
    assert all(
        not item.skill_id.startswith("community") for item in default_skill_registry().list()
    )


def test_manifest_is_read_once_then_pinned_before_parsing(monkeypatch):
    calls = 0
    real = sources._read_regular

    def counted(path, maximum):
        nonlocal calls
        data = real(path, maximum)
        if path == sources.MANIFEST:
            calls += 1
            if calls > 1:
                raise AssertionError("Manifest must not be reread after digest verification")
        return data

    monkeypatch.setattr(sources, "_read_regular", counted)
    assert sources.verify_community_sources()
    assert calls == 1


def test_verified_reference_refuses_unknown_and_returns_exact_bytes():
    path = "archify/schemas/common.schema.json"
    read = sources.read_pinned_reference("archify", path)
    assert read == (sources.SOURCE_ROOT / "archify" / path).read_bytes()
    with pytest.raises(sources.CommunitySourceRefusal, match="community_source_path_not_curated"):
        sources.read_pinned_reference("archify", "archify/unknown-schema.json")


def test_verified_reference_rechecks_consumed_bytes_after_manifest_check(monkeypatch):
    original = sources._read_regular
    observed = 0

    def mutate_once(path, maximum):
        nonlocal observed
        value = original(path, maximum)
        if path.name == "common.schema.json":
            observed += 1
            if observed == 2:
                return value + b" "
        return value

    monkeypatch.setattr(sources, "_read_regular", mutate_once)
    with pytest.raises(sources.CommunitySourceRefusal, match="community_source_bytes_changed"):
        sources.read_pinned_reference("archify", "archify/schemas/common.schema.json")
    assert observed == 2
