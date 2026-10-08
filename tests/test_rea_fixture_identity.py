"""Integrity gate on the actual staged REA fixed fixture, not just the CLI preparer."""

import hashlib

import pytest

from qualification import rea_static_mcp as rea
from scripts.qualify_rea_static_mcp import INDEX_JS, PACKAGE_JSON


def test_manual_fixture_pin_is_independently_derived_from_exact_source_bytes(tmp_path):
    source = tmp_path / "fixture"
    source.mkdir()
    files = {"index.js": INDEX_JS, "package.json": PACKAGE_JSON}
    digest = hashlib.sha256(b"zelo-rea-js-directory-v1\0")
    for name, data in sorted(files.items()):
        (source / name).write_bytes(data)
        digest.update(b"F" + name.encode("ascii") + b"\0" + str(len(data)).encode("ascii") + b"\0")
        digest.update(data)
    stage = tmp_path / "stage"
    stage.mkdir()
    observed, count, size = rea.stage_source(source, stage)
    assert (observed, count, size) == (
        digest.hexdigest(),
        len(files),
        sum(map(len, files.values())),
    )
    assert (observed, count, size) == (
        rea.FIXTURE_SHA256,
        rea.FIXTURE_FILE_COUNT,
        rea.FIXTURE_TOTAL_BYTES,
    )


@pytest.mark.parametrize("change", ["same_size", "other_js", "drop_package", "mutated_json"])
def test_direct_rea_qualifier_refuses_nonfixture_before_docker(tmp_path, monkeypatch, change):
    workspace = tmp_path / "qualification"
    source = workspace / "fixtures" / "tiny-app"
    source.mkdir(parents=True)
    (source / "index.js").write_bytes(INDEX_JS)
    (source / "package.json").write_bytes(PACKAGE_JSON)
    if change == "same_size":
        (source / "index.js").write_bytes(INDEX_JS.replace(b"double", b"single"))
    elif change == "other_js":
        (source / "extra.js").write_text("module.exports = 1;\n")
    elif change == "drop_package":
        (source / "package.json").unlink()
    else:
        (source / "package.json").write_bytes(PACKAGE_JSON.replace(b"1.0.0", b"2.0.0"))
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    docker = tmp_path / "fake-docker"
    docker.write_text("stub")
    monkeypatch.setattr(rea, "NATIVE_FIXTURE_ROOT", workspace)
    monkeypatch.setattr(rea, "NATIVE_RUNTIME", runtime)
    monkeypatch.setattr(rea, "DOCKER_BIN", docker)
    monkeypatch.setattr(
        rea, "trusted_profile", lambda _runtime: {"node_image_id": "sha256:" + "a" * 64}
    )

    def forbidden_docker(*_args, **_kwargs):
        pytest.fail("Docker must not start for an unpinned REA fixture")

    monkeypatch.setattr(rea.subprocess, "Popen", forbidden_docker)
    with pytest.raises(rea.ReaStaticRefusal, match="rea_fixture_identity_mismatch"):
        rea.analyze_static_fixture()
    assert not list(workspace.glob("rea-mcp-*")), "Reject must remove private staged copy"
    assert (source / "index.js").is_file(), "Source remains unchanged"
