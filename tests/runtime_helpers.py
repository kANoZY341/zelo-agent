"""Helpers for tests that need isolated, policy-compliant runtime storage."""

import hashlib
import uuid
from pathlib import Path

from app.config import ROOT


def isolated_runtime_dir(tmp_path: Path) -> Path:
    """Return a unique runtime directory inside the repository runtime root."""
    source = f"{tmp_path.resolve()}:{uuid.uuid4()}"
    identity = hashlib.sha256(source.encode()).hexdigest()
    runtime_dir = ROOT / "runtime" / "pytest" / identity
    runtime_dir.mkdir(parents=True)
    return runtime_dir
