"""Explicit opt-in CLI for four synthetic Open Code Review local model cases.

Only Zelo's existing locally attested Ollama provider can be constructed.
No HTTP listener, background action, Task, credential, file or PR writes.
"""

import argparse
import asyncio
import os
import sys

from app.community_review_local_model import (
    OcrLocalModelRefusal,
    PinnedLocalOcrProvider,
    probe_local_ocr_model,
)


def _args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Observe OCR outputs on four Zelo-authored synthetic code cases"
    )
    parser.add_argument("--model", required=True, help="Already-installed local Ollama model")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument(
        "--allow-local-ocr-probe",
        action="store_true",
        help="Explicitly permit four fixture prompts to local Ollama",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    options = _args(argv)
    if not options.allow_local_ocr_probe:
        print("ocr_local_probe_requires_explicit_opt_in", file=sys.stderr)
        return 2

    # Restrict inherited proxy variables inside this CLI process only.
    # This does not prove daemon-level offline/network isolation.
    for key in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        os.environ.pop(key, None)
    provider = PinnedLocalOcrProvider()
    try:
        report = asyncio.run(
            probe_local_ocr_model(
                provider,
                model_id=options.model,
                timeout_per_case=options.timeout,
            )
        )
    except OcrLocalModelRefusal as error:
        print(str(error), file=sys.stderr)
        return 1
    print(report.model_dump_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
