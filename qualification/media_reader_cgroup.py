"""Bounded cgroup-v2 evidence for the Media native-reader qualification."""

import os
import re
import stat
from pathlib import Path

from qualification import media_reader_container as container

MAX_FILE = 4096


class Refused(ValueError):
    """Fixed cgroup evidence refusal."""


def _require(condition, code):
    if not condition:
        raise Refused("reader_cgroup_" + code)


def parse_membership(raw: bytes) -> str:
    _require(type(raw) is bytes and 0 < len(raw) <= MAX_FILE, "membership")
    try:
        text = raw.decode("ascii", "strict")
    except UnicodeError:
        raise Refused("reader_cgroup_membership") from None
    rows = [row for row in text.splitlines() if row]
    _require(len(rows) == 1 and rows[0].startswith("0::/"), "membership")
    value = rows[0][3:]
    _require(
        value.startswith("/")
        and len(value) <= 2048
        and "\x00" not in value
        and "\\" not in value
        and all(part not in ("", ".", "..") for part in value.split("/")[1:]),
        "membership",
    )
    return value


def _read_regular(path: Path, *, allowed_uids=(0,)) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        _require(
            stat.S_ISREG(before.st_mode)
            and before.st_uid in allowed_uids
            and before.st_nlink == 1
            and 0 <= before.st_size <= MAX_FILE,
            "file",
        )
        raw = bytearray()
        while True:
            chunk = os.read(fd, min(1024, MAX_FILE - len(raw) + 1))
            if not chunk:
                break
            raw.extend(chunk)
            _require(len(raw) <= MAX_FILE, "file")
        after = os.fstat(fd)
        _require(
            (
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
            )
            and (before.st_size == 0 or len(raw) == before.st_size)
            and 0 < len(raw) <= MAX_FILE,
            "drift",
        )
        return bytes(raw)
    finally:
        os.close(fd)


def parse_scalar(raw: bytes) -> int:
    _require(type(raw) is bytes and len(raw) <= 64, "scalar")
    try:
        text = raw.decode("ascii", "strict").strip()
    except UnicodeError:
        raise Refused("reader_cgroup_scalar") from None
    _require(re.fullmatch(r"[0-9]+", text) is not None, "scalar")
    value = int(text)
    _require(0 <= value <= 2**63 - 1, "scalar")
    return value


def parse_cpu(raw: bytes) -> tuple[int, int]:
    _require(type(raw) is bytes and len(raw) <= 64, "cpu")
    try:
        text = raw.decode("ascii", "strict").strip()
    except UnicodeError:
        raise Refused("reader_cgroup_cpu") from None
    fields = text.split()
    _require(len(fields) == 2 and all(re.fullmatch(r"[0-9]+", x) for x in fields), "cpu")
    quota, period = map(int, fields)
    _require(quota == container.CPU_QUOTA and period == container.CPU_PERIOD, "cpu")
    return quota, period


def validate_values(values: dict[str, bytes]) -> dict:
    required = {"memory.max", "memory.swap.max", "pids.max", "pids.current", "cpu.max"}
    _require(type(values) is dict and set(values) == required, "shape")
    memory = parse_scalar(values["memory.max"])
    swap = parse_scalar(values["memory.swap.max"])
    pids = parse_scalar(values["pids.max"])
    current = parse_scalar(values["pids.current"])
    quota, period = parse_cpu(values["cpu.max"])
    _require(memory == container.MEMORY, "memory")
    _require(swap == 0, "swap")
    _require(pids == container.PIDS and 1 <= current <= container.PIDS, "pids")
    return {
        "schema": "zelo-media-reader-cgroup-v1",
        "memory_max": memory,
        "memory_swap_max": swap,
        "pids_max": pids,
        "pids_current": current,
        "cpu_quota": quota,
        "cpu_period": period,
        "aggregate_cgroup_verified": True,
        "runtime_authority": False,
    }


def snapshot(
    pid: int, *, proc_root: Path = Path("/proc"), cgroup_root: Path = Path("/sys/fs/cgroup")
):
    _require(type(pid) is int and 1 < pid <= 2**31 - 1, "pid")
    _require(isinstance(proc_root, Path) and proc_root.is_absolute(), "root")
    _require(isinstance(cgroup_root, Path) and cgroup_root.is_absolute(), "root")
    membership = parse_membership(
        _read_regular(proc_root / str(pid) / "cgroup", allowed_uids=(0, 65534))
    )
    relative = membership.removeprefix("/")
    base = cgroup_root / relative
    _require(base.resolve(strict=True).is_relative_to(cgroup_root.resolve(strict=True)), "path")
    values = {
        name: _read_regular(base / name)
        for name in ("memory.max", "memory.swap.max", "pids.max", "pids.current", "cpu.max")
    }
    result = validate_values(values)
    return {**result, "membership": membership}
