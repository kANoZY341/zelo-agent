#!/usr/bin/env python3
"""Append one exact native-reader attempt-2 recovery grant; no qualification."""

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qualification import media_reader_container as container  # noqa: E402
from qualification import media_reader_extra_attempt as extra  # noqa: E402
from qualification import media_reader_setup as setup  # noqa: E402
from scripts import qualify_media_native_reader as reader  # noqa: E402


def _observe(runtime: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="native-reader-extra-check-", dir=runtime) as raw:
        config = Path(raw)
        os.chmod(config, 0o700)
        environment = {"DOCKER_CONFIG": str(config)}

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
                    raise ValueError("reader_native_extra_resources")

        require_no_owned_resources()
        reader._docker_readiness(runtime)
        require_no_owned_resources()
        if list(config.iterdir()):
            raise ValueError("reader_native_extra_config")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--grant-once", action="store_true", required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    args = parser.parse_args()
    try:
        record = extra.grant_once(args.runtime_root, _observe)
        raw = (Path(record["runtime_root"]) / extra.BASE / extra.GRANT).read_bytes()
        extra.validate(args.runtime_root)
    except Exception:
        print("reader_native_extra_refused", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "status": "one-additional-native-attempt-granted",
                "grant_sha256": hashlib.sha256(raw).hexdigest(),
                "attempts_used_before": 2,
                "attempt_limit_after": 3,
                "runtime_authority": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    reader._operator_limits()
    raise SystemExit(main())
