"""Explicit offline operator setup for a reusable, inspected browser image.

Never imported as a runtime build path. Requires local qualified source artifacts,
compiler and Docker; no pull, external network, activation or service installation.
"""

import argparse
import hashlib
import os
from pathlib import Path
from uuid import uuid4

from app.api_contracts import ApiEndpointRegistry
from app.browser_operator import canonical, contract_data, load_receipt, private_directory
from qualification.broker import Policy
from qualification.browser_seccomp import build_profile
from qualification.renderer_contracts import (
    CONTROLLER_DIGEST,
    RENDERER_DIGEST,
    PreparedImage,
    RendererContract,
    origins_digest,
)
from qualification.runtime_contracts import ExecutionPolicy, ResourcePolicy
from scripts.qualify_browser_renderer import (
    cleanup_owned_setup,
    expected_image_files,
    prepare_candidate,
)


def prepare(source: Path, expected_digest: str, destination: Path, endpoints: tuple[str, ...]):
    registry = ApiEndpointRegistry.from_entries(endpoints)
    if not registry.list() or not destination.is_absolute() or destination.resolve() != destination:
        raise ValueError("invalid_operator_destination_or_origins")
    # Exclusive directory: never overwrite an old receipt or share setup ownership.
    destination.mkdir(mode=0o700)
    private_directory(destination)
    run_id = uuid4().hex
    preserve = None
    try:
        image, bundle, manifest, proof = prepare_candidate(
            source,
            expected_digest,
            Path(__file__).resolve().parents[1] / "qualification/fixtures/browser_pipe.c",
            destination,
            run_id,
        )
        expected = expected_image_files(manifest, bundle / "browser-pipe")
        if (
            proof["image_identity"] != image
            or proof["manifest_digest"] != manifest.digest
            or proof["image_payload_digest"] != hashlib.sha256(canonical(expected)).hexdigest()
        ):
            raise ValueError("image_inspection_receipt_mismatch")
        contract = RendererContract(
            ExecutionPolicy(
                ResourcePolicy(536870912, 128, 50000, 100000, 0, 33554432, 30),
                image,
                next(f.sha256 for f in manifest.files if f.path == "browser/chrome-headless-shell"),
                CONTROLLER_DIGEST,
                Policy().digest,
                RENDERER_DIGEST,
                origins_digest(
                    registry, tuple(registry.bind(e.endpoint_id) for e in registry.list())
                ),
            ),
            manifest,
            build_profile(),
            PreparedImage(image, manifest.digest),
        )
        receipt = {
            "schema": "zelo-browser-operator-v1",
            "contract": contract_data(contract),
            "image_payload": expected,
            "image_payload_digest": proof["image_payload_digest"],
        }
        path = destination / "verified-receipt.json"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as output:
            output.write(canonical(receipt))
            output.flush()
            os.fsync(output.fileno())
        load_receipt(path, bundle, registry)
        (destination / "admission").mkdir(mode=0o700)
        (destination / "runtime").mkdir(mode=0o700)
        preserve = image
        return path
    finally:
        if cleanup_owned_setup("/usr/bin/docker", run_id, preserve_verified_image=preserve):
            raise RuntimeError("browser_preparation_cleanup_unproven")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-owned-image-preparation", action="store_true")
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--expected-bundle-digest", required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--endpoint", action="append", required=True)
    args = parser.parse_args()
    if not args.allow_owned_image_preparation:
        parser.error("Explicit --allow-owned-image-preparation required")
    try:
        path = prepare(
            args.bundle, args.expected_bundle_digest, args.destination, tuple(args.endpoint)
        )
    except Exception:
        parser.exit(1, "browser_preparation_failed; inspect owned setup state before retry\n")
    print(path)


if __name__ == "__main__":
    main()
