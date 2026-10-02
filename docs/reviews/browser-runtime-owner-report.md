# Browser runtime owner qualification report

Date: 2 October 2026. Branch: `feat/v0.15-browser-runtime-qualification`.
Base: `939cbd7`; implementation was developed after planning HEAD `5161c6c`.
This is the delegated implementation report; parent follow-through is recorded below.

## Implemented and checked

- Host-scoped capacity ONE, nonblocking Linux flock, no admission queue. Multiple
  controller instances and independent Python processes contend on the same files.
- Existing operator-provisioned runtime directory must be owned by the effective
  UID with mode 0700. Files must be regular, same-UID, mode 0600, single-link and
  opened without following final-component symlinks. Directory final symlinks,
  file symlinks, hardlinks, wrong modes, directories and FIFOs are rejected.
  File descriptors are closed on denied admission and terminal lease release.
- A 64-hex unpredictable lease identity and validated 64-hex policy digest are
  recorded in a small fixed-schema marker, fsynced together with its directory
  before factory entry. A sync failure prevents factory entry. Persistent lock
  and marker inodes are never unlinked by this code.
- Marker clearing is allowed only after trusted backend cleanup confirmation.
  SIGKILL of the test-owned process releases flock but leaves a fence that a new
  independent process rejects. No PID polling, TTL, arbitrary path deletion or
  unknown-resource recovery exists.
- One-shot owner shields asynchronous creation, retains a late handle and performs
  exactly one cleanup call. Close cancels active work once and shares the lifecycle
  task. A module-level strong-reference set retains pending lifecycle tasks, with
  at most one per owner, until completion. Repeated cancellation does not cancel
  cleanup. Grace expiry retains capacity; eventual confirmed cleanup releases it.
  Creation failures and unconfirmed cleanup permanently fence that directory.
- Evidence returns only after cleanup; cancellation or concurrent close prevents
  successful evidence. Failed work still cleans up. Closed/used owners cannot run
  again. Deadline expiry initiates close; an unreturned handle remains owned.
- Frozen contracts hash every field with canonical JSON and normalized deadline
  numbers. Image identity must be `sha256:<64 lowercase hex>`; all other identity
  fields are lowercase SHA-256 digests, including approved-origin policy identity.
  Tests mutate configurable fields and reject bools, NaN/infinity, enormous numbers,
  negative/zero-invalid values and tag/malformed identities.

Resource ceilings: aggregate memory 16 MiB–2 GiB; PID/task count 1–256;
CPU period 1,000–1,000,000 microseconds; quota 1,000–2,000,000 microseconds,
also at most twice the period; swap exactly zero; tmpfs positive and at most
256 MiB and the memory ceiling; deadline positive and at most 30 seconds.
All budgets are explicit constructor arguments. Cleanup wait grace is positive
and at most one second per close call; repeated close does not create another task.

## Actual verification

Used `/home/zelo/zelo/core/.venv/bin/python` throughout validation:

```text
-m pytest -q tests/test_browser_runtime_owner.py
43 passed, 3 warnings in 0.41s

-m ruff check qualification/admission.py qualification/resource_owner.py qualification/runtime_contracts.py tests/test_browser_runtime_owner.py
All checks passed!

-m ruff format --check qualification/admission.py qualification/resource_owner.py qualification/runtime_contracts.py tests/test_browser_runtime_owner.py
4 files already formatted

git diff --check
Passed
```

The first focused run found one incorrect expected exception in a test
(`dataclasses.replace` raises ValueError for a fixed field). That assertion was
corrected; subsequent added deadline/failure/persistence tests also pass. Initial
Ruff line-length findings were resolved by formatting. The final pytest warnings
are existing Starlette/httpx, anyio alias and Pydantic schema-shadowing warnings
from repository test collection; no dependency changes were made.

## Trust assumptions and remaining gates

The directory's ancestors, mounts, local filesystem durability/flock semantics and
same-UID operator are trusted. This does not defend against an operator replacing
inodes or choosing another directory. Production must designate exactly one canonical
persistent host directory for all callers, forbid per-task directory selection,
and preserve it across restart. This slice deliberately adds no production setting.
Operator reconciliation of a leftover marker is a future gate, requiring explicit
proof that all associated resources are gone. Do not simply remove a marker.

The backend contract is trusted qualification code: it must track every created
resource and return exactly True only after full stop/reap. Factories raising before
returning a handle are conservatively ambiguous. The owner cannot discover or clean
unknown resources. Keep the event loop alive for retained tasks; event-loop/process
shutdown may interrupt cleanup, leaving the persistent fence. No hard termination
guarantee exists for uninterruptible OS work. Deadline plus bounded close grace limits
the caller's wait, not the lifetime of retained cleanup. General disk/filesystem or
host failures are not certified by these tests.

Contracts express requirements only. No cgroup, namespace, seccomp, native browser
sandbox, network boundary, container launcher, image provenance verification or
Docker socket access is implemented here. Digest shape validation is not artifact
verification. Existing broker and DNS behavior is untouched and not integrated with
this owner yet. Container/native-renderer/resource-exhaustion and broker transport
qualification remain parent-owned gates, as do independent review, full pytest and
exact-head CI. No full suite, Docker calls, services, containers, installs, host
configuration changes, commits, pushes or PR actions were performed. No Browser
Worker/task/capability/default AgentSpec is activated. UI stays parked; canonical
milestone order is unchanged.

## Parent host follow-through

After the delegated implementation, the serial host suite passed 242 tests,
including existing broker/DNS/API/project tests, the 43 owner tests, 28 optional
resource-harness validation tests and two real-child lifecycle integration tests.
Diff, Ruff lint and formatting passed. A separate optional scratch-container
fixture proved actual host cgroup memory/PID/CPU/tmpfs constraints; see
`browser-resource-host-report.md`. This does not establish native browser container
compatibility or integrated broker/renderer activation. The delegated report's
no-container-actions statement describes that delegate's own work only.
