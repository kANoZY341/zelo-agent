"""Owned image-setup primitives for the synthetic Media reader gate.

Pure command/configuration helpers only. No Docker or subprocess action on import.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from qualification import media_reader_container as container
from qualification import media_reader_image as image

DOCKER = "/usr/bin/docker"
IMAGE_LABEL = container.LEASE_LABEL
SETUP_PREFIX = "zelo-media-reader-setup-"


class Refused(ValueError):
    """Fixed setup refusal."""


def _require(condition, code):
    if not condition:
        raise Refused("reader_setup_" + code)


def _identity(value):
    _require(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None, "identity")
    return value


@dataclass(frozen=True)
class BuildPlan:
    identity: str
    bundle_sha256: str
    context: Path
    dockerfile: Path
    iidfile: Path
    setup_name: str

    def validate(self):
        _identity(self.identity)
        _require(
            type(self.bundle_sha256) is str
            and re.fullmatch(r"[0-9a-f]{64}", self.bundle_sha256) is not None,
            "bundle",
        )
        for path in (self.context, self.dockerfile, self.iidfile):
            _require(isinstance(path, Path) and path.is_absolute(), "path")
        _require(
            self.setup_name == SETUP_PREFIX + self.identity,
            "name",
        )


def plan(root: Path, identity: str, manifest: image.ImageManifest) -> BuildPlan:
    _require(isinstance(root, Path) and root.is_absolute(), "root")
    _identity(identity)
    _require(type(manifest) is image.ImageManifest, "manifest")
    context = root / "build"
    return BuildPlan(
        identity=identity,
        bundle_sha256=manifest.digest,
        context=context,
        dockerfile=context / "Dockerfile",
        iidfile=root / "image-id",
        setup_name=SETUP_PREFIX + identity,
    )


def build_arguments(value: BuildPlan) -> tuple[str, ...]:
    value.validate()
    return (
        DOCKER,
        "--host=unix:///var/run/docker.sock",
        "build",
        "--network=none",
        "--pull=false",
        "--label",
        f"{container.LEASE_LABEL}={value.identity}",
        "--label",
        f"{container.BUNDLE_LABEL}={value.bundle_sha256}",
        "--iidfile",
        str(value.iidfile),
        "-f",
        str(value.dockerfile),
        str(value.context),
    )


def setup_create_arguments(value: BuildPlan, image_identity: str) -> tuple[str, ...]:
    value.validate()
    _require(
        type(image_identity) is str
        and re.fullmatch(r"sha256:[0-9a-f]{64}", image_identity) is not None,
        "image",
    )
    return (
        DOCKER,
        "--host=unix:///var/run/docker.sock",
        "create",
        "--name",
        value.setup_name,
        "--label",
        f"{container.LEASE_LABEL}={value.identity}",
        "--label",
        f"{container.BUNDLE_LABEL}={value.bundle_sha256}",
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--restart=no",
        image_identity,
    )


def discovery_arguments(kind: str, identity: str) -> tuple[str, ...]:
    _identity(identity)
    _require(kind in ("container", "image"), "kind")
    prefix = ("container", "ls", "--all") if kind == "container" else ("image", "ls")
    return (
        DOCKER,
        "--host=unix:///var/run/docker.sock",
        *prefix,
        "--no-trunc",
        "--filter",
        f"label={container.LEASE_LABEL}={identity}",
        "--format",
        "{{.ID}}",
    )


def parse_discovery(kind: str, raw: bytes) -> tuple[str, ...]:
    _require(kind in ("container", "image"), "kind")
    _require(type(raw) is bytes and len(raw) <= 131072, "discovery")
    try:
        rows = raw.decode("ascii", "strict").splitlines()
    except UnicodeError:
        raise Refused("reader_setup_discovery") from None
    _require(len(rows) <= 1, "discovery")
    pattern = r"[0-9a-f]{64}" if kind == "container" else r"sha256:[0-9a-f]{64}"
    _require(all(re.fullmatch(pattern, row) is not None for row in rows), "discovery")
    _require(len(rows) == len(set(rows)), "discovery")
    return tuple(rows)


def verify_owned_image(data, contract: container.ReaderContainerContract, identity: str):
    _identity(identity)
    container.verify_image(data, contract, identity)


def verify_owned_setup_container(data, value: BuildPlan, image_identity: str) -> None:
    value.validate()
    _require(type(data) is dict, "container")
    labels = data.get("Config", {}).get("Labels")
    _require(
        data.get("Name") == "/" + value.setup_name
        and data.get("Image") == image_identity
        and labels
        == {
            container.LEASE_LABEL: value.identity,
            container.BUNDLE_LABEL: value.bundle_sha256,
        }
        and data.get("State", {}).get("Running") is False
        and not data.get("State", {}).get("Pid"),
        "container",
    )


def expected_export(manifest: image.ImageManifest):
    return image.expected_export(manifest)
