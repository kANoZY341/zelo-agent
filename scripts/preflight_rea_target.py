"""Manually review a sealed REA target without invoking Core, Docker, or REA."""

import argparse
import json

from pydantic import ValidationError

from app.rea_tool_inventory import ReaCatalogRefusal
from qualification.rea_source_vendor import ReaVendorRefusal
from qualification.rea_target_admission import AdmissionRefusal
from qualification.rea_target_preflight import ReaPreflightInput, prepare_rea_target_preflight


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-offline-rea-preflight", action="store_true")
    parser.add_argument("--snapshot-id")
    parser.add_argument("--receipt-sha256")
    args = parser.parse_args()
    if not args.allow_offline_rea_preflight:
        print(json.dumps({"status": "refused", "reason": "explicit_opt_in_required"}))
        return 2
    try:
        request = ReaPreflightInput.model_validate(
            {"snapshot_id": args.snapshot_id, "receipt_sha256": args.receipt_sha256},
            strict=True,
        )
        report = prepare_rea_target_preflight(request, operator_opt_in=True)
    except (AdmissionRefusal, ReaCatalogRefusal, ReaVendorRefusal, ValidationError, ValueError):
        print(json.dumps({"status": "refused", "reason": "rea_preflight_unavailable"}))
        return 2
    print(json.dumps(report.model_dump(mode="json"), sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
