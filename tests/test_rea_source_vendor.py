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
