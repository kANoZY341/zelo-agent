"""Read-only Docker lease-resource preflight for the proposed Media v3 admission."""

import os
import tempfile
from pathlib import Path

from qualification import media_reader_container as container
from qualification import media_reader_setup as setup


def observe(runtime: Path) -> None:
    """Require no retained-lease resources before and after Docker readiness."""
    from scripts import qualify_media_native_reader as reader

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
