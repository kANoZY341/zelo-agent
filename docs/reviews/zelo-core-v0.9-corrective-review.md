# Zelo Core v0.9 — Independent Corrective Review

Read-only, release-blocking re-review of the corrective fixes applied after the original v0.9
adversarial review. The reviewer is independent of the implementation agent; no implementation
code was modified and no prior summary or claim was taken on trust.

| Item | Value |
| --- | --- |
| Repository | `kANoZY341/zelo-agent` |
| Branch | `feat/v0.9-worker-authority` |
| Corrective head SHA reviewed | `3ed0472d14ea2da491f77111ac77d60f0bf0a9ce` |
| Originally reviewed SHA | `bd266a08487e7694188ce6410f37554767101451` |
| Corrective diff reviewed | `bd266a0..3ed0472` (7 app modules, 5 test modules, 1 review document) |
| Original report | `docs/reviews/zelo-core-v0.9-adversarial-review.md` |
| Environment | Linux, Python 3.12.3, git 2.55.0, Bubblewrap **absent**, `.venv` created for validation-manifest tests |

Analysis performed: full read of the corrective diff and of the resulting
`app/developer.py`, `app/runner.py`, `app/tasks.py`, `app/policy.py`, `app/worker_authority.py`,
`app/worker_registry.py`, `app/codex_worker.py`, `app/developer_sandbox.py`,
`app/developer_process.py`; `ruff check`, `ruff format --check`; the repository test suite; and
seven purpose-written adversarial probes executed against the **real** `DeveloperRuntime`,
`TaskService`, `Runner` and the real FastAPI operator API (probes were deleted after execution and
are not part of this branch).

---

## 1. Status of the accepted findings

| ID | Status | Severity now |
| --- | --- | --- |
| V9-01 authority semantic fingerprinting | **PARTIALLY RESOLVED** | MEDIUM |
| V9-02 claimed-but-unstarted cancellation | **RESOLVED** | — |
| V9-03 concurrent false boundary violation | **NOT REPRODUCED** | — (undefended invariant, see N-7) |
| V9-04 capacity contention must defer | **PARTIALLY RESOLVED** | HIGH (via new N-1/N-2/N-3) |
| V9-05 filesystem ceiling from trusted registry | **RESOLVED** | — |
| V9-09 truthful capacity audit and recovery | **PARTIALLY RESOLVED** | LOW |
| V9-11 expensive artifact binding off event loop | **PARTIALLY RESOLVED** | LOW |
| V9-13 no implicit Codex authority fallback | **RESOLVED** | — |
| V9-15 exception-safe worker slot ownership | **PARTIALLY RESOLVED** | LOW |

### V9-01 — authority semantic fingerprinting — PARTIALLY RESOLVED

What is genuinely fixed: `app/worker_authority.py` now builds `codex_permission_semantics()` and
`codex_cli_overrides()`, hashes both into `realized_authority_policy()`, bumps the schema to
`zelo-worker-authority-v2`, and — critically — `app/codex_worker.py::command_config` now *derives the
executed `-c` overrides from the same function that is hashed* (`codex_cli_overrides(profile)`).
The filesystem permission table, `approval_policy`, `web_search`, the `features.*` disable list and
the full `shell_environment_policy.set` (i.e. `developer_process.environment()`) are therefore inside
the fingerprint, and `authority_policy_version()` is now computed live on every call instead of being
frozen in a module constant, so there is no stale-constant TOCTOU in `Supervisor.select`/`matches`
(measured cost 53 µs per call — negligible at the 0.05 s poll interval).

What is **not** fixed: the bubblewrap argv actually executed by `app/developer_sandbox.py::sandbox`
is still not hashed. `sandbox_permission_semantics()` is a five-key hand-written *mirror*
(`repo`, `codex_mount`, `engine`, `service_network`, `host_network_unshared`) that no production code
path consults. The `SANDBOX_VERSION` string literal remains hand-maintained.

Reproduction (probe, passed):

```python
before = authority_policy_version()
monkeypatch.setattr(
    developer_sandbox, "sandbox", lambda repo, cmd, **kw: [argv[0], "--bind", "/", "/", *argv[1:]]
)
after = authority_policy_version()
assert before == after  # PASSES -> host root bind does not invalidate any approval
```

Removing `--unshare-net`, `--unshare-pid`, changing `--ro-bind` of `REPO/.git` to `--bind`, or binding
`/etc` changes real worker authority with a byte-identical policy fingerprint, registry version and
approval snapshot. `docs/v0.9-worker-authority-design.md:30` still asserts the fingerprint "hashes the
complete permission semantics"; the corrective diff did not touch the design document, so the
document continues to overclaim relative to the code.

### V9-02 — claimed-but-unstarted cancellation — RESOLVED

`Runner._loop` now attaches `_finalize_unstarted_operation` as a done callback before the operation is
registered, and the `await asyncio.sleep(0)` ordering hack is gone. Verified against the **production
`_loop`** (the repository's own regression test does not — see §3):

```
PROBE cancel-before-start: cancelled {}
```

A canceller coroutine already resident in the ready queue cancels the freshly created operation before
its first step; the task nevertheless reaches `CANCELLED`, the attempt is terminal, and
`runner.operations == {}`. The guards (`operation.cancelled()`, identity check against the current
`operations` entry) correctly avoid a double `finish()` when a *started* operation is marked cancelled
because `_must_cancel` was set while it was running. Shutdown/`stop()` and `runner_failed` paths reuse
the same callback with the correct `_cancellation_outcome()`.

### V9-03 — concurrent false `developer_boundary_violation` — NOT REPRODUCED

Independently re-tested in the actual Zelo execution environment rather than accepting either party's
claim. `app/developer_process.py::environment()` sets `GIT_OPTIONAL_LOCKS=0` (present since v0.6), and
every git invocation in `app/developer.py` and `app/promotion_git.py` goes through `run_process`, which
defaults to that environment; the only custom envs (`_commit_environment`, Codex `CODEX_HOME`) are
derived from `environment()` and keep the variable.

Measured on git 2.55.0 with a worktree deliberately given stale stat data:

```
GIT_OPTIONAL_LOCKS=0 status: index mtime changed: False
without the var:          index mtime changed: True
stale-stat primary digest stable (with the var): True
digest changed after one status without the var: True
```

Whole-snapshot concurrency was also exercised: 8 concurrent local-transport `git fetch` operations from
the source repository plus 6 concurrent full snapshot computations produced identical digests
(`digest stable under concurrent fetches: True`, `concurrent snapshots agree: True`). The original
finding's measurement was made without Zelo's process environment, so the reported failure mode does
not occur in the shipped configuration.

This is **not reproduced**, not "impossible": the entire protection is one environment-variable line,
`tree_digest` still hashes `st_mtime_ns` of every `.git` entry, and nothing in the suite asserts the
invariant (see N-7).

### V9-04 — capacity contention must defer without consuming approval — PARTIALLY RESOLVED

The deferral mechanism itself is correct: `DeveloperRuntime.run` re-raises the capacity error after its
`finally`, `Runner._run` routes `developer_worker_capacity_exhausted` to the new `TaskService.defer`,
which returns the task to `PENDING` with `error=None`, marks the attempt `INTERRUPTED`, audits
`execution_deferred`/`developer_deferred`, and leaves the approval `approved`. Verified with the real
runtime and a foreign-held slot: the task returns to `PENDING`, the approval survives, and the task is
re-claimed and completes once the slot is released.

However, the *integration* of the fix introduces three new defects, two of them release-blocking:
the state-machine widening required by `defer()` opens an unrelated scheduler-poisoning path (**N-1**),
the retry has no backoff or attempt bound (**N-2**), and `defer()` — unlike `finish()` — silently
discards a pending operator cancellation (**N-3**). The lock namespace is still
`ROOT/runtime/developer-worker-<worker_id>-<slot>.lock`, i.e. still shared across runtime databases and
workspace roots; that part of the original finding is unchanged, and is now the *trigger* for N-2.

### V9-05 — filesystem ceiling from the current trusted registry — RESOLVED

`worker_lock(worker_id, max_concurrency)` no longer reads the persisted request. In
`DeveloperRuntime.run` the slot is acquired only after `self.registry.get(...)` and
`self.supervisor.matches(request.worker_selection)` succeed, using `selected.descriptor`. A persisted
descriptor claiming `max_concurrency=2` cannot widen a registry ceiling of 1 (repository test
`test_persisted_descriptor_cannot_override_registry_ceiling`, and the ordering is visible at
`app/developer.py:610-615`). The fork-based two-process test passes in this environment.

Residual (not a regression): `TaskService._claimable_task` still derives the *scheduler* ceiling from
the persisted `developer_spec` (`app/tasks.py:1205`). This is acceptable defence-in-depth only because
`compatible()`/`matches()` reject snapshots that disagree with the live registry before execution.

### V9-09 — truthful capacity audit events and recovery — PARTIALLY RESOLVED

`worker_capacity_claimed` is now emitted by the component that actually owns the slot, immediately
after `flock` succeeds, and `worker_capacity_released` immediately after `lock.close()`;
`TaskService.claim`/`finish` no longer emit scheduler-intent events. `recover()` emits a release only
for attempts whose claim has no matching release (`_attempt_has_unreleased_capacity`), which is truthful
because process death releases `flock` at the kernel level. Verified end-to-end on a real run that
fails after binding: exactly one `worker_capacity_claimed` and one `worker_capacity_released`.

Residual gap: the release event is *not* emitted on the repeated-cancellation path (see N-5), so a
long-lived process can hold an unbalanced ledger entry until the next restart. The claim and release
events are also written in separate transactions from the `flock`/`close` calls, so a crash in the
microsecond window between them mis-states ownership; `recover()` resolves this conservatively.

### V9-11 — expensive artifact binding/hashing off the event loop — PARTIALLY RESOLVED

`selected.worker.bind(request.worker).__enter__` (which performs `pin_executable` + `pin_artifact`,
i.e. two full SHA-256 passes over binaries bounded at 512 MiB, plus memfd writes) now runs in
`asyncio.to_thread`. `tree_digest` and `validation_identity` remain threaded. `PinnedExecutable.verify`
is a seal check, not a hash, so the hot loop path is clean. The repository's loop-liveness test
(`test_runtime_offloads_worker_binding_from_event_loop`) genuinely fails if the work moves back inline.

Residual: the manual `__enter__`/`__exit__` pairing replaced `ExitStack.enter_context`, and a
cancellation delivered during the threaded `__enter__` leaves the context permanently entered (N-4).

### V9-13 — no implicit Codex authority fallback — RESOLVED

`command_config`, `argv`, `probe` and `local_command_probe` all require an explicit
`WorkerAuthorityProfile`; a repository-wide search finds no remaining
`profile or authority_profile((...))` fallback and no `WorkerAuthorityProfile | None` parameter in
`app/`. `local_command_probe`'s signature reordering (`profile` before `primary`) is consistently
applied at its only caller.

Note (pre-existing, previously filed as V9-14 and not in the accepted set): `request_authority()` still
validates the snapshot against the policy version only, not against the registry; the registry binding
remains a `DeveloperRuntime.run` precondition and is undocumented at the function.

### V9-15 — exception-safe worker slot ownership — PARTIALLY RESOLVED

`_open_lock_stream` closes the raw fd if `os.fdopen` raises (covered by a new test), `emit
("developer_started")` moved inside the `try`, and the lock is now acquired inside the `try` with a
`lock is not None` release in the `finally`. An `emit` failure after acquisition now releases the slot.

Residual: the `finally` performs `await asyncio.to_thread(bound_worker_context.__exit__, ...)` *before*
`artifacts.close()` and `lock.close()`, so a cancellation arriving during that await skips all
remaining cleanup (N-5). Slot release then depends on CPython refcounting of the `run()` frame rather
than on structure — which is precisely the property V9-15 asked for.

---

## 2. New findings

### N-1 — `RUNNING → PENDING` widening lets `POST /tasks/{id}/retry` permanently poison the scheduler

* **Severity:** HIGH — **release-blocking**
* **Introduced by:** `app/policy.py` (`TRANSITIONS[State.RUNNING]` gained `State.PENDING` so that
  `TaskService.defer` can re-queue) interacting with `TaskService.retry` (`app/tasks.py:1034-1047`).
* **Mechanism:** `retry()` computes `target = AWAITING_APPROVAL` only for approval kinds; for `echo`,
  `tool_call` and `agent_run` the target is `PENDING`, and its sole guard is
  `validate_transition(task.status, target)`. Before this diff that call rejected a `RUNNING` task; now
  it is legal. The in-flight attempt is left `RUNNING` while the task row becomes `PENDING`.
* **Reproduction** (real `TaskService`, then the real HTTP API):

```
PROBE status after claim: running
PROBE API retry status: 202 pending                      # POST /tasks/{id}/retry, operator token
PROBE task one: pending [(1, running)]                   # orphan RUNNING attempt
PROBE after recover: pending [(1, running)]              # restart does NOT clean it up
PROBE claim raised: IntegrityError  (sqlite3.IntegrityError) non-developer running attempt must be exclusive
PROBE finish raised: DomainError Cannot transition from pending to succeeded
```

* **Consequences:** (a) the original execution can never persist a terminal state; (b) every subsequent
  `TaskService.claim()` — for *any* non-developer task — raises `sqlite3.IntegrityError` from the
  exclusivity trigger; inside `Runner._loop` that is an unhandled `Exception`, so the runner sets
  `failed = True` and readiness drops permanently; (c) `recover()` only scans tasks whose *status* is
  `RUNNING`, so a process restart does not clear the orphan attempt and the runner dies again on its
  first claim. Recovery requires manual database surgery. A single authenticated operator call is
  sufficient; no race is needed.
* **Do the new tests catch it?** No. `tests/test_lifecycle.py` was *changed to assert that
  `RUNNING → PENDING` is valid*, which converts the regression into an expected behaviour without any
  test of what `retry()` now accepts.
* **Recommended fix:** do not widen the global transition table for `defer()`; give `defer()` its own
  guarded transition (or a distinct `DEFERRED` state), and/or reject `retry()` when `task.status ==
  State.RUNNING` explicitly. Regression tests: `retry()` on a RUNNING `echo`/`tool_call`/`agent_run`
  must raise `DomainError`, and `recover()` must clear orphan RUNNING attempts on non-RUNNING tasks.

### N-2 — Capacity deferral retries in a hot loop with no backoff and no attempt bound

* **Severity:** HIGH — **release-blocking** (availability / audit integrity)
* **File/function:** `Runner._run` (`wake_scheduler = False`), `Runner._loop`
  (`timeout=self.service.settings.runner_poll_seconds`, production default **0.05 s**),
  `TaskService.defer`, `DeveloperRuntime.run`.
* **Mechanism:** suppressing the wake event only defers the retry by one poll interval. A deferred task
  is re-claimed ~20×/s for as long as the foreign slot holder exists. Each retry creates a new
  `TaskAttempt`, writes `attempt_created`, `execution_started`, `developer_started`,
  `execution_deferred`, `developer_deferred` audit rows, and — because the slot is acquired *after*
  `primary_snapshot()` and re-verified in `finalize()` — performs **two full primary-repository
  snapshots** (≈9 git subprocesses plus full tracked-content and `.git` hashing) per attempt.
* **Reproduction** (real `DeveloperRuntime`, a foreign-held slot, production poll interval):

```
PROBE retry churn: 28 attempts in 3.01 s; 203 audit events; status running
```

  ≈9.3 attempts/s and ≈68 audit rows/s on a two-file fixture repository. Extrapolated over the default
  one-hour approval TTL: ~33 000 attempts, ~240 000 audit rows and ~300 000 git subprocesses for a
  single blocked task, on the host that also serves the API. `GET /tasks/{id}` embeds every attempt, so
  the task response grows without bound as well.
* **Do the new tests catch it?** No — and the test that claims to is misleading:
  `test_real_runtime_slot_contention_defers_and_retries_without_hot_loop` overrides
  `runner_poll_seconds` to `0.2` and then asserts no second attempt appears within `await
  asyncio.sleep(0.05)`. It asserts that 0.05 s < 0.2 s; it cannot fail for any implementation that polls.
* **Recommended fix:** exponential backoff keyed on consecutive `developer_worker_capacity_exhausted`
  deferrals (persisted on the task/attempt), a deferral counter that stops re-claiming after N
  attempts, and/or cheap slot pre-checks before the expensive snapshot. Regression test: hold a slot for
  several seconds at the default poll interval and assert the attempt count stays within a small bound.

### N-3 — `defer()` discards a pending operator cancellation that `finish()` would honour

* **Severity:** MEDIUM
* **File/function:** `TaskService.defer` (`app/tasks.py`) vs `TaskService.finish`
  (`app/tasks.py:1432-1435`), `TaskService.cancel` (`app/tasks.py:1000-1032`),
  `TaskService._claimable_task` (no `cancel_requested` filter).
* **Mechanism:** `cancel()` on a RUNNING task sets `cancel_requested = True` and returns 200/202 to the
  operator. If the execution then ends in capacity exhaustion, `defer()` re-queues the task to `PENDING`
  without consulting or clearing `cancel_requested`; `claim()` does not filter on it either.
* **Reproduction:**

```
PROBE defer/cancel: pending True
PROBE reclaimed after cancel: True      # cancelled developer work is claimed and executed again
```

* **Consequence:** approved developer work that an operator explicitly stopped is silently re-executed,
  and the task carries a permanently stale `cancel_requested = True` into its next (possibly successful)
  run. This violates the v0.8/v0.9 property that cancellation is authoritative.
* **Recommended fix:** in `defer()`, if `task.cancel_requested`, complete the cancellation (mirror
  `finish()`'s `State.CANCELLED` / `cancelled_by_operator` branch) instead of re-queueing; alternatively
  filter `cancel_requested` in `_claimable_task`.

### N-4 — Cancellation during the threaded bind leaves worker artifacts pinned with no unbind

* **Severity:** MEDIUM
* **File/function:** `DeveloperRuntime.run` — `bound_worker = await asyncio.to_thread(
  bound_worker_context.__enter__)`; `bound_worker_entered` is set only *after* the await returns.
* **Mechanism:** `asyncio.to_thread` cannot cancel a thread that has started. When the operation is
  cancelled during binding, the awaiting coroutine raises `CancelledError`, `bound_worker_entered`
  stays `False`, and the `finally` therefore never calls `__exit__` — while the thread goes on to
  complete `pin_executable` + `pin_artifact` (two memfds, each bounded at 512 MiB, plus their fds).
* **Reproduction:**

```
PROBE bind leak: {'entered': 1, 'exited': 0}     # still 0 after an 0.8 s grace period
```

* **Consequence:** for the production `CodexCliWorker.bind` (a `@contextmanager` generator) the memfds
  are reclaimed only when the generator is garbage-collected, i.e. cleanup is refcount-dependent rather
  than structural — exactly the class of defect V9-15 raised. Repeated cancel-during-bind cycles hold
  hundreds of MiB of anonymous memory for an unbounded window.
* **Recommended fix:** enter and exit the context entirely inside one thread callable (or use
  `contextlib.AsyncExitStack` with a threaded enter that registers its exit atomically), and shield the
  enter from cancellation.

### N-5 — A cancellation during the `finally`'s awaited unbind skips slot release and its audit event

* **Severity:** MEDIUM
* **File/function:** `DeveloperRuntime.run` `finally` — `await asyncio.to_thread(
  bound_worker_context.__exit__, ...)` precedes `artifacts.close()`, `lock.close()` and the
  `worker_capacity_released` emit; only `finalize()` is protected by `uncancellable()`.
* **Reproduction** (repeated cancellations, e.g. operator cancel followed by `Runner.stop()`):

```
PROBE repeated cancels: ['developer_started', 'worker_capacity_claimed']   # no release event
```

* **Consequence:** the capacity ledger records a claim with no release while the process keeps running
  (V9-09 truthfulness regression on this path); `artifacts.close()` is skipped; the `flock` is released
  only when the `run()` frame is garbage-collected. Any future exception object that retains the frame
  turns this into a real slot leak, which then feeds N-2's hot loop.
* **Recommended fix:** wrap the whole `finally` cleanup in `uncancellable()`/`asyncio.shield`, and
  release the lock before any awaited cleanup (or via an `ExitStack` registered at acquisition time).

### N-6 — Design documentation not updated for the corrective behaviour

* **Severity:** LOW
* `docs/v0.9-worker-authority-design.md:30` still claims the fingerprint hashes "the complete permission
  semantics" (false for the bwrap argv, see V9-01), and the document describes neither the new
  defer-instead-of-fail semantics, nor the new `RUNNING → PENDING` transition, nor recovery-time capacity
  release. The corrective diff changed no design documentation.

### N-7 — The V9-03 invariant is undefended by tests

* **Severity:** LOW
* Nothing asserts `environment()["GIT_OPTIONAL_LOCKS"] == "0"`, and the new
  `test_concurrent_primary_snapshots_do_not_mutate_source_index` compares `.git/index` **bytes** only,
  while the boundary digest is sensitive to `st_mtime_ns` via `tree_digest`. Deleting one environment
  line, or a future git version that rewrites an identical index, reintroduces a HIGH-severity false
  `developer_boundary_violation` with a green suite. Demonstrated: with the variable removed the digest
  changes while the index bytes are unchanged in content terms.

---

## 3. Test-coverage weaknesses

1. **`test_cancel_before_first_operation_step_is_finalized` bypasses production wiring.** It creates the
   task, attaches `_finalize_unstarted_operation` and populates `runner.operations` by hand; deleting the
   `add_done_callback` in `Runner._loop` would leave it green. (My equivalent probe drives `runner.start()`
   and the real claim loop; the production path does behave correctly today.)
2. **`test_real_runtime_slot_contention_defers_and_retries_without_hot_loop` proves nothing about hot
   loops** (see N-2) and is skipped entirely without Bubblewrap; the only genuinely end-to-end deferral
   coverage is therefore environment-conditional.
3. **`test_interrupted_developer_recovery_releases_only_claimed_capacity` fabricates the claim event** via
   `service.model_event(...)` instead of executing a run that takes a slot, so it tests the recovery query
   against its own assumption rather than against runtime-emitted audit data.
4. **`test_authority_policy_matches_realized_permission_semantics` patches the hash input**
   (`worker_authority.codex_permission_semantics`), not a real enforcement literal. There is no golden-hash
   pin on the realized `-c` override list, and no coverage at all of `developer_sandbox.sandbox`.
5. **No two-real-execution concurrency test.** Every scheduler/concurrency test except the slot-lock tests
   still uses `CapacityRuntime`, a fake with no git, no snapshot and no filesystem. The original V9-03
   coverage gap is unchanged.
6. **No tests for any of N-1…N-5**: retry on a RUNNING task, defer versus `cancel_requested`, cancellation
   during bind, repeated cancellation during unbind, or attempt/audit growth under sustained contention.
7. **Environment sensitivity**: in a host without Bubblewrap, 51 of 426 tests fail and 75 skip; all
   observed failures were `bubblewrap_unavailable`-class environment failures. CI installs Bubblewrap, so
   this is a local-reproducibility note, not a CI gap — but it does mean the most safety-relevant tests
   never run on a developer machine that lacks the prerequisite.

---

## 4. Acceptance-criteria results

| # | Criterion | Result |
| --- | --- | --- |
| 1 | Unsupported authority fails at registry construction | PASS |
| 2 | Authority profile/policy version persisted in the approval snapshot | PASS |
| 3 | Changing authority policy/profile invalidates compatibility | PASS for Codex config semantics; **FAIL for the bwrap argv** (V9-01) |
| 4 | Codex execution derives permissions from the bound profile | PASS (no implicit fallback remains) |
| 5 | Scheduler claims never exceed `max_concurrency` | PASS |
| 6 | Different eligible workers run concurrently | PASS |
| 7 | Non-developer tasks remain exclusive | PASS in the scheduler, but the DB trigger can be turned into a permanent scheduler outage via N-1 |
| 8 | Cross-process slot locks enforce the same ceiling | PASS (registry-derived ceiling; fork test passes). Namespace still shared across databases/workspace roots |
| 9 | Explicit registry cannot expose a non-winning primary worker | PASS |
| 10 | Trusted snapshot hashing off the event loop | PASS for hashing; cleanup correctness regressed (N-4, N-5) |
| 11 | All v0.8 approval/source/artifact/validation protections intact | **FAIL** — approval, source, artifact and validation boundaries are intact, but the task-lifecycle guarantee regressed (N-1); operator cancellation authority regressed (N-3) |
| 12 | Ruff, Ruff format, full pytest suite pass | `ruff check` PASS, `ruff format --check` PASS (78 files); pytest not fully verifiable here (no Bubblewrap): 375 passed / 51 environment failures / 75 skipped |

---

## 5. Release-blocking findings

| ID | Severity | Summary |
| --- | --- | --- |
| N-1 | HIGH | `RUNNING → PENDING` widening makes `POST /tasks/{id}/retry` legal on a RUNNING non-approval task, creating an orphan RUNNING attempt that permanently breaks every later claim (`IntegrityError` → `runner.failed`) and survives restart because `recover()` never sees it. |
| N-2 | HIGH | Capacity deferral retries ~20×/s with no backoff and no attempt bound: measured 28 attempts and 203 audit rows in 3 s, each attempt performing two full primary snapshots; unbounded until approval TTL. |

Strongly recommended before release (not formally blocking, but each is a correctness or safety
regression against a stated v0.9 property):

| ID | Severity | Summary |
| --- | --- | --- |
| N-3 | MEDIUM | `defer()` discards a pending operator cancellation; cancelled developer work is re-claimed and executed. |
| V9-01 residue | MEDIUM | The executed bubblewrap argv is outside the authority fingerprint while the design document claims complete coverage. |
| N-4 | MEDIUM | Cancellation during the threaded bind leaves up to two 512 MiB memfd artifacts pinned with no `__exit__`. |
| N-5 | MEDIUM | Cancellation during the awaited unbind skips slot release and the `worker_capacity_released` audit event. |

Non-blocking and unchanged from the original report (not accepted for correction, re-confirmed present
at `3ed0472`): V9-06 (no global execution ceiling), V9-07 (NULL-unsafe UPDATE trigger), V9-08 (orphan
attempts bypass the trigger), V9-10 (unbounded developer overtaking → approval expiry, now aggravated by
N-2), V9-12 (cleanup no longer excludes running executions by lock), V9-14 (`request_authority()`
self-validation), V9-16 (per-poll revalidation cost).

---

## 6. Conclusion

The corrective work is real and, in several places, good: V9-02, V9-05 and V9-13 are genuinely fixed and
verified against production code paths; V9-01, V9-09, V9-11 and V9-15 are materially improved; and V9-03
does not reproduce in the actual Zelo execution environment, which I confirmed independently rather than
accepting either claim.

However, the V9-04 fix was integrated in a way that introduced two release-blocking regressions — a
state-machine widening that lets a single authenticated operator call permanently disable the scheduler
(N-1), and an unbounded, un-backed-off retry loop that turns a benign capacity condition into a
self-inflicted load and audit-bloat event (N-2) — plus a cancellation-authority regression (N-3) and two
cancellation-path cleanup regressions (N-4, N-5). Several of the new tests restate implementation
assumptions rather than exercising production behaviour, and one of them ("without hot loop") is
tautological.

**Release readiness: NOT READY.** `3ed0472` must not be merged or released as v0.9 until N-1 and N-2 are
fixed with genuine regression tests, and N-3/N-4/N-5 and the V9-01 sandbox-argv residue are either fixed
or explicitly accepted with the design document corrected to match the code.

### Reviewed SHA

`3ed0472d14ea2da491f77111ac77d60f0bf0a9ce`
