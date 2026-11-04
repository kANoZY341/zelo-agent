"""Offline quality receipt for one pinned historical OCR real-diff case.

Reads local raw reviewer JSON only. No model, Core Task, network or PR action.
"""

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from app.community_review import MAX_BYTES
from app.community_review_git_scope import CommunityGitScopeRefusal
from app.ocr_real_diff_quality import (
    OcrCommentAdjudication,
    OcrRealDiffRefusal,
    evaluate_pinned_ocr_case,
    pinned_ocr_real_diff_cases,
)


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise OcrRealDiffRefusal("ocr_adjudication_duplicate_json_key")
        result[key] = value
    return result


def _read(path: Path, limit: int) -> bytes:
    with path.open("rb") as source:
        data = source.read(limit + 1)
    if not data or len(data) > limit:
        raise OcrRealDiffRefusal("ocr_real_diff_input_size_invalid")
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--case", required=True, choices=[x.case_id for x in pinned_ocr_real_diff_cases()]
    )
    parser.add_argument(
        "--repo", required=True, type=Path, help="Disposable checkout at the pinned HEAD"
    )
    parser.add_argument("--raw-review", required=True, type=Path, help="Local raw OCR JSON file")
    parser.add_argument(
        "--adjudications", type=Path, help="Optional JSON array of manual comment labels"
    )
    options = parser.parse_args(argv)
    try:
        labels = ()
        if options.adjudications is not None:
            payload = json.loads(
                _read(options.adjudications, 65536).decode("utf-8", "strict"),
                object_pairs_hook=_unique_pairs,
            )
            if type(payload) is not list or len(payload) > 64:
                raise OcrRealDiffRefusal("ocr_adjudication_shape_invalid")
            labels = tuple(
                OcrCommentAdjudication.model_validate(item, strict=True) for item in payload
            )
        result = evaluate_pinned_ocr_case(
            _read(options.raw_review, MAX_BYTES),
            options.repo,
            case_id=options.case,
            adjudications=labels,
        )
    except (OSError, UnicodeError, ValueError, ValidationError, CommunityGitScopeRefusal) as error:
        # Do not print private paths, raw model output or Git diagnostics.
        code = (
            str(error)
            if isinstance(error, (OcrRealDiffRefusal, CommunityGitScopeRefusal))
            else "ocr_real_diff_input_invalid"
        )
        print(code, file=sys.stderr)
        return 1
    print(result.model_dump_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
