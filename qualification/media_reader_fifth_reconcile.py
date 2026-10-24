"""Read-only exact fifth-refusal inventory; never clears admission or grants a run."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from qualification import media_reader_extra_attempt as v1
from qualification import media_reader_extra_attempt_v2 as v2
from qualification import media_reader_extra_attempt_v3 as v3
from qualification import media_reader_reconcile as first
from qualification import media_reader_scope as scope

RETAINED_ROOT = Path("/home/zelo/zelo/core-v015-browser-renderer/runtime")
FIFTH = "native-reader-proof-5r1zlt2c"
LEASE = "0f0a2eb8690a26b99d65adf1ef8b4a17b90d8be5d646452de255d17cc2441f59"
SOURCE_HEAD = "f43c971e61d8bd257fbf131b2fb0d3f1e96c25a2"
SOURCE_TREE = "8c13ad2563e9ee948df0ac81991222dc429381c4"
MARKER_SHA256 = "95e17987033c2d55c0b47b57758f4f85678dbc6d861cc3a4df9fb37dc61883ef"
POLICY_SHA256 = "40934ab43f091c882c49590fba38c514f42be03fb6f5af7edf0cd980c1dbd77a"
PROOF_SHA256 = "5232d217874020fd88375cd265405586f838feb2d88a32bef18b826866af76e1"

# Exact retained bytes are evidence, including build leftovers; nothing is repaired.
FILE_PINS: dict[str, tuple[str, int, int]] = {
    "media-native-reader-scope.json": (v2.SCOPE_SHA256, 0o600, 65536),
    "media-native-reader-reconciliation/reconcile-1fudorw3/pending.json": (
        v2.RECONCILIATION_SHA256,
        0o600,
        65536,
    ),
    f"media-native-reader/{first.ATTEMPT_NAME}/policy.json": (first.POLICY_SHA256, 0o600, 2097152),
    f"media-native-reader/{first.ATTEMPT_NAME}/cases.json": (first.CASES_SHA256, 0o600, 2097152),
    f"media-native-reader/{v1.SECOND}/policy.json": (v1.SECOND_POLICY_SHA256, 0o600, 2097152),
    f"media-native-reader/{v1.SECOND}/cases.json": (v1.SECOND_PROOF_SHA256, 0o600, 2097152),
    f"media-native-reader/{v2.THIRD}/policy.json": (v2.THIRD_POLICY_SHA256, 0o600, 2097152),
    f"media-native-reader/{v2.THIRD}/cases.json": (v2.THIRD_PROOF_SHA256, 0o600, 2097152),
    f"media-native-reader/{v3.FOURTH}/policy.json": (v3.FOURTH_POLICY_SHA256, 0o600, 2097152),
    f"media-native-reader/{v3.FOURTH}/cases.json": (v3.FOURTH_PROOF_SHA256, 0o600, 2097152),
    "media-native-reader-extra-attempt/grant.json": (v2.V1_GRANT_SHA256, 0o600, 65536),
    "media-native-reader-extra-attempt-v2/grant.json": (v3.V2_GRANT_SHA256, 0o600, 65536),
    "media-native-reader-extra-attempt-v3/grant.json": (
        "68687b62b6762edc233b1e780016b14d41e80cc15d37e196ec660a4475748802",
        0o600,
        65536,
    ),
    "media-native-reader-extra-attempt-v3/seal.json": (
        "3aaf7e7eb24fa728b1ab749c778287886d60b42e838e227efa17afa906030124",
        0o600,
        4096,
    ),
    "media-native-reader-run-approval-v3/receipt.json": (
        "3d7c5f69565579e26c6d257161fbe47266b55cee0ede00a56c29d503b16b8d86",
        0o600,
        16384,
    ),
    "media-native-reader-run-approval-v3/recheck.json": (
        "d013ab30c7bb54b69449699840fd91767161f605cb6f63e9fecfc38be26e02e6",
        0o600,
        16384,
    ),
    "media-native-reader-run-approval-v3/attempt-recheck.json": (
        "b6eb3f753de698adc602d33df86d876db71c93f6e95f418745d7f825e6997d6b",
        0o600,
        16384,
    ),
    "media-elf-metadata-admission/active.marker": (MARKER_SHA256, 0o600, 4096),
    f"media-native-reader/{FIFTH}/policy.json": (POLICY_SHA256, 0o600, 2097152),
    f"media-native-reader/{FIFTH}/cases.json": (PROOF_SHA256, 0o600, 2097152),
    f"media-native-reader/{FIFTH}/image-id": (
        "1e4bf4917af7ea79f6077ce924cb75df826e389daf62394eccf20ef68ec3ea35",
        0o644,
        256,
    ),
    f"media-native-reader/{FIFTH}/build/Dockerfile": (
        "a097907d925d3c2ac2efe9984fc91fa83cbf7c9def98e973b5fb742e5a1b4df9",
        0o600,
        4096,
    ),
}
for name, digest, mode in (
    ("00", "fc9dde782ca8309b65ab92e61d13318bf463cd1aa71daa8967b87b81b48b63d2", 0o555),
    ("01", "3a15d66867d83762c7f2f1e37359cb8f6c5743edb369c65285cb0b1c4f7498bf", 0o444),
    ("02", "c20a2dc8917c755f02b94049356320fe1f62ac7d9f8994731f807d9df39302da", 0o555),
    ("03", "86200da370f20476a2507e9097a789b5ef97269b4ca8d5e164ad82dab9d99892", 0o444),
    ("04", "cc08c9f50a8009ffd6391e0116a100369b11ca9238fa52392c019e6645b122a1", 0o444),
    ("05", "696e868dd0700a19a6d65fc01608ec2d70d3cb91f65710e89180cd2e688f30cb", 0o444),
    ("06", "0a2128bc10841fb29e76d08d945864dfb0b6a66da5df6df5d8299197439e54bb", 0o444),
    ("07", "423e18586b6ea740f4465afd64f7a9a4cb7264ed979c8e7256f9085af5345fef", 0o444),
):
    FILE_PINS[f"media-native-reader/{FIFTH}/build/{name}.binary.reference"] = (
        digest,
        mode,
        4194304,
    )


class FifthReconciliationRefused(ValueError):
    """The retained fifth refusal does not match its fixed inventory."""


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise FifthReconciliationRefused("reader_fifth_reconcile_" + code)


def _children(path: Path, expected: set[str]) -> None:
    info = path.lstat()
    _require(
        stat.S_ISDIR(info.st_mode)
        and info.st_uid == os.geteuid()
        and info.st_gid == os.getegid()
        and stat.S_IMODE(info.st_mode) == 0o700,
        "directory",
    )
    _require({entry.name for entry in path.iterdir()} == expected, "inventory")


def _read_pinned(path: Path, digest: str, mode: int, bound: int) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        identity = (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_uid,
            before.st_gid,
            before.st_nlink,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        _require(
            stat.S_ISREG(before.st_mode)
            and before.st_uid == os.geteuid()
            and before.st_gid == os.getegid()
            and stat.S_IMODE(before.st_mode) == mode
            and before.st_nlink == 1
            and 0 < before.st_size <= bound,
            "file",
        )
        chunks = []
        total = 0
        while total <= bound:
            chunk = os.read(fd, min(65536, bound + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        after = os.fstat(fd)
        _require(
            total <= bound
            and identity
            == (
                after.st_dev,
                after.st_ino,
                after.st_mode,
                after.st_uid,
                after.st_gid,
                after.st_nlink,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            ),
            "drift",
        )
        raw = b"".join(chunks)
        _require(len(raw) == before.st_size and hashlib.sha256(raw).hexdigest() == digest, "pin")
        return raw
    finally:
        os.close(fd)


@contextmanager
def _locked(root: Path) -> Iterator[None]:
    admission = root / "media-elf-metadata-admission"
    _children(admission, {"admission.lock", "active.marker"})
    fd = os.open(admission / "admission.lock", os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        _require(
            stat.S_ISREG(info.st_mode)
            and stat.S_IMODE(info.st_mode) == 0o600
            and info.st_uid == os.geteuid()
            and info.st_gid == os.getegid()
            and info.st_nlink == 1
            and info.st_size == 0,
            "lock",
        )
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise FifthReconciliationRefused("reader_fifth_reconcile_live_lock") from None
        yield
    finally:
        os.close(fd)


def _audit_held(root: Path, pins: dict[str, tuple[str, int, int]]) -> dict[str, object]:
    """Caller must hold the exact shared admission lock for this entire call."""
    proof_base = root / "media-native-reader"
    _children(proof_base, {first.ATTEMPT_NAME, v1.SECOND, v2.THIRD, v3.FOURTH, FIFTH})
    for name in (first.ATTEMPT_NAME, v1.SECOND, v2.THIRD, v3.FOURTH):
        names = {"cases.json", "policy.json"}
        if name == first.ATTEMPT_NAME:
            names.add("docker-config")
        _children(proof_base / name, names)
    _children(proof_base / first.ATTEMPT_NAME / "docker-config", set())
    fifth = proof_base / FIFTH
    _children(fifth, {"build", "cases.json", "docker-config", "image-id", "policy.json"})
    _children(fifth / "docker-config", set())
    _children(fifth / "build", {"Dockerfile"} | {f"{n:02}.binary.reference" for n in range(8)})
    _children(root / "media-native-reader-extra-attempt", {"grant.json"})
    _children(root / "media-native-reader-extra-attempt-v2", {"grant.json"})
    _children(root / "media-native-reader-reconciliation", {"reconcile-1fudorw3"})
    _children(root / "media-native-reader-reconciliation" / "reconcile-1fudorw3", {"pending.json"})
    _children(root / "media-native-reader-extra-attempt-v3", {"grant.json", "seal.json"})
    _children(
        root / "media-native-reader-run-approval-v3",
        {"receipt.json", "recheck.json", "attempt-recheck.json"},
    )
    values = {relative: _read_pinned(root / relative, *pin) for relative, pin in pins.items()}
    marker = scope._json(values["media-elf-metadata-admission/active.marker"])
    policy = scope._json(values[f"media-native-reader/{FIFTH}/policy.json"])
    proof = scope._json(values[f"media-native-reader/{FIFTH}/cases.json"])
    digest = hashlib.sha256(
        json.dumps(policy, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    _require(marker == {"version": 1, "lease": LEASE, "policy": digest}, "marker_binding")
    _require(
        policy.get("attempt_limit") == 5 and policy.get("runtime_authority") is False, "policy"
    )
    _require(
        proof
        == {
            "status": "refused",
            "stage": "build",
            "support_release_confirmed": False,
            "runtime_authority": False,
            "build_failure": {
                "kind": "validation",
                "code": "reader_container_image_configuration",
            },
        },
        "refusal",
    )
    grant = scope._json(values["media-native-reader-extra-attempt-v3/grant.json"])
    _require(
        grant.get("runtime_root") == str(root)
        and grant.get("source_head") == SOURCE_HEAD
        and grant.get("source_tree") == SOURCE_TREE
        and grant.get("attempt_limit_after") == 5
        and grant.get("additional_attempts") == 1
        and grant.get("runtime_authority") is False,
        "grant_binding",
    )
    return {
        "status": "verified-retained-fifth-refusal-filesystem",
        "attempts_used": 5,
        "marker_fenced": True,
        "proof_sha256": pins[f"media-native-reader/{FIFTH}/cases.json"][0],
        "docker_resources_checked": False,
        "marker_clear_authorized": False,
        "another_attempt_authorized": False,
        "runtime_authority": False,
    }


def _audit(root: Path, pins: dict[str, tuple[str, int, int]]) -> dict[str, object]:
    root = scope._canonical_root(root)
    with _locked(root):
        return _audit_held(root, pins)


def audit(runtime_root: Path) -> dict[str, object]:
    """Inspect only the one retained runtime; no Docker access or writes."""
    _require(Path(runtime_root) == RETAINED_ROOT, "root")
    return _audit(RETAINED_ROOT, FILE_PINS)
