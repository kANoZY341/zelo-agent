"""Pinned source snapshots must survive Git's Windows-style checkout conversion."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def git(*args: str, cwd: Path) -> None:
    hooks = cwd / ".empty-hooks"
    hooks.mkdir(exist_ok=True)
    subprocess.run(
        [
            "git",
            "-c",
            "core.autocrlf=true",
            "-c",
            "commit.gpgsign=false",
            "-c",
            f"core.hooksPath={hooks}",
            *args,
        ],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


class PinnedSourceCheckoutBytesTest(unittest.TestCase):
    def test_autocrlf_checkout_preserves_manifest_and_pinned_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            source.mkdir()
            shutil.copyfile(ROOT / ".gitattributes", source / ".gitattributes")
            curated = "integrations/community/sources"
            schema_path = f"{curated}/archify/archify/schemas/common.schema.json"
            expected = {
                "integrations/community/sources.json": b'{"sources": []}\n',
                f"{curated}/archify/LICENSE": b"MIT License\nline two\n",
                schema_path: b'{"title": "schema"}\n',
                "integrations/rea/source-v6.0.0.manifest.json": b'{"files": []}\n',
                "third_party/rea/LICENSE": b"Apache License\nline two\n",
            }
            for name, data in expected.items():
                target = source / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            git("init", "--quiet", cwd=source)
            git("add", ".", cwd=source)
            git(
                "-c",
                "user.name=Zelo Test",
                "-c",
                "user.email=zelo-test@example.invalid",
                "commit",
                "--quiet",
                "-m",
                "fixture",
                cwd=source,
            )
            checkout = root / "checkout"
            git("clone", "--quiet", str(source), str(checkout), cwd=root)
            for name, data in expected.items():
                self.assertEqual((checkout / name).read_bytes(), data, name)


if __name__ == "__main__":
    unittest.main()
