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
