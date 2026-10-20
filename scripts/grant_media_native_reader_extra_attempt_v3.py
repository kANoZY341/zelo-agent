#!/usr/bin/env python3
"""Retained v3 grant proposal; standalone grant is disabled pending approval."""

import argparse
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qualification import media_reader_container as container  # noqa: E402
from qualification import media_reader_setup as setup  # noqa: E402
from scripts import qualify_media_native_reader as reader  # noqa: E402

CONFIRMATION = "GRANT_ONE_FIFTH_SYNTHETIC_READER_ATTEMPT_AFTER_APPROVAL"


def _observe(runtime: Path) -> None:
    """Require no retained-lease resources before and after Docker readiness."""
    with tempfile.TemporaryDirectory(prefix="native-reader-extra-v3-check-", dir=runtime) as raw:
        config = Path(raw)
        os.chmod(config, 0o700)
        environment = reader._docker_environment(config)

        def require_no_owned_resources() -> None:
            for kind in ("container", "image"):
                prefix = ("container", "ls", "--all") if kind == "container" else ("image", "ls")
                output = reader._docker(
                    (
                        setup.DOCKER,
                        "--host=unix:///var/run/docker.sock",
                        *prefix,
                        "--no-trunc",
                        "--filter",
                        f"label={container.LEASE_LABEL}",
                        "--format",
                        "{{.ID}}",
                    ),
                    environment,
                    stdout_limit=0,
                )
                if output:
                    raise ValueError("reader_native_extra_v3_resources")

        require_no_owned_resources()
        reader._docker_readiness(runtime)
        require_no_owned_resources()
        if list(config.iterdir()):
            raise ValueError("reader_native_extra_v3_config")


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
