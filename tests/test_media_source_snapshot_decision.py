"""Offline decision binding; no snapshot request or host admission."""

import hashlib
import json
import os
import py_compile
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from qualification import media_source_snapshot_decision as decision


class SnapshotDecisionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "decision.json"
        self.now = datetime(2026, 10, 30, 21, 0, tzinfo=UTC)
        self.urls = ("https://snapshot.ubuntu.com/one", "https://snapshot.ubuntu.com/two")
        self.caps = (524288, 4194304)
        self.value = {
            "schema": "zelo-media-source-snapshot-stage-s-decision-v1",
            "decision": "authorize-one-stage-s-source-snapshot-input",
            "source_head": "a" * 40,
            "source_tree": "b" * 40,
            "policy_digest": "c" * 64,
            "host_root": "/home/zelo/zelo",
            "urls": list(self.urls),
            "body_caps": list(self.caps),
            "additional_attempts": 1,
            "runtime_authority": False,
            "issued_utc": "2026-10-30T20:59:00Z",
            "expires_utc": "2026-10-30T21:09:00Z",
        }

    def _write(self, raw=None):
        raw = raw if raw is not None else (json.dumps(self.value, sort_keys=True) + "\n").encode()
        self.path.write_bytes(raw)
        os.chmod(self.path, 0o600)
        return raw

    def _validate(self, raw, *, now=None):
        with (
            patch.object(decision, "DECISION_PATH", self.path),
            patch.object(decision, "source_identity", return_value=("a" * 40, "b" * 40)),
        ):
            return decision.validate(
                path=self.path,
                expected_sha256=hashlib.sha256(raw).hexdigest(),
                root=Path(self.temporary.name),
                policy_digest="c" * 64,
                urls=self.urls,
                caps=self.caps,
                now=self.now if now is None else now,
            )

    def test_exact_private_decision_is_retained_as_bytes(self):
        raw = self._write()
        self.assertEqual(self._validate(raw), raw)

    def test_expired_changed_or_wrong_source_refuses(self):
        raw = self._write()
        with self.assertRaises(decision.DecisionRefused):
            self._validate(raw, now=self.now + timedelta(minutes=10))
        self.path.write_bytes(raw + b"x")
        with self.assertRaises(decision.DecisionRefused):
            self._validate(raw)
        self.value["source_tree"] = "d" * 40
        changed = self._write()
        with self.assertRaises(decision.DecisionRefused):
            self._validate(changed)

    def test_duplicate_fields_and_nonprivate_file_refuse(self):
        raw = self._write(b'{"schema":1,"schema":2}\n')
        with self.assertRaises(decision.DecisionRefused):
            self._validate(raw)
        raw = self._write()
        os.chmod(self.path, 0o644)
        with self.assertRaises(decision.DecisionRefused):
            self._validate(raw)

    def test_skip_worktree_cannot_hide_changed_executable_bytes(self):
        root = Path(self.temporary.name) / "repo"
        root.mkdir()

        def git(*arguments):
            return subprocess.run(
                ["/usr/bin/git", *arguments], cwd=root, check=True, capture_output=True
            ).stdout

        git("init", "-q")
        source = root / "operator.py"
        source.write_text("print('reviewed')\n")
        git("add", "operator.py")
        git(
            "-c",
            "user.name=Reviewer",
            "-c",
            "user.email=reviewer@example.test",
            "commit",
            "-qm",
            "pin",
        )
        self.assertEqual(len(decision.source_identity(root)), 2)
        git("update-index", "--skip-worktree", "operator.py")
        source.write_text("print('changed!')\n")
        self.assertEqual(git("status", "--porcelain", "--untracked-files=no"), b"")
        with self.assertRaises(decision.DecisionRefused, msg="skip-worktree must not hide code"):
            decision.source_identity(root)

    def test_fresh_cache_prefix_ignores_timestamp_matched_bytecode(self):
        root = Path(self.temporary.name) / "imports"
        root.mkdir()
        source = root / "module.py"
        source.write_text("VALUE = 'evil'\n")
        stamp = 1700000000
        os.utime(source, (stamp, stamp))
        py_compile.compile(str(source), doraise=True)
        source.write_text("VALUE = 'safe'\n")
        os.utime(source, (stamp, stamp))
        ordinary = subprocess.run(
            [
                sys.executable,
                "-I",
                "-c",
                (
                    f"import sys; sys.path.insert(0, {str(root)!r}); "
                    "import module; print(module.VALUE)"
                ),
            ],
            check=True,
            capture_output=True,
        )
        self.assertEqual(ordinary.stdout, b"evil\n")
        isolated = subprocess.run(
            [
                sys.executable,
                "-I",
                "-c",
                (
                    f"import sys; sys.pycache_prefix={str(root / 'fresh-cache')!r}; "
                    f"sys.dont_write_bytecode=True; sys.path.insert(0, {str(root)!r}); "
                    "import module; print(module.VALUE)"
                ),
            ],
            check=True,
            capture_output=True,
        )
        self.assertEqual(isolated.stdout, b"safe\n")


if __name__ == "__main__":
    unittest.main()
