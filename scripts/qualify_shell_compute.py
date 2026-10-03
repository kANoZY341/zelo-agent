"""Explicit native built-in Shell isolation proof; no Core worker activation."""

import argparse
import asyncio
import hashlib
import json
import os
import re
import shlex
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from app.developer_process import DeveloperError
from qualification.shell_compute import (
    ENVIRONMENT,
    ERROR_BYTES,
    INPUT_BYTES,
    MAX_SECONDS,
    OUTPUT_BYTES,
    SOURCES,
    arguments,
    run_probe,
)
from scripts.qualify_browser_renderer import command

STATUS = """while IFS= read -r line; do
case "$line" in
Uid:*|Gid:*|CapInh:*|CapPrm:*|CapEff:*|CapBnd:*|CapAmb:*|NoNewPrivs:*)
 printf '%s\\n' "$line" ;; esac
done </proc/self/status
printf '%s\\n' __LIMITS__
while IFS= read -r line; do printf '%s\\n' "$line"; done </proc/self/limits
printf '%s\\n' __NETWORK__
while IFS= read -r line; do printf '%s\\n' "$line"; done </proc/net/dev
"""


def check_status(text: str) -> None:
    uid, gid = os.geteuid(), os.getegid()
    if uid == 0:
        raise RuntimeError("shell_nonroot_required")
    for field, identity in (("Uid", uid), ("Gid", gid)):
        if not re.search(
            rf"^{field}:\s+{identity}\s+{identity}\s+{identity}\s+{identity}$", text, re.M
        ):
            raise RuntimeError("shell_guest_identity_mismatch")
    for expression in (
        *(rf"^{field}:\s+0+$" for field in ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb")),
        r"^NoNewPrivs:\s+1$",
    ):
        if not re.search(expression, text, re.M):
            raise RuntimeError("shell_guest_privileges_mismatch")
    for name, value in (
        ("Max processes", 0),
        ("Max open files", 32),
        ("Max file size", 0),
        ("Max core file size", 0),
        ("Max address space", 268435456),
        ("Max cpu time", 2),
    ):
        if not re.search(rf"^{name}\s+{value}\s+{value}\s+", text, re.M):
            raise RuntimeError("shell_guest_limits_mismatch")
    network = text.split("__NETWORK__\n", 1)[1]
    if any(line.split(":", 1)[0].strip() != "lo" for line in network.splitlines() if ":" in line):
        raise RuntimeError("shell_guest_network_mismatch")


async def exercise(root: Path, report: dict) -> None:
    children = []
    spawned = asyncio.Event()
    original = asyncio.create_subprocess_exec

    async def observed_spawn(*args, **kwargs):
        child = await original(*args, **kwargs)
        children.append(child)
        spawned.set()
        # No await after resource creation before returning it to its real owner.
        return child

    try:
        with patch("app.developer_process.asyncio.create_subprocess_exec", observed_spawn):
            await _exercise(root, report, spawned, children)
    finally:
        report["native_launchers"] = len(children)
        report["native_launchers_reaped"] = bool(children) and all(
            child.returncode is not None for child in children
        )
        if any(child.returncode is None for child in children):
            raise RuntimeError("shell_cleanup_unproven")


async def _exercise(root: Path, report: dict, spawned: asyncio.Event, children: list) -> None:
    cases = report["cases"]

    async def success(name, script, expected):
        result = await run_probe(script)
        if result.exit_code != 0 or result.stdout != expected or result.stderr:
            raise RuntimeError("shell_probe_expectation_failed")
        cases.append({"case": name, "passed": True})

    async def rejected(name, script):
        result = await run_probe(script)
        if result.exit_code == 0:
            raise RuntimeError("shell_probe_restriction_failed")
        cases.append({"case": name, "passed": True, "exit_code": result.exit_code})

    result = await run_probe(STATUS)
    if result.exit_code or result.stderr:
        raise RuntimeError("shell_guest_status_failed")
    status = result.stdout.decode("ascii")
    check_status(status)
    report["effective_guest_state"] = status
    cases.append({"case": "actual_nonroot_capability_limits_and_network_state", "passed": True})
    await success(
        "built_in_compute", "n=6; printf 'COMPUTE_%s\\n' \"$((n * 7))\"\n", b"COMPUTE_42\n"
    )
    await success(
        "ambient_credentials_absent",
        'test -z "${ZELO_OPERATOR_TOKEN-}${DOCKER_HOST-}${LD_PRELOAD-}${ENV-}" && printf CLEAN\n',
        b"CLEAN",
    )
    canary = root / "host-canary.txt"
    canary.write_text("diagnostic-only-canary")
    canary.chmod(0o600)
    await success(
        "host_file_and_engine_absent",
        "test ! -e "
        + shlex.quote(str(canary))
        + " && test ! -e /engine && test ! -e /home && printf HIDDEN\n",
        b"HIDDEN",
    )
    await rejected("read_only_guest", "printf blocked >/tmp/new-file\n")
    await rejected("process_creation_denied", "printf harmless | read value\n")
    await rejected(
        "hard_limits_cannot_be_raised",
        "exec /usr/bin/prlimit --nproc=1:1 -- /usr/bin/dash -c 'printf ESCALATED'\n",
    )
    await rejected("host_shell_unavailable", "exec /bin/sh -c 'printf ESCAPED'\n")
    result = await run_probe("exec /usr/bin/dash -c " + shlex.quote(STATUS) + "\n")
    if result.exit_code or result.stderr:
        raise RuntimeError("shell_exec_state_failed")
    check_status(result.stdout.decode("ascii"))
    cases.append({"case": "exec_retains_identity_and_limits", "passed": True})
    for name, script, timeout, code in (
        (
            "stdout_bounded",
            "while :; do printf 'xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx'; done\n",
            2,
            "process_output_limit",
        ),
        (
            "stderr_bounded",
            "while :; do printf 'xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx' >&2; done\n",
            2,
            "process_output_limit",
        ),
        ("deadline_cleanup", "while :; do :; done\n", 0.2, "process_timeout"),
    ):
        previous = len(children)
        try:
            await run_probe(script, timeout=timeout)
        except DeveloperError as error:
            if str(error) != code:
                raise
        else:
            raise RuntimeError("shell_expected_failure_missing")
        if len(children) != previous + 1 or children[-1].returncode is None:
            raise RuntimeError("shell_failure_before_native_spawn_or_reap")
        cases.append({"case": name, "passed": True, "error_code": code})
    spawned.clear()
    operation = asyncio.create_task(run_probe("while :; do :; done\n"))
    try:
        await asyncio.wait_for(spawned.wait(), 1)
        operation.cancel()
        try:
            await operation
        except asyncio.CancelledError:
            pass
        else:
            raise RuntimeError("shell_cancellation_missing")
    finally:
        if not operation.done():
            operation.cancel()
        await asyncio.gather(operation, return_exceptions=True)
    cases.append({"case": "cancellation_after_native_spawn_cleanup", "passed": True})
    await success("fresh_probe_after_cleanup", "printf FRESH_PROBE\n", b"FRESH_PROBE")
    if canary.read_text() != "diagnostic-only-canary":
        raise RuntimeError("shell_host_canary_changed")


def qualify(base: Path) -> tuple[Path, bool]:
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="shell-proof-", dir=base)).resolve()
    report = {
        "status": "incomplete",
        "cases": [],
        "external_network_used": False,
        "core_worker_activated": False,
        "source_artifacts_are_independent_provenance": False,
        "started_at": datetime.now(UTC).isoformat(),
    }
    try:
        repo = str(Path(__file__).resolve().parents[1])
        report["git_commit"] = command(["/usr/bin/git", "-C", repo, "rev-parse", "HEAD"]).strip()
        report["worktree_clean"] = not bool(
            command(["/usr/bin/git", "-C", repo, "status", "--porcelain"])
        )
        policy = {
            "schema": "zelo-shell-qualification-v1",
            "artifact_handling": "verified-sealed-memfd-executable-and-guest-data-mounts-0500",
            "limits": {
                "stdin_bytes": INPUT_BYTES,
                "stdout_bytes": OUTPUT_BYTES,
                "stderr_bytes": ERROR_BYTES,
                "wall_seconds": MAX_SECONDS,
            },
            "arguments": arguments(),
            "sources": SOURCES,
            "environment": ENVIRONMENT,
        }
        report["policy_digest"] = hashlib.sha256(
            json.dumps(policy, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        report["policy"] = policy
        asyncio.run(exercise(root, report))
        report["status"] = "passed"
    except BaseException as error:
        report["status"] = "failed"
        report["failure_type"] = type(error).__name__
        if re.fullmatch(r"shell_[a-z_]{1,64}", str(error)):
            report["failure_code"] = str(error)
        if isinstance(error, DeveloperError):
            report["error_code"] = str(error)
    finally:
        report["finished_at"] = datetime.now(UTC).isoformat()
        data = json.dumps(report, indent=2).encode() + b"\n"
        if len(data) > 131072:
            raise RuntimeError("shell_report_limit")
        path = root / "result.json"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
    return path, report["status"] == "passed"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-native-shell-probes", action="store_true")
    args = parser.parse_args()
    if not args.allow_native_shell_probes:
        parser.error("Explicit --allow-native-shell-probes required; no guest started")
    path, passed = qualify(Path(__file__).resolve().parents[1] / "runtime/shell-qualification")
    print("EVIDENCE=" + str(path))
    if not passed:
        parser.exit(1, "native Shell qualification failed; inspect private report\n")


if __name__ == "__main__":
    main()
