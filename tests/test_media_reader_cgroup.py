"""Offline cgroup-v2 contract tests for the Media native-reader gate."""

from pathlib import Path

import pytest

from qualification import media_reader_cgroup as m
from qualification import media_reader_container as container


def values():
    return {
        "memory.max": f"{container.MEMORY}\n".encode(),
        "memory.swap.max": b"0\n",
        "pids.max": f"{container.PIDS}\n".encode(),
        "pids.current": b"3\n",
        "cpu.max": f"{container.CPU_QUOTA} {container.CPU_PERIOD}\n".encode(),
    }


def test_membership_exact_v2_path():
    assert (
        m.parse_membership(b"0::/system.slice/docker-fixed.scope\n")
        == "/system.slice/docker-fixed.scope"
    )


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"1:name=/x\n",
        b"0::/a/../b\n",
        b"0:://double\n",
        b"0::/x\n0::/y\n",
        b"\xff",
    ],
)
def test_membership_refuses_ambiguous_or_non_v2(raw):
    with pytest.raises(m.Refused):
        m.parse_membership(raw)


def test_expected_limits_pass_and_remain_non_authoritative():
    result = m.validate_values(values())
    assert result["aggregate_cgroup_verified"] is True
    assert result["runtime_authority"] is False
    assert result["memory_max"] == container.MEMORY
    assert result["memory_swap_max"] == 0
    assert result["pids_max"] == container.PIDS
    assert result["pids_current"] == 3
    assert (result["cpu_quota"], result["cpu_period"]) == (
        container.CPU_QUOTA,
        container.CPU_PERIOD,
    )


@pytest.mark.parametrize(
    "name,raw,code",
    [
        ("memory.max", b"max\n", "scalar"),
        ("memory.max", b"123\n", "memory"),
        ("memory.swap.max", b"1\n", "swap"),
        ("pids.max", b"9\n", "pids"),
        ("pids.current", b"0\n", "pids"),
        ("pids.current", b"9\n", "pids"),
        ("cpu.max", b"max 100000\n", "cpu"),
        ("cpu.max", b"60000 100000\n", "cpu"),
    ],
)
def test_limit_drift_refused(name, raw, code):
    changed = values()
    changed[name] = raw
    with pytest.raises(m.Refused, match=f"reader_cgroup_{code}"):
        m.validate_values(changed)


def test_shape_requires_exact_files():
    changed = values()
    changed["memory.current"] = b"1\n"
    with pytest.raises(m.Refused, match="reader_cgroup_shape"):
        m.validate_values(changed)


def test_snapshot_reads_only_fixed_membership_and_five_files(monkeypatch):
    requested = []
    uid_policies = []
    proc = Path("/proc")
    cg = Path("/sys/fs/cgroup")
    mapping = {
        proc / "43210" / "cgroup": b"0::/docker/fixed\n",
        cg / "docker/fixed/memory.max": f"{container.MEMORY}\n".encode(),
        cg / "docker/fixed/memory.swap.max": b"0\n",
        cg / "docker/fixed/pids.max": f"{container.PIDS}\n".encode(),
        cg / "docker/fixed/pids.current": b"2\n",
        cg / "docker/fixed/cpu.max": f"{container.CPU_QUOTA} {container.CPU_PERIOD}\n".encode(),
    }

    def read(path, *, allowed_uids=(0,)):
        requested.append(path)
        uid_policies.append(allowed_uids)
        return mapping[path]

    monkeypatch.setattr(m, "_read_regular", read)
    monkeypatch.setattr(Path, "resolve", lambda self, strict=False: self)
    result = m.snapshot(43210)
    assert result["membership"] == "/docker/fixed"
    assert requested == list(mapping)
    assert uid_policies[0] == (0, 65534)
    assert all(policy == (0,) for policy in uid_policies[1:])


@pytest.mark.parametrize("pid", [0, 1, -1, 2**31])
def test_snapshot_refuses_invalid_pid(pid):
    with pytest.raises(m.Refused, match="reader_cgroup_pid"):
        m.snapshot(pid)


def test_no_product_import():
    found = []
    for path in Path("app").rglob("*.py"):
        if "media_reader_cgroup" in path.read_text():
            found.append(path)
    assert found == []
