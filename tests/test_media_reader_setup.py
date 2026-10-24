"""Offline Media reader image-setup tests; no Docker daemon is contacted."""

import copy
from pathlib import Path

import pytest

from qualification import media_reader_container as container
from qualification import media_reader_image as image
from qualification import media_reader_setup as m

LEASE = "c" * 64
IMAGE = "sha256:" + "a" * 64


def manifest():
    rows = tuple(
        image.ImageFile(
            source=f"{i:02d}.binary.reference",
            destination=dest,
            bytes=100 + i,
            mode=mode,
            sha256=f"{i + 1:064x}",
        )
        for i, (dest, mode) in enumerate(zip(image.DESTINATIONS, image.MODES, strict=True))
    )
    return image.ImageManifest(rows, "b" * 64)


def contract():
    return container.ReaderContainerContract(IMAGE, manifest().digest)


def build_plan(tmp_path):
    return m.plan(tmp_path.resolve(), LEASE, manifest())


def image_record():
    return {
        "Id": IMAGE,
        "Architecture": "amd64",
        "Os": "linux",
        "Config": {
            "Labels": {
                container.LEASE_LABEL: LEASE,
                container.BUNDLE_LABEL: manifest().digest,
            },
            "Entrypoint": None,
            "Cmd": None,
            "Env": ["PATH=/nonexistent"],
            "User": "",
            "WorkingDir": "",
            "Volumes": None,
            "ExposedPorts": None,
            "OnBuild": None,
            "Healthcheck": None,
        },
    }


def setup_record(value):
    return {
        "Name": "/" + value.setup_name,
        "Image": IMAGE,
        "Config": {
            "Labels": {
                container.LEASE_LABEL: LEASE,
                container.BUNDLE_LABEL: manifest().digest,
            }
        },
        "State": {"Running": False, "Pid": 0},
    }


def test_plan_and_build_command_are_exact(tmp_path):
    value = build_plan(tmp_path)
    value.validate()
    args = m.build_arguments(value)
    assert args[:3] == (m.DOCKER, "--host=unix:///var/run/docker.sock", "build")
    assert "--network=none" in args and "--pull=false" in args
    assert f"{container.LEASE_LABEL}={LEASE}" in args
    assert f"{container.BUNDLE_LABEL}={manifest().digest}" in args
    assert args[-1] == str(value.context)
    assert "--iidfile" in args


def test_setup_create_never_starts_or_mounts(tmp_path):
    args = m.setup_create_arguments(build_plan(tmp_path), IMAGE)
    assert args[2] == "create"
    assert "--network=none" in args and "--read-only" in args and "--cap-drop=ALL" in args
    assert "start" not in args and "--mount" not in args and "--volume" not in args
    assert args[-1] == IMAGE


@pytest.mark.parametrize("kind", ["container", "image"])
def test_discovery_is_exact_lease_only(kind):
    args = m.discovery_arguments(kind, LEASE)
    assert f"label={container.LEASE_LABEL}={LEASE}" in args
    assert "--no-trunc" in args


@pytest.mark.parametrize(
    "kind,raw",
    [
        ("container", ("d" * 64 + "\n").encode()),
        ("image", ("sha256:" + "e" * 64 + "\n").encode()),
    ],
)
def test_discovery_parses_one_owned_identity(kind, raw):
    assert m.parse_discovery(kind, raw) == (raw.decode().strip(),)


@pytest.mark.parametrize(
    "kind,raw",
    [
        ("container", b"short\n"),
        ("image", b"sha256:BAD\n"),
        ("container", (("d" * 64 + "\n") * 2).encode()),
        ("image", (("sha256:" + "e" * 64 + "\n") * 2).encode()),
        ("image", b"\xff"),
    ],
)
def test_discovery_refuses_ambiguous_identity(kind, raw):
    with pytest.raises(m.Refused, match="reader_setup_discovery"):
        m.parse_discovery(kind, raw)


def test_owned_image_requires_lease_label():
    m.verify_owned_image(image_record(), contract(), LEASE)
    bad = copy.deepcopy(image_record())
    bad["Config"]["Labels"].pop(container.LEASE_LABEL)
    with pytest.raises(container.Refused):
        m.verify_owned_image(bad, contract(), LEASE)


def test_setup_container_must_be_stopped_exact_owner(tmp_path):
    value = build_plan(tmp_path)
    m.verify_owned_setup_container(setup_record(value), value, IMAGE)
    for mutate in ("running", "label", "image", "name"):
        bad = copy.deepcopy(setup_record(value))
        if mutate == "running":
            bad["State"]["Running"] = True
        elif mutate == "label":
            bad["Config"]["Labels"][container.LEASE_LABEL] = "e" * 64
        elif mutate == "image":
            bad["Image"] = "sha256:" + "f" * 64
        else:
            bad["Name"] = "/foreign"
        with pytest.raises(m.Refused, match="reader_setup_container"):
            m.verify_owned_setup_container(bad, value, IMAGE)


def test_expected_export_is_exact_manifest():
    found = m.expected_export(manifest())
    assert set(found) == set(image.DESTINATIONS)
    for row in manifest().files:
        assert found[row.destination] == (row.bytes, row.mode, row.sha256)


@pytest.mark.parametrize("identity", ["c" * 63, "C" * 64, "x" * 64])
def test_bad_identity_refused(tmp_path, identity):
    with pytest.raises(m.Refused):
        m.plan(tmp_path.resolve(), identity, manifest())


def test_no_product_import():
    found = []
    for path in Path("app").rglob("*.py"):
        if "media_reader_setup" in path.read_text():
            found.append(path)
    assert found == []
