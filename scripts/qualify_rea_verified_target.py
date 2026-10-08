"""Explicit local-only REA static analysis proof for one sealed target snapshot."""

import argparse
import json

from qualification.rea_static_mcp import ReaStaticRefusal
from qualification.rea_target_admission import AdmissionRefusal
from qualification.rea_verified_target_static import analyze_verified_static_target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-native-rea-target-probe", action="store_true")
    parser.add_argument("--snapshot-id")
    parser.add_argument("--receipt-sha256")
    args = parser.parse_args()
    if not args.allow_native_rea_target_probe:
        print(json.dumps({"status": "refused", "reason": "explicit_opt_in_required"}))
        return 2
    try:
        receipt = analyze_verified_static_target(
            args.snapshot_id,
            args.receipt_sha256,
            operator_opt_in=True,
        )
    except (ReaStaticRefusal, AdmissionRefusal, OSError, ValueError):
        # No exceptions, paths, target contents or untrusted MCP text leak.
        print(json.dumps({"status": "refused", "reason": "rea_manual_target_qualification_failed"}))
        return 1
    print(json.dumps({"status": "qualified_manual_offline_only", "receipt": receipt.snapshot()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
