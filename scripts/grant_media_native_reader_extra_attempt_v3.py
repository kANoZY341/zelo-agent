#!/usr/bin/env python3
"""Retained v3 grant proposal; standalone grant is disabled pending approval."""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qualification.media_reader_resource_preflight_v3 import observe as _observe  # noqa: E402,F401
from scripts import qualify_media_native_reader as reader  # noqa: E402

CONFIRMATION = "GRANT_ONE_FIFTH_SYNTHETIC_READER_ATTEMPT_AFTER_APPROVAL"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--grant-once", action="store_true", required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--source-head", required=True)
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--operator-confirmation", required=True)
    args = parser.parse_args()
    if args.operator_confirmation != CONFIRMATION:
        print("reader_native_extra_v3_refused", file=sys.stderr)
        return 1
    # A fixed confirmation phrase is not a Managing Director decision. The
    # only permitted future route must bind live approval, durable consumption
    # and this same held admission lock to the native qualifier.
    print("reader_native_extra_v3_approval_unavailable", file=sys.stderr)
    return 1


if __name__ == "__main__":
    reader._operator_limits()
    raise SystemExit(main())
