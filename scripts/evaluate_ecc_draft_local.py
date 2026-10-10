"""Explicitly opted-in local-only ECC draft model probe.

Run from the trusted Zelo checkout. This module does not create Tasks, persist
user data, register Tools or activate Skills. All model I/O stays on loopback.
"""

import argparse
import asyncio
import os
import sys

from app.community_ecc_model_eval import (
    EccLocalModelRefusal,
    PinnedLocalOllamaProvider,
    probe_local_ecc_model,
)

_LOCAL_OLLAMA_URL = "http://127.0.0.1:11434"


def _arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate an ECC draft using 3 local-only fixed cases"
    )
    parser.add_argument(
        "--skill-id",
        required=True,
        choices=(
            "ecc_security_review",
            "ecc_agent_evaluation",
            "ecc_ai_regression",
            "ecc_architecture_decisions",
        ),
    )
    parser.add_argument("--model", required=True, help="An already installed local Ollama model")
    parser.add_argument(
        "--timeout", type=float, default=20.0, help="Seconds for each of 3 cases (2-45)"
    )
    parser.add_argument(
        "--allow-local-ecc-probe",
        action="store_true",
        help="Explicitly opt in to 3 non-authoritative prompts sent to local Ollama",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _arguments(argv)
    if not args.allow_local_ecc_probe:
        print("ecc_local_probe_requires_explicit_opt_in", file=sys.stderr)
        return 2

    # The model service must connect directly to loopback, never via inherited
    # enterprise/global HTTP proxy settings. Changes affect this CLI process only.
    for variable in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        os.environ.pop(variable, None)

    provider = PinnedLocalOllamaProvider()
    try:
        result = asyncio.run(
            probe_local_ecc_model(
                provider,
                skill_id=args.skill_id,
                model_id=args.model,
                timeout_per_case=args.timeout,
            )
        )
    except EccLocalModelRefusal as error:
        print(str(error), file=sys.stderr)
        return 1
    print(result.model_dump_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
