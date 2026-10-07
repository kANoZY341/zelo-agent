#!/usr/bin/env python3
"""Explicitly bind the retained native-reader refusal/reconciliation to a runtime path."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qualification import media_reader_scope as scope  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bind", action="store_true", required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    args = parser.parse_args()
    try:
        record = scope.bind_runtime_scope(args.runtime_root)
    except Exception:
        print("reader_native_scope_bind_refused", file=sys.stderr)
        return 1
    print(json.dumps({"status": "native-reader-runtime-scope-bound", **record}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
