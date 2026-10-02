"""Private local operator receipts; structural validation is not image attestation.

Only the explicit preparation path can establish actual stopped-image payload
verification. Same-UID operator, local filesystem and ancestors remain trusted.
"""

import hashlib
import json
import math
import os
import stat
from dataclasses import asdict, fields, is_dataclass
from pathlib import Path
from typing import get_args, get_origin, get_type_hints

from app.api_contracts import ApiEndpointRegistry
from qualification.broker import Policy
from qualification.renderer_contracts import RendererContract
from scripts.qualify_browser_renderer import expected_image_files

RECEIPT_LIMIT = 2 * 1024 * 1024


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def contract_data(contract: RendererContract) -> dict:
    value = asdict(contract)
    value["profile"]["profile_bytes"] = contract.profile.profile_bytes.hex()
    return value


def _decode(kind, value):
    """Reconstruct only statically named dataclasses, requiring every exact field."""
    if is_dataclass(kind):
        declared = fields(kind)
        if type(value) is not dict or set(value) != {field.name for field in declared}:
            raise ValueError("receipt_fields")
        hints = get_type_hints(kind)
        args = {field.name: _decode(hints[field.name], value[field.name]) for field in declared}
        result = kind(**{field.name: args[field.name] for field in declared if field.init})
        if any(getattr(result, field.name) != args[field.name] for field in declared):
            raise ValueError("receipt_constant")
        return result
    if get_origin(kind) is tuple:
        if type(value) is not list:
            raise ValueError("receipt_tuple")
        args = get_args(kind)
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(_decode(args[0], item) for item in value)
        if len(args) != len(value):
            raise ValueError("receipt_tuple")
        return tuple(_decode(k, v) for k, v in zip(args, value, strict=True))
    if kind is bytes:
        if type(value) is not str or len(value) > 131072:
            raise ValueError("receipt_bytes")
        result = bytes.fromhex(value)
        if result.hex() != value:
            raise ValueError("receipt_bytes")
        return result
    if kind is float:
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError("receipt_number")
        return float(value)
    if kind not in (str, int, bool) or type(value) is not kind:
        raise ValueError("receipt_type")
    return value


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("receipt_duplicate")
        result[key] = value
    return result


def _invalid(value):
    raise ValueError("receipt_nonfinite")


def private_directory(path: Path) -> Path:
    if not path.is_absolute() or path.resolve() != path:
        raise ValueError("browser_directory_not_canonical")
    info = path.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise ValueError("browser_directory_not_private")
    return path


def load_receipt(
    path: Path, bundle: Path, registry: ApiEndpointRegistry
) -> tuple[RendererContract, str]:
    """No Docker/DNS/build work; reject malformed config before worker registration."""
    try:
        if not path.is_absolute() or path.resolve() != path:
            raise ValueError("receipt_path")
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_nlink != 1
                or not 0 < info.st_size <= RECEIPT_LIMIT
            ):
                raise ValueError("receipt_private_file")
            with os.fdopen(fd, "rb", closefd=False) as source:
                raw = source.read(RECEIPT_LIMIT + 1)
            if len(raw) > RECEIPT_LIMIT:
                raise ValueError("receipt_limit")
        finally:
            os.close(fd)
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique, parse_constant=_invalid)
        if (
            type(value) is not dict
            or set(value) != {"schema", "contract", "image_payload", "image_payload_digest"}
            or value["schema"] != "zelo-browser-operator-v1"
        ):
            raise ValueError("receipt_schema")
        contract = _decode(RendererContract, value["contract"])
        contract.verify_bundle(bundle)
        contract.verify_broker(
            registry, tuple(registry.bind(e.endpoint_id) for e in registry.list()), Policy()
        )
        expected = expected_image_files(contract.manifest, bundle / "browser-pipe")
        # Exact inventory including hashes and modes, not a count or boolean claim.
        if (
            canonical(value["image_payload"]) != canonical(expected)
            or value["image_payload_digest"] != hashlib.sha256(canonical(expected)).hexdigest()
        ):
            raise ValueError("receipt_inventory")
        return contract, hashlib.sha256(canonical(value)).hexdigest()
    except (OSError, ValueError, TypeError, KeyError, RecursionError, OverflowError):
        raise ValueError("browser_operator_receipt_invalid") from None
