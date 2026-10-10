"""Pinned, opt-in operator runtime for the public memory comparison.

Importing this module performs no setup, environment loading or Hindsight import.
The operator stages reviewed inputs, reviews the offline plan, then explicitly
runs it. The normal Zelo application does not import or activate this module.
"""

from __future__ import annotations

import argparse
import asyncio
import contextvars
import hashlib
import importlib.metadata
import json
import logging
import math
import os
import pwd
import re
import signal
import stat
import sys
import time
import tomllib
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
API_ORIGIN = "http://127.0.0.1:28888"
OLLAMA_ORIGIN = "http://127.0.0.1:11434"
UPSTREAM_COMMIT = "44d5340148fd47efb34e184d61f87ab5e2d8d1e2"
SLIM_TREE_SHA1 = "b1bb4d5be99443079ba6f0eda9541ab30d52c11a"
LOCK_BLOB_SHA1 = "b7fbb924a1eebb8c2869105ef5b12ae3623d21d3"
LICENSE_BLOB_SHA1 = "8dce7b4d4c2632990bdf8b4101694ea19ba6ad46"
PG_IMAGE = (
    "pgvector/pgvector:pg18@sha256:ad249f9fb9572979643a47694e59b9a8cdcdf6abc6accebf49bb80ad5f20ff9b"
)
E5_REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
FLASHRANK_REVISION = "858a1ac046a05663a35367eac852d7f76feeefdd"
FLASHRANK_MODEL = "ms-marco-MiniLM-L-12-v2"
RETAIN_SECONDS = 600
RECALL_SECONDS = 120
CONTEXT_TOKENS = 16384
CHUNK_CHARS = 1000
EXTRACTION_MAX_COMPLETION_TOKENS = 2048
SCHEMA = "zelo-hindsight-runtime-v1"
AUDIT_LIMIT = 4096
_STARTUP_PHASE = "not_started"
SAMPLE_NAMES = {
    "temperature",
    "top_p",
    "top_k",
    "min_p",
    "presence_penalty",
    "repeat_penalty",
    "seed",
    "num_predict",
}
ASSET_PINS = {
    "e5/onnx/model.onnx": (
        470268510,
        "ca456c06b3a9505ddfd9131408916dd79290368331e7d76bb621f1cba6bc8665",
    ),
    "e5/tokenizer.json": (
        17082730,
        "0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39",
    ),
    "e5/config.json": (
        655,
        "69137736cab8b8903a07fe8afaafdda25aac55415a12a55d1bffa9f581abf959",
    ),
    "e5/tokenizer_config.json": (
        443,
        "a1d6bc8734a6f635dc158508bef000f8e2e5a759c7d92f984b2c86e5ff53425b",
    ),
    "e5/special_tokens_map.json": (
        167,
        "d05497f1da52c5e09554c0cd874037a083e1dc1b9cfd48034d1c717f1afc07a7",
    ),
    "ms-marco-MiniLM-L-12-v2.zip": (
        22696961,
        "bdd3772b651ffc34f70e414049285bb55ccc6d1b8e29d0640f836d44f70ec77a",
    ),
    "uv-0.13.0.whl": (
        17941073,
        "52c7da0fd9056d9551124777ec77de53f0df7017daf8f98a19e947b7837416ed",
    ),
}
# The separate build closure is filled only from independently verified PyPI
# wheel metadata. Runtime wheels must instead match the pinned upstream lock.
BUILD_WHEELS: dict[str, tuple[str, int, str]] = {
    "hatchling": (
        "1.27.0",
        75794,
        "d3a2f3567c4f926ea39849cdf924c7e99e6686c9c8e288ae1037c8fa2a5d937b",
    ),
    "pathspec": (
        "0.12.1",
        31191,
        "a0d503e138a4c123b27490a4f7beda6a01c6f288df0e4a8b79c7eb0dc7b4cc08",
    ),
    "trove-classifiers": (
        "2025.1.15.22",
        13610,
        "5f19c789d4f17f501d36c94dbbf969fb3e8c2784d008e6f5164dd2c3d6a2b07c",
    ),
    "packaging": (
        "25.0",
        66469,
        "29572ef2b1f17581046b3a2227d5c611fb25ec70ca1ba8554b24b0e69331a484",
    ),
    "pluggy": ("1.6.0", 20538, "e920276dd6813095e9377c0bc5566d94c932c33b27a3e3945d8389c374dd4746"),
}


class RuntimeRefusal(ValueError):
    """A bounded operator failure code; never an upstream exception message."""


def canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode()


def sha256_file(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def _hex(value: Any, size: int) -> bool:
    return isinstance(value, str) and re.fullmatch(f"[0-9a-f]{{{size}}}", value) is not None


def _plain_path(path: Path, *, exists: bool = True) -> Path:
    if not path.is_absolute() or ".." in path.parts or path != path.absolute():
        raise RuntimeRefusal("runtime_path_invalid")
    for part in (path, *path.parents):
        if part.is_symlink():
            raise RuntimeRefusal("runtime_path_symlink")
    if exists and not path.exists():
        raise RuntimeRefusal("runtime_input_missing")
    return path


def _private_file(path: Path, *, immutable: bool = False, max_bytes: int = 1048576) -> bytes:
    _plain_path(path)
    info = path.stat()
    expected = 0o400 if immutable else 0o600
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_IMODE(info.st_mode) != expected
        or info.st_uid != os.getuid()
        or info.st_size > max_bytes
    ):
        raise RuntimeRefusal("runtime_file_identity")
    return path.read_bytes()


def _write_once(path: Path, data: bytes, mode: int = 0o400) -> None:
    _plain_path(path, exists=False)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
        os.fchmod(handle.fileno(), mode)


@dataclass(frozen=True)
class RuntimeProtocol:
    comparison_run_id: str
    public_report_sha256: str
    memory_receipt_sha256: str
    source_commit: str
    runtime_script_sha256: str
    wheel_manifest_sha256: str
    model: str
    model_digest: str
    model_request_settings: dict[str, Any]
    schema: str = SCHEMA
    upstream_commit: str = UPSTREAM_COMMIT
    retain_timeout_seconds: int = RETAIN_SECONDS
    recall_timeout_seconds: int = RECALL_SECONDS
    extraction_max_completion_tokens: int = EXTRACTION_MAX_COMPLETION_TOKENS

    def __post_init__(self) -> None:
        if (
            self.schema != SCHEMA
            or self.upstream_commit != UPSTREAM_COMMIT
            or not _hex(self.comparison_run_id, 32)
            or not _hex(self.source_commit, 40)
            or any(
                not _hex(value, 64)
                for value in (
                    self.public_report_sha256,
                    self.memory_receipt_sha256,
                    self.runtime_script_sha256,
                    self.wheel_manifest_sha256,
                    self.model_digest,
                )
            )
            or type(self.retain_timeout_seconds) is not int
            or self.retain_timeout_seconds != RETAIN_SECONDS
            or type(self.recall_timeout_seconds) is not int
            or self.recall_timeout_seconds != RECALL_SECONDS
            or type(self.extraction_max_completion_tokens) is not int
            or self.extraction_max_completion_tokens != EXTRACTION_MAX_COMPLETION_TOKENS
            or not isinstance(self.model, str)
            or re.fullmatch(r"[A-Za-z0-9._:/-]{1,128}", self.model) is None
        ):
            raise RuntimeRefusal("runtime_protocol_invalid")
        settings = self.model_request_settings
        if (
            not isinstance(settings, dict)
            or set(settings) != SAMPLE_NAMES | {"format", "think", "num_ctx"}
            or settings["format"] != "json_schema"
            or settings["think"] is not False
            or type(settings["num_ctx"]) is not int
            or settings["num_ctx"] != CONTEXT_TOKENS
        ):
            raise RuntimeRefusal("runtime_model_settings")
        for name in SAMPLE_NAMES:
            value = settings[name]
            if type(value) not in (int, float) or not math.isfinite(value):
                raise RuntimeRefusal("runtime_model_settings")
        if (
            not 0 <= settings["temperature"] <= 2
            or not 0 <= settings["top_p"] <= 1
            or not 0 <= settings["min_p"] <= 1
            or not 0 <= settings["presence_penalty"] <= 2
            or not 0 <= settings["repeat_penalty"] <= 2
            or type(settings["top_k"]) is not int
            or not 1 <= settings["top_k"] <= 100
            or type(settings["seed"]) is not int
            or not 0 <= settings["seed"] < 2**31
            or type(settings["num_predict"]) is not int
            or not CHUNK_CHARS < settings["num_predict"] <= 2048
        ):
            raise RuntimeRefusal("runtime_model_settings")
        object.__setattr__(self, "model_request_settings", json.loads(canonical(settings)))


@dataclass(frozen=True)
class RuntimePaths:
    root: Path
    protocol: Path
    inputs: Path
    source: Path
    venv: Path
    build_venv: Path
    cache: Path
    readiness: Path
    extraction_audit: Path

    @property
    def flashrank(self) -> Path:
        return self.cache / "flashrank" / FLASHRANK_MODEL


def runtime_paths(protocol: RuntimeProtocol) -> RuntimePaths:
    root = ROOT / "runtime" / "hindsight-comparison" / protocol.comparison_run_id
    _plain_path(root, exists=False)
    return RuntimePaths(
        root,
        root / "runtime-protocol.json",
        root / "inputs",
        root / "inputs" / "hindsight-api-slim",
        root / "venv",
        root / "build-venv",
        root / "cache",
        root / "readiness.json",
        root / "extraction-audit.jsonl",
    )


def load_runtime_protocol(path: Path | str, *, expected_sha256: str) -> RuntimeProtocol:
    path = Path(path)
    raw = _private_file(path, immutable=True)
    if not _hex(expected_sha256, 64) or hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise RuntimeRefusal("runtime_protocol_digest")
    try:
        protocol = RuntimeProtocol(**json.loads(raw))
    except (TypeError, json.JSONDecodeError) as error:
        raise RuntimeRefusal("runtime_protocol_invalid") from error
    paths = runtime_paths(protocol)
    if path != paths.protocol or stat.S_IMODE(paths.root.stat().st_mode) != 0o700:
        raise RuntimeRefusal("runtime_protocol_path")
    if sha256_file(Path(__file__).resolve()) != protocol.runtime_script_sha256:
        raise RuntimeRefusal("runtime_script_changed")
    return protocol


def _git_blob(path: Path) -> str:
    _plain_path(path)
    data = path.read_bytes()
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def _git_tree(path: Path) -> str:
    entries = []
    for child in path.iterdir():
        _plain_path(child)
        if child.is_dir():
            mode, value, order = b"40000", _git_tree(child), child.name + "/"
        elif child.is_file():
            mode = b"100755" if child.stat().st_mode & 0o111 else b"100644"
            value, order = _git_blob(child), child.name
        else:
            raise RuntimeRefusal("runtime_source_type")
        entries.append(
            (order.encode(), mode + b" " + child.name.encode() + b"\0" + bytes.fromhex(value))
        )
    data = b"".join(entry for _, entry in sorted(entries))
    return hashlib.sha1(b"tree " + str(len(data)).encode() + b"\0" + data).hexdigest()


def _checked_asset(path: Path, size: int, digest: str) -> None:
    _plain_path(path)
    if not path.is_file() or path.stat().st_size != size or sha256_file(path) != digest:
        raise RuntimeRefusal("runtime_asset_changed")


def _zip_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    members = archive.infolist()
    if len(members) > 128 or sum(item.file_size for item in members) > 100_000_000:
        raise RuntimeRefusal("runtime_archive_limit")
    seen = set()
    for item in members:
        name = PurePosixPath(item.filename)
        if (
            name.is_absolute()
            or ".." in name.parts
            or "\\" in item.filename
            or item.filename in seen
            or stat.S_ISLNK(item.external_attr >> 16)
        ):
            raise RuntimeRefusal("runtime_archive_path")
        seen.add(item.filename)
    return members


def _flashrank_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    result = []
    # Validate every member before ignoring macOS metadata in this pinned ZIP.
    for item in _zip_members(archive):
        relative = PurePosixPath(item.filename)
        if relative.parts and relative.parts[0] == "__MACOSX":
            continue
        if relative == PurePosixPath(FLASHRANK_MODEL, ".DS_Store"):
            continue
        if not relative.parts or relative.parts[0] != FLASHRANK_MODEL:
            raise RuntimeRefusal("runtime_flashrank_layout")
        result.append(item)
    return result


def verify_runtime_assets(protocol: RuntimeProtocol, *, staged: bool = True) -> dict[str, Any]:
    paths = runtime_paths(protocol)
    for name, (size, digest) in ASSET_PINS.items():
        _checked_asset(paths.inputs / name, size, digest)
    if _git_tree(paths.source) != SLIM_TREE_SHA1:
        raise RuntimeRefusal("runtime_source_changed")
    if _git_blob(paths.inputs / "uv.lock") != LOCK_BLOB_SHA1:
        raise RuntimeRefusal("runtime_lock_changed")
    if _git_blob(paths.inputs / "LICENSE.Hindsight") != LICENSE_BLOB_SHA1:
        raise RuntimeRefusal("runtime_license_changed")
    members = {}
    with zipfile.ZipFile(paths.inputs / "ms-marco-MiniLM-L-12-v2.zip") as archive:
        for item in _flashrank_members(archive):
            if item.is_dir():
                continue
            relative = PurePosixPath(item.filename)
            value = archive.read(item)
            digest = hashlib.sha256(value).hexdigest()
            members[str(relative)] = digest
            if staged:
                target = paths.cache / "flashrank" / str(relative)
                _checked_asset(target, len(value), digest)
    required = {
        "flashrank-MiniLM-L-12-v2_Q.onnx",
        "tokenizer.json",
        "config.json",
        "tokenizer_config.json",
        "special_tokens_map.json",
    }
    if not {f"{FLASHRANK_MODEL}/{name}" for name in required} <= set(members):
        raise RuntimeRefusal("runtime_flashrank_incomplete")
    return {
        "upstream_commit": UPSTREAM_COMMIT,
        "source_tree_sha1": SLIM_TREE_SHA1,
        "e5_revision": E5_REVISION,
        "flashrank_revision": FLASHRANK_REVISION,
        "flashrank_members": members,
    }


def _wheel_manifest(protocol: RuntimeProtocol) -> list[dict[str, Any]]:
    paths = runtime_paths(protocol)
    raw = _private_file(paths.inputs / "wheel-manifest.json", immutable=True)
    if hashlib.sha256(raw).hexdigest() != protocol.wheel_manifest_sha256:
        raise RuntimeRefusal("runtime_wheel_manifest_changed")
    manifest = json.loads(raw)
    if (
        set(manifest) != {"schema", "wheels"}
        or manifest["schema"] != "zelo-hindsight-wheelhouse-v1"
    ):
        raise RuntimeRefusal("runtime_wheel_manifest")
    rows = manifest["wheels"]
    if not isinstance(rows, list) or not 5 < len(rows) <= 256:
        raise RuntimeRefusal("runtime_wheel_manifest")
    if _git_blob(paths.inputs / "uv.lock") != LOCK_BLOB_SHA1:
        raise RuntimeRefusal("runtime_lock_changed")
    locked = tomllib.loads((paths.inputs / "uv.lock").read_text())
    available = {
        (
            package["name"],
            package["version"],
            wheel["url"],
            wheel["hash"].removeprefix("sha256:"),
            wheel["size"],
        )
        for package in locked["package"]
        for wheel in package.get("wheels", [])
        if {"url", "hash", "size"} <= wheel.keys()
    }
    seen = set()
    for row in rows:
        if set(row) != {"name", "version", "filename", "url", "sha256", "size", "role"}:
            raise RuntimeRefusal("runtime_wheel_manifest")
        name, role = row["name"], row["role"]
        key = (role, name)
        if (
            key in seen
            or role not in {"build", "runtime"}
            or re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) is None
            or re.fullmatch(r"[A-Za-z0-9_.+-]+\.whl", row["filename"]) is None
            or any(
                name.startswith(prefix)
                for prefix in (
                    "torch",
                    "nvidia",
                    "cuda",
                    "llama-cpp",
                    "pg0",
                    "sentence-transformers",
                    "triton",
                    "onnxruntime-gpu",
                    "mlx",
                )
            )
        ):
            raise RuntimeRefusal("runtime_wheel_manifest")
        seen.add(key)
        if role == "build":
            if BUILD_WHEELS.get(name) != (row["version"], row["size"], row["sha256"]):
                raise RuntimeRefusal("runtime_build_wheel_unpinned")
        elif (name, row["version"], row["url"], row["sha256"], row["size"]) not in available:
            raise RuntimeRefusal("runtime_wheel_unpinned")
        _checked_asset(paths.inputs / "wheels" / row["filename"], row["size"], row["sha256"])
    if {name for role, name in seen if role == "build"} != set(BUILD_WHEELS) or not BUILD_WHEELS:
        raise RuntimeRefusal("runtime_build_closure_missing")
    if not {"flashrank", "onnxruntime", "transformers", "tokenizers", "numpy"} <= {
        name for role, name in seen if role == "runtime"
    }:
        raise RuntimeRefusal("runtime_onnx_closure_missing")
    return rows


def runtime_environment(
    protocol: RuntimeProtocol, paths: RuntimePaths | None = None
) -> dict[str, str]:
    paths = paths or runtime_paths(protocol)
    secret = _private_file(paths.root / "postgres.env", max_bytes=256).decode()
    match = re.fullmatch(
        r"POSTGRES_PASSWORD=([0-9a-f]{64})\nPOSTGRES_DB=zelo_([0-9a-f]{32})\n", secret
    )
    if match is None or match[2] != protocol.comparison_run_id:
        raise RuntimeRefusal("runtime_database_identity")
    settings = protocol.model_request_settings
    extra = {name: settings[name] for name in sorted(SAMPLE_NAMES - {"temperature", "num_predict"})}
    config = {
        "DATABASE_BACKEND": "postgresql",
        "DATABASE_URL": f"postgresql://postgres:{match[1]}@127.0.0.1:15432/zelo_{protocol.comparison_run_id}",
        "VECTOR_EXTENSION": "pgvector",
        "DB_POOL_MIN_SIZE": "1",
        "DB_POOL_MAX_SIZE": "2",
        "LLM_PROVIDER": "ollama",
        "LLM_MODEL": protocol.model,
        "LLM_BASE_URL": OLLAMA_ORIGIN,
        "LLM_API_KEY": "local",
        "LLM_OLLAMA_NUM_CTX": str(CONTEXT_TOKENS),
        "LLM_EXTRA_BODY": canonical({"options": extra}).decode(),
        "LLM_TIMEOUT": str(RETAIN_SECONDS),
        "RETAIN_LLM_TIMEOUT": str(RETAIN_SECONDS),
        "LLM_MAX_RETRIES": "0",
        "RETAIN_LLM_MAX_RETRIES": "0",
        "LLM_MAX_CONCURRENT": "1",
        "RETAIN_LLM_MAX_CONCURRENT": "1",
        "LLM_TEMPERATURE_RETAIN": str(settings["temperature"]),
        "RETAIN_MAX_COMPLETION_TOKENS": str(protocol.extraction_max_completion_tokens),
        "RETAIN_CHUNK_SIZE": str(CHUNK_CHARS),
        "RETAIN_MAX_CONCURRENT": "1",
        "RECALL_MAX_CONCURRENT": "1",
        "RETAIN_BATCH_ENABLED": "false",
        "FAIL_ON_EXTRACTION_ERRORS": "true",
        "SKIP_LLM_VERIFICATION": "true",
        "STORE_DOCUMENT_TEXT": "true",
        "ENABLE_BANK_LLM_HEALTH": "false",
        "EMBEDDINGS_PROVIDER": "onnx",
        "EMBEDDINGS_ONNX_MODEL_ID": "intfloat/multilingual-e5-small",
        "EMBEDDINGS_ONNX_MODEL_PATH": str(paths.inputs / "e5/onnx/model.onnx"),
        "EMBEDDINGS_ONNX_TOKENIZER_NAME_OR_PATH": str(paths.inputs / "e5"),
        "EMBEDDINGS_ONNX_DIMENSIONS": "384",
        "EMBEDDINGS_ONNX_MAX_TOKENS": "512",
        "EMBEDDINGS_ONNX_POOLING": "mean",
        "EMBEDDINGS_ONNX_NORMALIZE": "true",
        "EMBEDDINGS_ONNX_QUERY_PREFIX": "query: ",
        "EMBEDDINGS_ONNX_PASSAGE_PREFIX": "passage: ",
        "EMBEDDINGS_ONNX_BATCH_SIZE": "1",
        "EMBEDDINGS_ONNX_DEVICE": "cpu",
        "EMBEDDINGS_ONNX_CPU_MEM_ARENA": "false",
        "RERANKER_PROVIDER": "flashrank",
        "RERANKER_FLASHRANK_MODEL": FLASHRANK_MODEL,
        "RERANKER_FLASHRANK_CACHE_DIR": str(paths.cache / "flashrank"),
        "RERANKER_FLASHRANK_CPU_MEM_ARENA": "false",
        "RERANKER_FLASHRANK_BATCH_SIZE": "1",
        "WORKER_ENABLED": "false",
        "WORKERS": "1",
        "MCP_ENABLED": "false",
        "ENABLE_OBSERVATIONS": "false",
        "ENABLE_AUTO_CONSOLIDATION": "false",
        "CONSOLIDATION_RECONCILE_INTERVAL_SECONDS": "0",
        "MENTAL_MODEL_REFRESH_TICK_SECONDS": "0",
        "RETENTION_SWEEP_INTERVAL_SECONDS": "0",
        "OPERATION_CLEANUP_INTERVAL_SECONDS": "0",
        "OTEL_TRACES_ENABLED": "false",
        "LLM_TRACE_ENABLED": "false",
        "AUDIT_LOG_ENABLED": "false",
    }
    result = {f"HINDSIGHT_API_{key}": value for key, value in config.items()}
    result.update(
        {
            "PATH": "/usr/bin:/bin",
            "HOME": pwd.getpwuid(os.getuid()).pw_dir,
            "LANG": "C.UTF-8",
            "TMPDIR": str(paths.root / "tmp"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "HF_HOME": str(paths.cache / "huggingface"),
            "XDG_CACHE_HOME": str(paths.cache),
            "XDG_CONFIG_HOME": str(paths.root / "config"),
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "HF_HUB_DISABLE_TELEMETRY": "1",
            "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1",
            "PYTHON_DOTENV_DISABLED": "1",
            "LITELLM_LOCAL_MODEL_COST_MAP": "true",
            "TOKENIZERS_PARALLELISM": "false",
            "OMP_NUM_THREADS": "1",
            "OPENBLAS_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
            "NUMEXPR_NUM_THREADS": "1",
            "OTEL_SDK_DISABLED": "true",
            "OTEL_TRACES_EXPORTER": "none",
            "OTEL_METRICS_EXPORTER": "none",
            "OTEL_LOGS_EXPORTER": "none",
        }
    )
    return result


def extraction_request_settings(protocol: RuntimeProtocol) -> dict[str, Any]:
    """The extraction cap is explicit and separate from the shared answer profile."""
    return {
        **protocol.model_request_settings,
        "num_predict": protocol.extraction_max_completion_tokens,
    }


def runtime_config_fingerprint(protocol: RuntimeProtocol) -> str:
    """Hash the explicit owned configuration without exposing its PG credential."""
    return hashlib.sha256(canonical(runtime_environment(protocol))).hexdigest()


def build_runtime_plan(protocol: RuntimeProtocol) -> dict[str, Any]:
    """Reviewable offline argv; this function executes no command."""
    paths = runtime_paths(protocol)
    rows = _wheel_manifest(protocol)
    uv = str(paths.root / "bootstrap/uv")
    python = str(paths.venv / "bin/python")
    build_python = str(paths.build_venv / "bin/python")
    requirements = {}
    for role in ("build", "runtime"):
        requirements[role] = "".join(
            f"{row['name']}=={row['version']} --hash=sha256:{row['sha256']}\n"
            for row in sorted(rows, key=lambda value: (value["name"], value["role"]))
            if row["role"] == role
        )
    commands = [
        {
            "argv": [
                uv,
                "--offline",
                "venv",
                "--no-project",
                "--python",
                "/usr/bin/python3",
                str(paths.build_venv),
            ]
        },
        {
            "argv": [
                uv,
                "--offline",
                "venv",
                "--no-project",
                "--python",
                "/usr/bin/python3",
                str(paths.venv),
            ]
        },
    ]
    for role, interpreter in (("build", build_python), ("runtime", python)):
        commands.append(
            {
                "argv": [
                    uv,
                    "--offline",
                    "pip",
                    "install",
                    "--python",
                    interpreter,
                    "--no-index",
                    "--require-hashes",
                    "--only-binary",
                    ":all:",
                    "--find-links",
                    str(paths.inputs / "wheels"),
                    "-r",
                    str(paths.root / f"{role}-requirements.txt"),
                ]
            }
        )
    commands.extend(
        [
            {
                "cwd": str(paths.source),
                "argv": [
                    build_python,
                    "-I",
                    "-B",
                    "-m",
                    "hatchling",
                    "build",
                    "-t",
                    "wheel",
                    "-d",
                    str(paths.root / "built"),
                ],
            },
            {
                "argv": [
                    uv,
                    "--offline",
                    "pip",
                    "install",
                    "--python",
                    python,
                    "--no-index",
                    "--no-deps",
                    str(paths.root / "built/hindsight_api_slim-0.10.3-py3-none-any.whl"),
                ]
            },
            {"argv": [uv, "--offline", "pip", "check", "--python", python]},
            {
                "argv": [
                    "docker",
                    "run",
                    "--detach",
                    "--pull=never",
                    "--name",
                    f"zelo-hindsight-{protocol.comparison_run_id}",
                    "--label",
                    f"zelo.hindsight.run={protocol.comparison_run_id}",
                    "--publish",
                    "127.0.0.1:15432:5432",
                    "--env-file",
                    str(paths.root / "postgres.env"),
                    "--mount",
                    f"type=bind,source={paths.root / 'postgres'},target=/var/lib/postgresql",
                    PG_IMAGE,
                ]
            },
        ]
    )
    return {
        "schema": "zelo-hindsight-offline-plan-v1",
        "run_id": protocol.comparison_run_id,
        "python": "CPython 3.12.3 / Linux x86_64",
        "commands": commands,
        "requirements": requirements,
        "environment": {
            "PATH": "/usr/bin:/bin",
            "HOME": pwd.getpwuid(os.getuid()).pw_dir,
            "LANG": "C.UTF-8",
            "TMPDIR": str(paths.root / "tmp"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "UV_CACHE_DIR": str(paths.cache / "uv"),
            "UV_OFFLINE": "true",
            "UV_NO_CONFIG": "1",
            "UV_PYTHON_DOWNLOADS": "never",
            "XDG_CONFIG_HOME": str(paths.root / "config"),
            "PYTHON_DOTENV_DISABLED": "1",
        },
        "serve_argv": [
            python,
            "-I",
            "-B",
            str(Path(__file__).resolve()),
            "serve",
            "--protocol",
            str(paths.protocol),
            "--expected-protocol-sha256",
            sha256_file(paths.protocol),
        ],
        "api_origin": API_ORIGIN,
        "pg_image": PG_IMAGE,
        "network_downloads": False,
        "upstream_workspace_sync": False,
        "notes": [
            "Run explicit prepare before this plan; all inputs must already match their pins.",
            "Review and execute commands serially in the minimal environment; halt on any failure.",
            "Missing dependencies fail the offline closure check; no resolver fallback.",
            "Serve imports the library factory, never the upstream dotenv-loading CLI.",
            "API startup performs the ONNX dimension warmup; it skips the Ollama model probe.",
            "Local metrics remain; external exporters and content tracing are disabled.",
            "Preserve the owned database and all failed evidence; stopping does not delete them.",
        ],
    }


def prepare_runtime_files(protocol: RuntimeProtocol) -> dict[str, Any]:
    """Stage only previously supplied pinned bytes; never download/install/start."""
    paths = runtime_paths(protocol)
    verify_runtime_assets(protocol, staged=False)
    plan = build_runtime_plan(protocol)
    for relative in (
        "bootstrap",
        "cache",
        "cache/flashrank",
        "cache/huggingface",
        "config",
        "tmp",
        "postgres",
        "built",
    ):
        path = paths.root / relative
        _plain_path(path, exists=False)
        path.mkdir(mode=0o700, parents=True, exist_ok=False)
    with zipfile.ZipFile(paths.inputs / "uv-0.13.0.whl") as archive:
        candidates = [
            item for item in _zip_members(archive) if PurePosixPath(item.filename).name == "uv"
        ]
        if len(candidates) != 1:
            raise RuntimeRefusal("runtime_uv_layout")
        binary = archive.read(candidates[0])
        if not binary.startswith(b"\x7fELF"):
            raise RuntimeRefusal("runtime_uv_layout")
        _write_once(paths.root / "bootstrap/uv", binary, 0o500)
    with zipfile.ZipFile(paths.inputs / "ms-marco-MiniLM-L-12-v2.zip") as archive:
        for item in _flashrank_members(archive):
            target = paths.cache / "flashrank" / item.filename
            if item.is_dir():
                target.mkdir(mode=0o700, parents=True, exist_ok=True)
            else:
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                _write_once(target, archive.read(item))
    secret = (
        f"POSTGRES_PASSWORD={os.urandom(32).hex()}\nPOSTGRES_DB=zelo_{protocol.comparison_run_id}\n"
    )
    _write_once(paths.root / "postgres.env", secret.encode(), 0o600)
    for role, content in plan["requirements"].items():
        _write_once(paths.root / f"{role}-requirements.txt", content.encode())
    _write_once(paths.root / "offline-plan.json", canonical(plan) + b"\n")
    return verify_runtime_assets(protocol)


def verify_installed_runtime(protocol: RuntimeProtocol) -> None:
    paths = runtime_paths(protocol)
    if Path(sys.prefix) != paths.venv:
        raise RuntimeRefusal("runtime_venv_identity")
    expected = {
        row["name"]: row["version"] for row in _wheel_manifest(protocol) if row["role"] == "runtime"
    }
    expected["hindsight-api-slim"] = "0.10.3"
    actual = {
        re.sub(r"[-_.]+", "-", distribution.metadata["Name"]).lower(): distribution.version
        for distribution in importlib.metadata.distributions()
    }
    if actual != expected:
        raise RuntimeRefusal("runtime_installed_closure")
    distribution = importlib.metadata.distribution("hindsight-api-slim")
    for source in (paths.source / "hindsight_api").rglob("*"):
        if source.is_file():
            relative = source.relative_to(paths.source)
            installed = Path(distribution.locate_file(str(relative)))
            if not installed.is_file() or sha256_file(source) != sha256_file(installed):
                raise RuntimeRefusal("runtime_installed_source_changed")


_OPERATION = contextvars.ContextVar("hindsight_comparison_operation", default=None)


def process_starttime(pid: int) -> int | None:
    try:
        text = Path(f"/proc/{pid}/stat").read_text()
        fields = text[text.rfind(")") + 2 :].split()
        return None if fields[0] == "Z" else int(fields[19])
    except (FileNotFoundError, ProcessLookupError):
        return None


class ExtractionAudit:
    """Counts actual native POST attempts; stores no messages or model content."""

    def __init__(self, path: Path, protocol: RuntimeProtocol) -> None:
        self.path, self.protocol, self.sequence, self.calls = path, protocol, 0, 0
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.close(descriptor)

    def emit(self, event: str, **fields: Any) -> None:
        self.sequence += 1
        if self.sequence > AUDIT_LIMIT:
            raise RuntimeRefusal("runtime_audit_limit")
        record = {
            "sequence": self.sequence,
            "event": event,
            "operation_id": _OPERATION.get(),
            "monotonic_ns": time.monotonic_ns(),
            **fields,
        }
        with self.path.open("ab") as handle:
            handle.write(canonical(record) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())

    def begin(self, url: str, kwargs: dict[str, Any]) -> int:
        payload = kwargs.get("json", {})
        expected = {
            "num_batch": 512,
            **{
                key: value
                for key, value in extraction_request_settings(self.protocol).items()
                if key not in {"format", "think"}
            },
        }
        if (
            url != OLLAMA_ORIGIN + "/api/chat"
            or payload.get("model") != self.protocol.model
            or payload.get("think") is not False
            or payload.get("stream") is not False
            or not isinstance(payload.get("format"), dict)
            or canonical(payload.get("options")) != canonical(expected)
            or _OPERATION.get() is None
        ):
            raise RuntimeRefusal("runtime_native_request_changed")
        self.calls += 1
        self.emit(
            "native_start", call_id=self.calls, settings=expected, format="json_schema", think=False
        )
        return self.calls

    def response(self, call_id: int, text: str, status: int) -> None:
        result: dict[str, Any] = {}
        try:
            value = json.loads(text) if len(text) <= 2_000_000 else None
            if isinstance(value, dict):
                result = value
        except json.JSONDecodeError:
            pass
        numeric = {}
        for name in (
            "prompt_eval_count",
            "eval_count",
            "prompt_eval_duration",
            "eval_duration",
            "load_duration",
            "total_duration",
        ):
            value = result.get(name)
            if type(value) is int and 0 <= value < 2**63:
                numeric[name] = value
        self.emit(
            "native_response",
            call_id=call_id,
            status=status,
            usage_known={"prompt_eval_count", "eval_count"} <= numeric.keys(),
            done_reason=result.get("done_reason")
            if result.get("done_reason") in {"stop", "length"}
            else "unknown",
            **numeric,
        )


def summarize_extraction_audit(raw: bytes, *, operation_id: str) -> dict[str, Any]:
    """Summarize a complete operation's byte range, without guessing lost usage."""
    if len(raw) > 2_000_000 or not raw.endswith(b"\n"):
        raise RuntimeRefusal("runtime_audit_incomplete")
    try:
        records = [json.loads(line) for line in raw.splitlines()]
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeRefusal("runtime_audit_invalid") from error
    if not records or len(records) > AUDIT_LIMIT:
        raise RuntimeRefusal("runtime_audit_invalid")
    sequence = [row.get("sequence") for row in records]
    if (
        any(type(value) is not int for value in sequence)
        or any(right != left + 1 for left, right in zip(sequence, sequence[1:], strict=False))
        or any(row.get("operation_id") != operation_id for row in records)
        or records[0].get("event") != "operation_start"
        or records[-1].get("event") != "operation_end"
    ):
        raise RuntimeRefusal("runtime_audit_incomplete")
    calls: dict[int, dict[str, Any]] = {}
    for row in records[1:-1]:
        event, call_id = row.get("event"), row.get("call_id")
        if (
            event not in {"native_start", "native_response", "native_end"}
            or type(call_id) is not int
        ):
            raise RuntimeRefusal("runtime_audit_invalid")
        if event == "native_start":
            if call_id in calls:
                raise RuntimeRefusal("runtime_audit_invalid")
            calls[call_id] = {}
        elif call_id not in calls or event in calls[call_id] or "native_end" in calls[call_id]:
            raise RuntimeRefusal("runtime_audit_invalid")
        else:
            calls[call_id][event] = row
    complete = [call.get("native_response") for call in calls.values()]
    known = [row for row in complete if row and row.get("usage_known") is True]
    for row in known:
        if any(
            type(row.get(key)) is not int or row[key] < 0
            for key in ("prompt_eval_count", "eval_count")
        ):
            raise RuntimeRefusal("runtime_audit_invalid")
    return {
        "sha256": hashlib.sha256(raw).hexdigest(),
        "records": len(records),
        "native_attempts": len(calls),
        "complete_responses": sum(row is not None for row in complete),
        "usage_known_attempts": len(known),
        "usage_unknown_attempts": len(calls) - len(known),
        "unfinished_attempts": sum("native_end" not in call for call in calls.values()),
        "cancelled_attempts": sum(
            call.get("native_end", {}).get("outcome") == "cancelled" for call in calls.values()
        ),
        "output_limited_responses": sum(
            row is not None and row.get("done_reason") == "length" for row in complete
        ),
        "input_tokens": sum(row["prompt_eval_count"] for row in known),
        "output_tokens": sum(row["eval_count"] for row in known),
        "operation_outcome": records[-1].get("outcome"),
    }


class _AuditedResponse:
    def __init__(self, response: Any, audit: ExtractionAudit, call_id: int) -> None:
        self.response, self.audit, self.call_id, self.observed = response, audit, call_id, False

    def __getattr__(self, name: str) -> Any:
        return getattr(self.response, name)

    async def text(self, *args: Any, **kwargs: Any) -> str:
        text = await self.response.text(*args, **kwargs)
        if not self.observed:
            self.audit.response(self.call_id, text, self.response.status)
            self.observed = True
        return text


class _AuditedPost:
    def __init__(
        self, session: Any, url: str, kwargs: dict[str, Any], audit: ExtractionAudit
    ) -> None:
        self.session, self.url, self.kwargs, self.audit = session, url, kwargs, audit
        self.context = self.response = None
        self.call_id = 0
        self.started = 0.0

    def _finish(self, error: BaseException | None) -> None:
        category = "http_complete"
        if isinstance(error, asyncio.CancelledError):
            category = "cancelled"
        elif isinstance(error, TimeoutError):
            category = "timeout"
        elif error is not None:
            category = "transport_error"
        self.audit.emit(
            "native_end",
            call_id=self.call_id,
            outcome=category,
            response_observed=bool(self.response and self.response.observed),
            duration_seconds=round(time.monotonic() - self.started, 6),
        )

    async def __aenter__(self) -> _AuditedResponse:
        self.call_id = self.audit.begin(self.url, self.kwargs)
        self.started = time.monotonic()
        try:
            self.context = self.session.post(self.url, **self.kwargs)
            response = await self.context.__aenter__()
        except BaseException as error:
            self._finish(error)
            raise
        self.response = _AuditedResponse(response, self.audit, self.call_id)
        return self.response

    async def __aexit__(self, kind: Any, error: Any, traceback: Any) -> Any:
        try:
            result = await self.context.__aexit__(kind, error, traceback)
        except BaseException as close_error:
            self._finish(close_error)
            raise
        self._finish(error)
        return result


class _AuditedSession:
    def __init__(self, session: Any, audit: ExtractionAudit) -> None:
        self.session, self.audit = session, audit

    def post(self, url: str, **kwargs: Any) -> _AuditedPost:
        return _AuditedPost(self.session, url, kwargs, self.audit)


class _AuditedPool:
    def __init__(self, pool: Any, audit: ExtractionAudit) -> None:
        self.pool, self.audit = pool, audit

    def get(self) -> _AuditedSession:
        return _AuditedSession(self.pool.get(), self.audit)

    async def close(self) -> None:
        await self.pool.close()


def install_native_audit(memory: Any, audit: ExtractionAudit) -> None:
    # Pinned 0.10.3 uses this LoopLocalSession only for /api/chat. Observe the
    # original session/context/response without rebuilding bodies or changing
    # timeouts/retries. An output-capped response is counted before autosplitting.
    wrapped: set[int] = set()
    for name in ("_llm_config", "_retain_llm_config"):
        wrapper = getattr(memory, name)
        provider = wrapper._provider_impl
        if (
            provider.provider != "ollama"
            or provider.model != audit.protocol.model
            or wrapper.max_retries != 0
            or provider.timeout != RETAIN_SECONDS
        ):
            raise RuntimeRefusal("runtime_provider_identity")
        if id(provider) not in wrapped:
            provider._ollama_http = _AuditedPool(provider._ollama_http, audit)
            wrapped.add(id(provider))


class RuntimeBoundary:
    def __init__(
        self,
        app: Any,
        protocol: RuntimeProtocol,
        audit: ExtractionAudit,
        environment: dict[str, str],
    ) -> None:
        self.app, self.protocol, self.audit, self.environment = app, protocol, audit, environment

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        paths = runtime_paths(self.protocol)
        if scope["type"] == "lifespan":

            async def startup_send(message: dict[str, Any]) -> None:
                if message["type"] == "lifespan.startup.complete":
                    receipt = {
                        "schema": "zelo-hindsight-readiness-v1",
                        "state": "startup_complete",
                        "run_id": self.protocol.comparison_run_id,
                        "pid": os.getpid(),
                        "process_starttime": process_starttime(os.getpid()),
                        "runtime_protocol_sha256": sha256_file(paths.protocol),
                        "upstream_commit": UPSTREAM_COMMIT,
                        "source_commit": self.protocol.source_commit,
                        "config_sha256": hashlib.sha256(canonical(self.environment)).hexdigest(),
                        "extraction_audit": str(paths.extraction_audit),
                        "api_origin": API_ORIGIN,
                        "local_metrics": True,
                        "external_exports": False,
                    }
                    _write_once(paths.readiness, canonical(receipt) + b"\n")
                await send(message)

            await self.app(scope, receive, startup_send)
            return
        path = scope.get("path", "")
        timeout = None
        if scope["type"] == "http" and scope.get("method") == "POST":
            if re.fullmatch(r"/v1/default/banks/[^/]+/memories", path):
                timeout = RETAIN_SECONDS
            elif re.fullmatch(r"/v1/default/banks/[^/]+/memories/recall", path):
                timeout = RECALL_SECONDS
        if timeout is None:
            await self.app(scope, receive, send)
            return
        operation = (
            dict(scope.get("headers", []))
            .get(b"x-zelo-operation-id", b"")
            .decode("ascii", errors="replace")
        )
        if re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", operation) is None:
            await send({"type": "http.response.start", "status": 400, "headers": []})
            await send({"type": "http.response.body", "body": b'{"error":"operation_id_required"}'})
            return
        token = _OPERATION.set(operation)
        started = False

        async def response_send(message: dict[str, Any]) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            self.audit.emit("operation_start", timeout_seconds=timeout)
            async with asyncio.timeout(timeout):
                await self.app(scope, receive, response_send)
            self.audit.emit("operation_end", outcome="complete")
        except TimeoutError:
            self.audit.emit("operation_end", outcome="timeout")
            if started:
                raise
            await send({"type": "http.response.start", "status": 504, "headers": []})
            await send({"type": "http.response.body", "body": b'{"error":"operation_timeout"}'})
        except BaseException:
            self.audit.emit("operation_end", outcome="interrupted")
            raise
        finally:
            _OPERATION.reset(token)


def serve_runtime(protocol: RuntimeProtocol) -> None:
    global _STARTUP_PHASE
    _STARTUP_PHASE = "assets"
    paths = runtime_paths(protocol)
    verify_runtime_assets(protocol)
    _STARTUP_PHASE = "installed_packages"
    verify_installed_runtime(protocol)
    if paths.readiness.exists() or paths.extraction_audit.exists():
        raise RuntimeRefusal("runtime_already_started")
    _STARTUP_PHASE = "configuration"
    environment = runtime_environment(protocol)
    os.environ.clear()
    os.environ.update(environment)
    logging.disable(logging.CRITICAL)
    # These explicit library imports do not call the upstream server/CLI dotenv
    # loader. Missing local assets or dependencies are a failure, never a download.
    _STARTUP_PHASE = "imports"
    import uvicorn
    from hindsight_api import MemoryEngine
    from hindsight_api.api import create_app

    _STARTUP_PHASE = "engine"
    memory = MemoryEngine(
        db_url=environment["HINDSIGHT_API_DATABASE_URL"],
        run_migrations=True,
        skip_llm_verification=True,
    )
    _STARTUP_PHASE = "audit"
    audit = ExtractionAudit(paths.extraction_audit, protocol)
    install_native_audit(memory, audit)
    _STARTUP_PHASE = "application"
    app = create_app(
        memory=memory, http_api_enabled=True, mcp_api_enabled=False, initialize_memory=True
    )
    _STARTUP_PHASE = "server"
    uvicorn.run(
        RuntimeBoundary(app, protocol, audit, environment),
        host="127.0.0.1",
        port=28888,
        workers=1,
        reload=False,
        access_log=False,
        log_config=None,
        timeout_graceful_shutdown=5,
    )


def stop_runtime(
    protocol: RuntimeProtocol, readiness: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Stop only this owned API. Preserve its database and all evidence."""
    paths = runtime_paths(protocol)
    retained = json.loads(_private_file(paths.readiness, immutable=True))
    if readiness is not None and canonical(retained) != canonical(readiness):
        raise RuntimeRefusal("runtime_readiness_changed")
    pid, expected = retained.get("pid"), retained.get("process_starttime")
    if (
        type(pid) is not int
        or pid <= 1
        or type(expected) is not int
        or retained.get("run_id") != protocol.comparison_run_id
        or retained.get("runtime_protocol_sha256") != sha256_file(paths.protocol)
        or retained.get("extraction_audit") != str(paths.extraction_audit)
    ):
        raise RuntimeRefusal("runtime_process_identity")
    current = process_starttime(pid)
    if current is not None and current != expected:
        raise RuntimeRefusal("runtime_pid_reused")
    forced = False
    if current is not None:
        if pid == os.getpid():
            raise RuntimeRefusal("runtime_process_identity")
        try:
            descriptor = os.pidfd_open(pid)
        except ProcessLookupError:
            descriptor = None
        if descriptor is not None:
            try:
                if process_starttime(pid) not in (None, expected):
                    raise RuntimeRefusal("runtime_pid_reused")
                try:
                    signal.pidfd_send_signal(descriptor, signal.SIGTERM)
                    deadline = time.monotonic() + 10
                    while process_starttime(pid) == expected and time.monotonic() < deadline:
                        time.sleep(0.05)
                    if process_starttime(pid) == expected:
                        signal.pidfd_send_signal(descriptor, signal.SIGKILL)
                        forced = True
                        deadline = time.monotonic() + 2
                        while process_starttime(pid) == expected and time.monotonic() < deadline:
                            time.sleep(0.05)
                except ProcessLookupError:
                    pass
            finally:
                os.close(descriptor)
        if process_starttime(pid) == expected:
            raise RuntimeRefusal("runtime_cleanup_unproven")
    receipt = {
        "schema": "zelo-hindsight-stop-v1",
        "run_id": protocol.comparison_run_id,
        "pid": pid,
        "process_starttime": expected,
        "api_process_gone": True,
        "forced": forced,
        "ollama_cancelled_usage": "unknown_if_no_complete_response",
        "postgres_preserved": True,
    }
    target = paths.root / "stop-receipt.json"
    if not target.exists():
        _write_once(target, canonical(receipt) + b"\n")
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["plan", "prepare", "verify", "serve", "stop"])
    parser.add_argument("--protocol", required=True, type=Path)
    parser.add_argument("--expected-protocol-sha256", required=True)
    args = parser.parse_args()
    phase = "protocol"
    try:
        protocol = load_runtime_protocol(
            args.protocol, expected_sha256=args.expected_protocol_sha256
        )
        phase = args.action
        if args.action == "serve":
            serve_runtime(protocol)
            result = {"status": "stopped"}
        elif args.action == "stop":
            result = stop_runtime(protocol)
        elif args.action == "prepare":
            result = prepare_runtime_files(protocol)
        elif args.action == "verify":
            result = verify_runtime_assets(protocol)
        else:
            result = build_runtime_plan(protocol)
        print(canonical(result).decode(), flush=True)
        return 0
    except (Exception, KeyboardInterrupt, SystemExit) as error:
        candidate = str(error) if isinstance(error, RuntimeRefusal) else ""
        code = candidate if re.fullmatch(r"runtime_[a-z_]{1,63}", candidate) else "runtime_failed"
        exception_class = type(error).__name__
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", exception_class) is None:
            exception_class = "UnknownError"
        failure = {
            "status": "failed",
            "code": code,
            "action": args.action,
            "phase": _STARTUP_PHASE if phase == "serve" else phase,
            "exception_class": exception_class,
        }
        print(canonical(failure).decode(), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
