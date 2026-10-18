"""No-network, no-install integrity gates for the full REA source import."""

import pytest

from qualification.rea_source_vendor import (
    EXPECTED_COMMIT,
    EXPECTED_GITLINKS,
    EXPECTED_TRACKED_FILES,
    ReaVendorRefusal,
    _git_blob_digest,
    _safe_relative_path,
    verify_vendored_rea_source,
)

pytestmark = pytest.mark.xdist_group("rea_vendor_inventory")


def test_full_vendored_rea_release_has_exact_source_provenance():
    receipt = verify_vendored_rea_source()
    assert receipt.upstream_commit == EXPECTED_COMMIT
    assert receipt.source_tag == "rea-agents-6.0.0"
    assert receipt.verified_files == EXPECTED_TRACKED_FILES
    assert receipt.optional_engine_gitlinks == len(EXPECTED_GITLINKS)
    assert 0 < receipt.total_bytes < 30 * 1024 * 1024
    assert receipt.execution_enabled is False


@pytest.mark.parametrize(
    "path",
    [
        "",
        "../escape",
        "src/../escape",
        "/etc/passwd",
        "src//test",
        "src/./test",
        ".git/config",
        "src/.git/data",
        "src\\windows",
        "a\x00b",
    ],
)
def test_vendored_inventory_refuses_path_escape(path):
    with pytest.raises(ReaVendorRefusal, match="rea_vendor_path_invalid"):
        _safe_relative_path(path)


def test_git_blob_checks_exact_bytes_without_executing_code():
    assert _git_blob_digest(b"hello\n") == "ce013625030ba8dba906f756967f9e9ca394464a"


def test_oversized_source_file_refused_before_reading(tmp_path):
    from qualification.rea_source_vendor import _MAX_FILE_BYTES, _read_bounded_regular_file

    oversized = tmp_path / "oversized.js"
    with oversized.open("wb") as stream:
        stream.truncate(_MAX_FILE_BYTES + 1)
    with pytest.raises(ReaVendorRefusal, match="rea_vendor_byte_limit"):
        _read_bounded_regular_file(oversized, _MAX_FILE_BYTES * 2)


def test_smaller_remaining_budget_refused_before_reading(tmp_path):
    from qualification.rea_source_vendor import _read_bounded_regular_file

    path = tmp_path / "little.js"
    path.write_bytes(b"0123456789")
    with pytest.raises(ReaVendorRefusal, match="rea_vendor_byte_limit"):
        _read_bounded_regular_file(path, 9)


def test_extra_fifo_cannot_hide_outside_source_inventory():
    import os

    from qualification.rea_source_vendor import VENDOR_ROOT

    fifo = VENDOR_ROOT / "_zelo_qualification_fifo"
    assert not fifo.exists()
    os.mkfifo(fifo, mode=0o600)
    try:
        with pytest.raises(ReaVendorRefusal, match="rea_vendor_special_entry_forbidden"):
            verify_vendored_rea_source()
    finally:
        fifo.unlink()


def test_extra_symlink_cannot_escape_source_inventory(tmp_path):
    from qualification.rea_source_vendor import VENDOR_ROOT

    alias = VENDOR_ROOT / "_zelo_qualification_symlink"
    assert not alias.is_symlink()
    alias.symlink_to(tmp_path, target_is_directory=True)
    try:
        with pytest.raises(ReaVendorRefusal, match="rea_vendor_symlink_forbidden"):
            verify_vendored_rea_source()
    finally:
        alias.unlink()


def test_oversized_manifest_refuses_before_json_load(tmp_path):
    from qualification.rea_source_vendor import _MAX_MANIFEST_BYTES

    manifest = tmp_path / "untrusted-manifest.json"
    with manifest.open("wb") as stream:
        stream.truncate(_MAX_MANIFEST_BYTES + 1)
    with pytest.raises(ReaVendorRefusal, match="rea_vendor_byte_limit"):
        verify_vendored_rea_source(manifest_path=manifest)


def test_untracked_empty_directory_cannot_hide_from_exact_inventory():
    from qualification.rea_source_vendor import VENDOR_ROOT

    new_dir = VENDOR_ROOT / "_zelo_unlisted_empty_directory"
    assert not new_dir.exists()
    new_dir.mkdir(mode=0o700)
    try:
        with pytest.raises(ReaVendorRefusal, match="rea_vendor_directory_inventory_mismatch"):
            verify_vendored_rea_source()
    finally:
        new_dir.rmdir()


def test_fixed_rea_v6_source_owned_mcp_catalog_is_inert():
    import hashlib
    import json
    import re

    from qualification.rea_source_vendor import EXPECTED_COMMIT, ROOT

    catalog_path = ROOT / "integrations" / "rea" / "tool-catalog-v6.0.0.json"
    raw = catalog_path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == (
        "82fb67571758c177d1eecac72efac152772cccc753442a77ef9418edc69be6a0"
    )
    catalog = json.loads(raw)
    assert catalog["schema"] == "zelo-rea-mcp-tool-inventory-v1"
    assert catalog["upstream_source_commit"] == EXPECTED_COMMIT
    assert catalog["upstream_source_tag"] == "rea-agents-6.0.0"
    assert catalog["mcp_server"] == {"name": "rea", "version": "6.0.0"}
    assert catalog["mcp_protocol"] == "2025-11-25"
    assert catalog["source_build"] is True
    assert catalog["observed_no_network"] is True
    assert catalog["execution_enabled"] is False
    assert catalog["zelo_tool_grants"] == []
    names = catalog["tool_names"]
    assert catalog["tool_count"] == len(names) == 139
    assert names == sorted(set(names))
    assert all(
        isinstance(name, str) and re.fullmatch(r"[a-z][a-z0-9_]{1,79}", name) for name in names
    )
    for expected in (
        "open_binary",
        "analyze_javascript_application",
        "capture_process_scenario",
        "inspect_android_package",
        "inspect_web_page",
        "extract_firmware",
        "export_evidence_bundle",
        "verify_reconstruction",
        "set_comment",
    ):
        assert expected in names
