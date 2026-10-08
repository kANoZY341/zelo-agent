"""Explicit offline operator admission; no execution or Core registration."""

import argparse
import json

from qualification.rea_target_admission import AdmissionRefusal, admit_target, verify_target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", nargs="?", help="One folder name in the fixed private inbox")
    parser.add_argument("--allow-offline-target-admission", action="store_true")
    parser.add_argument("--verify", metavar="SNAPSHOT_ID")
    parser.add_argument("--receipt-sha256")
    args = parser.parse_args()
    if not args.allow_offline_target_admission:
        print(json.dumps({"status": "refused", "reason": "explicit_opt_in_required"}))
        return 2
    try:
        if args.verify and args.receipt_sha256 and args.folder is None:
            result = verify_target(args.verify, args.receipt_sha256)
        elif args.folder and args.verify is None and args.receipt_sha256 is None:
            result = admit_target(args.folder, operator_opt_in=True)
        else:
            raise AdmissionRefusal("invalid_arguments")
    except AdmissionRefusal as exc:
        print(json.dumps({"status": "refused", "reason": str(exc)}))
        return 1
    print(
        json.dumps(
            {"status": "verified_inert" if args.verify else "admitted_inert", "receipt": result}
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
