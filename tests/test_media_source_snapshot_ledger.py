"""One-use Stage S retention and byte ceilings without network or GPG."""

import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from qualification import media_source_snapshot_ledger as ledger


class StageSLedgerTests(unittest.TestCase):
    def test_allocated_attempt_consumes_one_use_even_without_receipt(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root) / "proofs"
            with patch.object(ledger, "_sync_directory", wraps=ledger._sync_directory) as sync:
                path = ledger.allocate_attempt(base)
            self.assertEqual([call.args[0] for call in sync.call_args_list], [base.parent, base])
            self.assertEqual(path.parent, base)
            self.assertEqual(path.stat().st_mode & 0o777, 0o700)
            with self.assertRaises(ledger.LedgerRefused):
                ledger.allocate_attempt(base)

    def test_two_exclusive_bodies_are_bounded_and_read_back(self):
        with tempfile.TemporaryDirectory() as root:
            attempt = ledger.StageSLedger(ledger.allocate_attempt(Path(root) / "proofs"))
            fd = attempt.open_body("InRelease", 524288)
            try:
                attempt.write_body("InRelease", fd, b"signed")
                self.assertEqual(
                    attempt.finish_body("InRelease", fd, 6, hashlib.sha256(b"signed").hexdigest()),
                    b"signed",
                )
                with self.assertRaises(ledger.LedgerRefused):
                    attempt.open_body("InRelease", 524288)
            finally:
                os.close(fd)

    def test_short_or_oversized_body_never_finishes(self):
        with tempfile.TemporaryDirectory() as root:
            attempt = ledger.StageSLedger(ledger.allocate_attempt(Path(root) / "proofs"))
            fd = attempt.open_body("Sources.xz", 4194304)
            try:
                attempt.write_body("Sources.xz", fd, b"abc")
                with self.assertRaises(ledger.LedgerRefused):
                    attempt.finish_body("Sources.xz", fd, 4, hashlib.sha256(b"abc").hexdigest())
                with self.assertRaises(ledger.LedgerRefused):
                    attempt.write_body("Sources.xz", fd, b"x" * 4194304)
            finally:
                os.close(fd)

    def test_directory_sync_failure_preserves_unknown_open_descriptor(self):
        with tempfile.TemporaryDirectory() as root:
            attempt = ledger.StageSLedger(ledger.allocate_attempt(Path(root) / "proofs"))
            prior = len(ledger._QUARANTINED_FDS)
            with patch.object(ledger, "_sync_directory", side_effect=OSError("sync failed")):
                with self.assertRaises(OSError):
                    attempt.open_body("InRelease", 524288)
            self.assertEqual(len(ledger._QUARANTINED_FDS), prior + 1)
            fd = ledger._QUARANTINED_FDS.pop()
            try:
                self.assertEqual(os.fstat(fd).st_size, 0)
                with self.assertRaises(ledger.LedgerRefused):
                    attempt.open_body("InRelease", 524288)
            finally:
                os.close(fd)

    def test_journal_has_separate_eight_file_one_megabyte_budget(self):
        with tempfile.TemporaryDirectory() as root:
            attempt = ledger.StageSLedger(ledger.allocate_attempt(Path(root) / "proofs"))
            attempt.save_journal("policy.json", b"{}\n")
            self.assertEqual((attempt.path / "policy.json").read_bytes(), b"{}\n")
            with self.assertRaises(ledger.LedgerRefused):
                attempt.save_journal("policy.json", b"{}\n")
            with self.assertRaises(ledger.LedgerRefused):
                attempt.save_journal("unknown.json", b"{}\n")
            with self.assertRaises(ledger.LedgerRefused):
                attempt.save_journal("manifest.json", b"x" * 1048577)

    def test_failed_body_can_be_synced_and_identified_without_accepting_it(self):
        with tempfile.TemporaryDirectory() as root:
            attempt = ledger.StageSLedger(ledger.allocate_attempt(Path(root) / "proofs"))
            fd = attempt.open_body("InRelease", 524288)
            try:
                attempt.write_body("InRelease", fd, b"partial")
                record = attempt.persist_partial("InRelease", fd)
                self.assertEqual(
                    record,
                    {
                        "name": "InRelease",
                        "bytes": 7,
                        "sha256": hashlib.sha256(b"partial").hexdigest(),
                        "accepted": False,
                    },
                )
                self.assertEqual((attempt.path / "InRelease").read_bytes(), b"partial")
            finally:
                os.close(fd)


if __name__ == "__main__":
    unittest.main()
