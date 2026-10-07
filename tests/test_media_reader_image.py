"""Offline deterministic reader-image manifest tests; no Docker daemon."""

import copy
import hashlib
import json
from pathlib import Path

import pytest

from qualification import media_reader_container as container
from qualification import media_reader_image as m

ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = ROOT / "qualification/fixtures/media-reader-elf-target-lock.json"


def lock_raw():
    return LOCK_PATH.read_bytes()


def lock():
    return m.load_lock(lock_raw())


def test_exact_lock_builds_eight_file_manifest():
    value = m.manifest(lock())
    assert len(value.files) == 8
    assert sum(row.bytes for row in value.files) == 3718288
    assert [row.destination for row in value.files] == list(m.DESTINATIONS)
    assert [row.mode for row in value.files] == list(m.MODES)
    assert len(value.digest) == 64


def test_manifest_digest_is_independent_canonical_metadata_hash():
    value = m.manifest(lock())
    canonical = [
        {
            "source": row.source,
            "destination": row.destination,
            "bytes": row.bytes,
            "mode": row.mode,
            "sha256": row.sha256,
        }
        for row in value.files
    ]
    expected = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert value.digest == expected


@pytest.mark.parametrize("index", range(8))
def test_lock_order_drift_refused(index):
    value = copy.deepcopy(lock())
    value["files"][index]["relative"] = "wrong"
    with pytest.raises(m.Refused, match="reader_image_lock_order"):
        m.manifest(value)


def test_pinned_lock_tamper_refused_before_parse(monkeypatch):
    raw = bytearray(lock_raw())
    raw[-2] ^= 1
    with pytest.raises(m.Refused, match="reader_image_lock"):
        m.load_lock(bytes(raw))


def test_dockerfile_is_inert_scratch_only():
    value = m.manifest(lock())
    raw = m.dockerfile(value)
    text = raw.decode()
    assert text.startswith("FROM scratch\n")
    assert text.count("COPY --chown=0:0") == 8
    assert text.count("LABEL ") == 1
    assert container.BUNDLE_LABEL in text and value.digest in text
    forbidden = (
        "RUN ",
        "ADD ",
        "ENTRYPOINT",
        "CMD ",
        "ENV ",
        "USER ",
        "WORKDIR",
        "VOLUME",
        "EXPOSE",
        "HEALTHCHECK",
        "ONBUILD",
        "http://",
        "https://",
    )
    assert not any(token in text for token in forbidden)


def test_dockerfile_uses_legacy_compatible_copy_with_fixed_destinations():
    value = m.manifest(lock())
    text = m.dockerfile(value).decode()
    assert "--chmod" not in text
    for row in value.files:
        assert f"COPY --chown=0:0 {row.source} /{row.destination}" in text


def test_expected_export_is_exact_and_no_notice_or_helper_file():
    value = m.manifest(lock())
    result = m.expected_export(value)
    assert set(result) == set(m.DESTINATIONS)
    assert all(not name.startswith("/") for name in result)
    assert all("notice" not in name and "Dockerfile" not in name for name in result)
    for row in value.files:
        assert result[row.destination] == (row.bytes, row.mode, row.sha256)


def test_container_contract_binds_manifest_digest_only():
    value = m.manifest(lock())
    result = m.contract("sha256:" + "a" * 64, value)
    assert result.bundle_sha256 == value.digest
    assert result.bootstrap_proof_sha256 == container.BOOTSTRAP_PROOF_SHA256
    assert result.bootstrap_lock_sha256 == container.BOOTSTRAP_LOCK_SHA256


@pytest.mark.parametrize("image", ["bad", "a" * 64, "sha256:" + "A" * 64])
def test_bad_image_identity_refused(image):
    with pytest.raises(m.Refused, match="reader_image_contract"):
        m.contract(image, m.manifest(lock()))


def test_no_operation_or_docker_import_on_module_import():
    source = Path(m.__file__).read_text()
    for token in ("subprocess", "docker.sock", "asyncio", "socket.", "Popen(", "os.system"):
        assert token not in source
