# Zelo Core v0.9 — Independent Adversarial Security and Correctness Review

Read-only, release-blocking review of Worker Authority and Multi-Worker Runtime.

| Item | Value |
| --- | --- |
| Repository | `kANoZY341/zelo-agent` |
| Base ref | `main` |
| Base SHA | `e4610fd` (`Zelo Core v0.8.0 release prep (#10)`) |
| Reviewed head branch | `feat/v0.9-worker-authority` |
| Reviewed head SHA | `bd266a08487e7694188ce6410f37554767101451` |
| Diff reviewed | `main..bd266a08487e7694188ce6410f37554767101451` (14 files, +952/-97) |
| Reported pre-checks | `git diff --check`, `ruff check .`, `ruff format --check .`, 485 pytest passed |
| Reviewer posture | Adversarial. Passing tests were treated as evidence of nothing. |

No implementation, test, configuration or migration file was modified by this review.
This document is the only file added.

---

## 0. What v0.9 actually changes

* `app/worker_authority.py` (new): frozen `WorkerAuthorityProfile`, a single production profile
  `workspace-write-no-network-v1`, a content fingerprint `AUTHORITY_POLICY_VERSION`, plus
  `authority_profile()` and `authority_snapshot_matches()`.
* `app/worker_registry.py`: registry bumped to `zelo-worker-registry-v2`, resolves each descriptor
  authority at construction (fail closed), folds the authority policy version and resolved profile
  name into the content-derived registry version, and extends `Supervisor.matches()` with authority
  checks.
* `app/developer_contracts.py`: `WorkerSelectionSnapshot` gains `authority_profile` and
  `authority_policy_version`.
* `app/codex_worker.py`: `command_config(profile)` derives the Codex filesystem/network permission
  table from the profile; `request_authority()` re-derives the profile from the approved snapshot
  before any model execution.
* `app/developer.py`: explicit-registry construction now derives the primary worker from the
  Supervisor routing winner; the global `developer-execution.lock` is replaced by per-worker slot
  locks (`developer-worker-<id>-<slot>.lock`) plus a separate `developer-maintenance.lock`; several
  synchronous hashes moved to `asyncio.to_thread`.
* `app/runner.py`: single `operation`/`active_id` replaced by an `operations` dict, greedy inner
  claim loop, `failed` flag, and `runner_failed` interruption semantics.
* `app/tasks.py`: `_claimable_task()` implements developer-only concurrency keyed by
  `worker_id`/`max_concurrency`; capacity audit events added.
* `migrations/versions/0009_worker_concurrency.py`: drops the global single-running-attempt index,
  adds a per-task partial unique index and two exclusivity triggers.

Important deployment fact used throughout this review: production assembly
(`app/main.py:62`) never passes an explicit registry, so `descriptor_for_worker()` yields
`worker_id="codex_core"`, `max_concurrency=1`. Real multi-worker concurrency is today reachable
only from tests or future assembly changes. That lowers *current* exploitability of several
findings but does not lower their release risk, because v0.9's stated mission is precisely to make
a second production worker safe.

---

## 1. Findings

### V9-01 — Authority-policy fingerprint does not cover the permission semantics it claims to cover

* **Severity:** HIGH
* **File/function:** `app/worker_authority.py::authority_policy_fingerprint` /
  `AUTHORITY_POLICY_VERSION`; `app/codex_worker.py::command_config`;
  `app/codex_worker.py::CONFIG_VERSION`; `app/developer_sandbox.py::SANDBOX_VERSION`;
  `docs/v0.9-worker-authority-design.md` ("hashes the complete permission semantics").
* **Exact failure scenario:** The fingerprint hashes only the four dataclass fields of each profile
  (`name`, `authority`, `workspace_write`, `network`). The *actual* enforced permission semantics
  live in `command_config()` — the filesystem table (`":minimal"=read`, `/opt/zelo-codex=read`,
  `REPO=write|read`, `REPO/.git=read`, `ENGINE=deny`), `approval_policy="never"`,
  `web_search="disabled"`, and the `features.*=false` disable list — and in `sandbox()`. An engineer
  who changes `ENGINE: "deny"` to `"read"`, flips one `features.*` entry to `true`, removes
  `web_search="disabled"`, or relaxes a bind in `sandbox()` changes real worker authority while
  `AUTHORITY_POLICY_VERSION`, the registry version, `CONFIG_VERSION` and `SANDBOX_VERSION` all stay
  byte-identical (the latter two are hand-maintained string literals:
  `CONFIG_VERSION = "codex-zelo-v4-sealed"`, `SANDBOX_VERSION = "zelo-linux-v3-sealed-artifacts"`).
* **Why it matters:** The explicit v0.9 property is "permission-semantic changes must invalidate old
  approvals even if a human forgets to bump a version string." For everything except the two boolean
  flags, that property is *not* satisfied — an already-approved `DeveloperRequest` executes under
  materially weakened authority with no re-approval. The design document asserts the stronger
  property, which makes the gap worse than a silent omission: reviewers will trust the claim.
* **Do existing tests catch it?** No. `test_authority_policy_fingerprint_binds_permission_semantics`
  only proves that flipping `network` changes the hash. Nothing hashes `command_config()` output or
  the bwrap argv.
* **Recommended fix:** Derive `AUTHORITY_POLICY_VERSION` from the canonicalized realized policy —
  e.g. hash `command_config(profile)` (with fds/paths normalized) and the `sandbox()` argv template
  per profile — instead of from the dataclass fields. Alternatively bind the request to a digest of
  the realized Codex `-c` overrides and re-verify it before execution.
* **Required regression test:** A test that mutates the `command_config` permission table (for
  example `ENGINE` deny→read) via monkeypatch of the table source and asserts that an existing
  approval becomes incompatible / `Supervisor.matches()` returns `False`; plus a golden-hash test
  pinning the realized override list so any edit forces an explicit fingerprint change.
* **Evidence:** `app/worker_authority.py:28-49` hashes `asdict(profile)` only;
  `app/codex_worker.py:79-125` builds the real policy from literals unreferenced by any hash.

### V9-02 — Claimed-but-unstarted runner operations can be cancelled before their coroutine runs, leaving tasks permanently RUNNING

* **Severity:** HIGH
* **File/function:** `app/runner.py::_loop` (inner claim loop and the `await asyncio.sleep(0)`
  mitigation), `app/runner.py::_run`, `app/runner.py::cancel`, `app/runner.py::stop`.
* **Exact failure scenario:** `_loop` claims a task (DB row already transitioned to `RUNNING`),
  creates `asyncio.create_task(self._run(execution))`, registers it in `self.operations`, then
  yields once with `await asyncio.sleep(0)` specifically to let `_run` start. That mitigation is not
  sufficient: if any other coroutine was already queued in the event loop ready queue before
  `create_task` (the `POST /tasks/{id}/cancel` route is `async def` and therefore runs on the loop,
  as does `Runner.stop()` during shutdown), it is dispatched *before* the new task's first step and
  calls `task.cancel()`. CPython then throws `CancelledError` into a not-yet-started coroutine, and
  the coroutine body never executes — so `_run`'s `try/except asyncio.CancelledError` never runs and
  `self.service.finish(...)` is never called. The task stays `RUNNING` forever with no attempt
  terminal state, and `self.operations` keeps a stale entry (its `finally` never runs either).
* **Why it matters:** Directly violates the stated v0.9 property "No claimed task may remain
  permanently RUNNING due to cancellation races" and "shutdown, cancellation, timeout and
  unexpected-exception paths must persist truthful terminal state." A permanently `RUNNING`
  developer task is also a permanent scheduler-capacity leak: `_claimable_task` counts it forever,
  so a `max_concurrency=1` worker becomes permanently unschedulable until a process restart runs
  `TaskService.recover()`. Audit truthfulness is broken in the meantime.
* **Do existing tests catch it?** No. `test_runner_failure_interrupts_already_claimed_developer_work`
  only covers cancellation *after* `_run` has started (the fake runtime awaits an event inside
  `run()`). There is no test that cancels between claim and first step, and no test asserting
  `operations` is empty after such a cancel.
* **Recommended fix:** Do not rely on ready-queue ordering. Persist the terminal state from a place
  that cannot be skipped — e.g. have `_loop` wrap creation so that cancellation before start is
  detected (`task.cancelled()` with the coroutine never entered) and finalize the execution from the
  loop, or use an explicit "started" `asyncio.Event` set as the first statement of `_run` and have
  `cancel`/`stop` await/queue cancellation until started, or catch `CancelledError` in the creator
  and call `service.finish(...)` there.
* **Required regression test:** Schedule a canceller coroutine into the ready queue before the claim
  (e.g. a task that `await asyncio.sleep(0)` then calls `runner.cancel(task_id)`), drive one claim,
  and assert the task reaches a terminal state (`CANCELLED`) and `runner.operations == {}`.
* **Evidence / reproduction:** Verified with CPython asyncio:
  cancelling a freshly created task before its first step produces an empty execution trace
  (`ran == []`), and a canceller already queued at the moment of `create_task` wins the race even
  with the `await asyncio.sleep(0)` mitigation (trace `['cancel']`, body never entered).

### V9-03 — Concurrent developer executions can trigger false `developer_boundary_violation` on the protected primary repository

* **Severity:** HIGH
* **File/function:** `app/developer.py::primary_snapshot` / `_primary_snapshot_digest`,
  `app/developer.py::run` (`before` capture and the `finalize()` comparison), enabled by the removal
  of the global `developer-execution.lock`.
* **Exact failure scenario:** `primary_snapshot()` runs `git rev-parse`, `git status --porcelain=v1
  -z`, `git show-ref`, `git ls-files` **against the primary source repository**, then hashes tracked
  contents and `tree_digest(source/.git)` including `st_mtime_ns` of every `.git` entry. `git status`
  is not a read-only operation: it refreshes and rewrites `.git/index` when worktree stat data is
  stale. With v0.9 concurrency (two developer executions, either two workers or one worker with
  `max_concurrency > 1`), execution B's `primary_snapshot` rewrites the primary `.git/index` between
  execution A's `before` snapshot and A's `finalize()` snapshot. A then raises
  `developer_boundary_violation`.
* **Why it matters:** `developer_boundary_violation` is Zelo's highest-signal protected-source alarm.
  v0.9 makes Zelo itself the perturbing actor, so the alarm becomes routinely false under the very
  feature v0.9 ships. Consequences: valid work is destroyed, audit truthfulness degrades, and
  operators are trained to ignore the single most important boundary signal. A secondary variant is
  `.git/index.lock` contention causing `git_operation_failed` from concurrent primary Git commands.
  In v0.8 the global execution lock made this impossible.
* **Do existing tests catch it?** No. Every new concurrency test substitutes `CapacityRuntime`, a
  fake with no Git, no snapshots and no filesystem. No test ever runs two real `DeveloperRuntime.run`
  executions against one primary repository.
* **Recommended fix:** Make primary-state capture non-mutating (`git --no-optional-locks status`,
  or `GIT_OPTIONAL_LOCKS=0`), and/or serialize primary snapshots with a dedicated primary-source
  lock shared by all concurrent executions, and/or exclude volatile paths (`index`, `index.lock`,
  `gc.log`) from `tree_digest` while hashing the index *content* deterministically.
* **Required regression test:** Two concurrent real developer executions against one fixture
  repository asserting both complete without `developer_boundary_violation`, plus a unit test
  asserting `primary_snapshot()` leaves `.git/index` `st_mtime_ns` unchanged.
* **Evidence / reproduction:** Measured on a scratch repository — `.git/index` mtime changes from
  `…143091152` to `…176091802` after a single `git status --porcelain=v1 -z` following a worktree
  `utime`. `app/developer.py` hashes exactly that value through
  `tree_digest(source / ".git")`.

### V9-04 — Filesystem slot exhaustion permanently FAILS an approved task instead of deferring it

* **Severity:** HIGH
* **File/function:** `app/developer.py::worker_lock` (raises `developer_worker_capacity_exhausted`
  *before* the `try:` block in `run`), `app/runner.py::_run` (`except DeveloperRuntimeError` →
  `State.FAILED`).
* **Exact failure scenario:** The slot lock namespace is `ROOT/runtime/developer-worker-<worker_id>-<slot>.lock`
  — keyed only by repository root and `worker_id`, deliberately shared "across runtime databases and
  configured workspace roots" (the comment stating this was deleted in this diff, but the behavior
  remains). Any second Zelo process, staging instance, or test run on the same checkout holding a
  slot causes a legitimately scheduled task to raise `developer_worker_capacity_exhausted`, which
  propagates out of the runtime and is recorded as `FAILED`. The approval is consumed; a human must
  re-approve.
* **Why it matters:** The design states "Scheduler capacity and runtime slot capacity are independent
  fail-closed checks." Failing closed is correct; *destroying the approval* is not. Two independent
  ceilings that can disagree plus a terminal-failure response turns a benign capacity condition into
  operator toil and an availability problem, and in the worst case into approval churn (repeatedly
  re-approving is exactly the behavior that erodes approval discipline).
* **Do existing tests catch it?** No. `test_runtime_worker_slots_enforce_descriptor_ceiling` asserts
  only that the third acquisition raises; no test covers the runner-level outcome, and no test covers
  two processes.
* **Recommended fix:** Treat slot exhaustion as a *retryable scheduling* condition — return the task
  to `PENDING` (or a deferred state) with an audit event rather than `FAILED`, and/or acquire the
  slot inside `TaskService.claim()` so DB accounting and filesystem accounting are one decision.
  Also namespace the lock directory by database identity so unrelated deployments cannot collide.
* **Required regression test:** Runner-level test asserting that a pre-held slot leads to a deferred
  or retried task (not a consumed approval), and a genuine two-process test (`multiprocessing`) of
  the slot ceiling.

### V9-05 — Slot ceiling is derived from the persisted request, so two processes can disagree on the ceiling

* **Severity:** MEDIUM
* **File/function:** `app/developer.py::worker_lock`
  (`for slot in range(descriptor.max_concurrency)` where `descriptor` comes from
  `request.worker_selection.descriptor`).
* **Exact failure scenario:** The filesystem ceiling is whatever the *request* says, not what the
  local trusted registry says. Process A (registry: `codex_core` max 2) and process B (registry:
  `codex_core` max 8, same `worker_id`, same checkout) each create slot files from their own
  requests; the effective host ceiling becomes 8. The lock is therefore not an independent trusted
  ceiling — it inherits the same (validated-elsewhere) value it is supposed to cross-check. Note the
  lock is acquired *before* `self.compatible(request, …)` runs inside `run()`, so for the duration of
  the window a stale snapshot's ceiling is honored.
* **Why it matters:** Acceptance criterion 8 promises that "a second Zelo process cannot bypass
  scheduler accounting." An independent check that reads its bound from the thing it is checking is
  not independent.
* **Do existing tests catch it?** No.
* **Recommended fix:** Take the slot count from the locally trusted registry descriptor
  (`self.registry.get(worker_id).descriptor.max_concurrency`) after `supervisor.matches()`, not from
  the persisted snapshot; or acquire the slot after the compatibility revalidation.
* **Required regression test:** Acquire a slot with a request whose `max_concurrency` exceeds the
  registry descriptor and assert the runtime ceiling follows the registry, not the request.

### V9-06 — No global execution ceiling: up to 32 × N concurrent sandboxed executions

* **Severity:** MEDIUM
* **File/function:** `app/tasks.py::_claimable_task`, `app/runner.py::_loop` (greedy inner loop),
  `app/developer_contracts.py::WorkerDescriptor.max_concurrency` (`gt=0, le=32`).
* **Exact failure scenario:** The claim loop keeps claiming while any pending developer task's worker
  has capacity. With several registered workers, the host can be asked to run `32 × N` concurrent
  bubblewrap + Codex executions, each performing a full local `git fetch` into a fresh workspace,
  full validation (`ruff`, `pytest`) and repeated tree hashing. There is no global cap, no disk
  guard, and no CPU/memory budget.
* **Why it matters:** Self-inflicted resource exhaustion on the box that also serves the API and owns
  the runner; under pressure the practical result is timeouts, spurious validation failures and
  interrupted approvals.
* **Do existing tests catch it?** No.
* **Recommended fix:** Add a settings-backed global developer concurrency ceiling enforced in
  `_claimable_task` (and mirrored by a global slot file), independent of per-worker capacity.
* **Required regression test:** Scheduler test with several workers asserting claims stop at the
  global ceiling.

### V9-07 — Update trigger is NULL-unsafe: a malformed attempt row can run non-developer work alongside developer work

* **Severity:** MEDIUM
* **File/function:** `migrations/versions/0009_worker_concurrency.py::_UPDATE_GUARD`
  (`WHEN NEW.outcome = 'running' AND (OLD.outcome != 'running' OR OLD.task_id != NEW.task_id)`).
* **Exact failure scenario:** SQLite `!=` against `NULL` yields `NULL`, so the `WHEN` clause is false
  whenever `OLD.outcome IS NULL` (or `OLD.task_id IS NULL`) and both exclusivity guards are skipped
  entirely. An attempt row with a NULL outcome — reachable through a malformed/externally repaired
  database, a partial restore, or any future migration that adds the column nullable — can be updated
  straight to `running` for a non-developer task while developer attempts are running, defeating the
  v0.9 exclusivity invariant at the last line of defense.
* **Why it matters:** These triggers are the database-level guarantee that non-developer work stays
  exclusive; they are the control that must hold when application logic is wrong.
* **Do existing tests catch it?** No. The new DB test exercises only the INSERT guard. The UPDATE
  guard has **zero** test coverage, and malformed rows are untested.
* **Recommended fix:** Use NULL-safe comparisons (`OLD.outcome IS NOT 'running' OR OLD.task_id IS NOT
  NEW.task_id`) and, defensively, `COALESCE` the `kind` subquery so an orphan `task_id` also fails
  closed.
* **Required regression test:** Insert an attempt with `outcome IS NULL` for a non-developer task
  alongside two running developer attempts, update it to `running`, and assert `IntegrityError`; plus
  a positive UPDATE-path test for the normal pending→running transition.
* **Evidence / reproduction:** Trigger SQL replayed on an in-memory SQLite schema:
  `INSERT … VALUES('c1','e1',NULL)` then `UPDATE task_attempts SET outcome='running' WHERE id='c1'`
  succeeds, leaving `[('b1','d1','running'), ('b2','d2','running'), ('c1','e1','running')]` — a
  non-developer running attempt coexisting with developer attempts. The same replay confirms the
  INSERT guard, the reverse INSERT branch and the ordinary UPDATE branch do work.

### V9-08 — Orphan-task attempts bypass both trigger branches

* **Severity:** LOW
* **File/function:** `migrations/versions/0009_worker_concurrency.py` (both guards use
  `(SELECT kind FROM tasks WHERE id = NEW.task_id) != 'developer_run'` / `= 'developer_run'`).
* **Exact failure scenario:** If the subquery yields `NULL` (no matching task row), both comparisons
  are `NULL` and neither guard fires. Today `PRAGMA foreign_keys=ON` plus the FK on
  `task_attempts.task_id` makes this unreachable through the application, but the FK pragma is
  per-connection and is disabled by some maintenance tooling.
* **Why it matters:** Defense-in-depth should not silently depend on a per-connection pragma.
* **Do existing tests catch it?** No.
* **Recommended fix:** `COALESCE((SELECT kind …), '')` so an unknown kind takes the exclusive branch.
* **Required regression test:** With `PRAGMA foreign_keys=OFF`, assert an orphan running attempt is
  rejected.

### V9-09 — Capacity audit events describe scheduler intent, not actual capacity

* **Severity:** MEDIUM
* **File/function:** `app/tasks.py::claim` (`worker_capacity_claimed`) and
  `app/tasks.py::finish` (`worker_capacity_released`).
* **Exact failure scenario:** `worker_capacity_claimed` is written at DB claim time; the real
  filesystem slot is only taken later in `DeveloperRuntime.run`. If `worker_lock` raises
  `developer_worker_capacity_exhausted` (V9-04), the audit trail still shows a capacity claim
  followed by a capacity release for a slot that was never held. Conversely, if the process dies, no
  `worker_capacity_released` is ever written; `TaskService.recover()` emits
  `startup_interruption_recovery` but no capacity release, so the ledger never balances.
* **Why it matters:** v0.8's audit-truthfulness guarantee is explicitly in scope for the v0.9
  regression audit. A capacity ledger that can claim without holding and hold without releasing is
  not a truthful record of enforcement.
* **Do existing tests catch it?** No. Neither audit event is asserted anywhere.
* **Recommended fix:** Emit capacity audit events from the component that actually owns the slot
  (runtime lock acquisition/release), or rename the events to reflect scheduler accounting and emit a
  matching release in `recover()`.
* **Required regression test:** Assert both events appear exactly once per developer attempt for a
  normal run, and assert `recover()` balances the ledger for an interrupted attempt.

### V9-10 — Developer tasks overtake capacity-blocked developer tasks with no anti-starvation bound

* **Severity:** MEDIUM
* **File/function:** `app/tasks.py::_claimable_task` (the `for candidate in pending` loop).
* **Exact failure scenario:** FIFO is preserved only against non-developer barriers. Among developer
  tasks, a later-submitted task for an idle worker overtakes an earlier task whose worker is at
  capacity — by design. But nothing ages or reserves the head of the queue, so a continuously busy
  worker (`max_concurrency=1` is the production default) starves its oldest approved task
  indefinitely while other workers keep consuming claims. Approvals have a TTL, so starvation
  silently converts into approval expiry.
* **Why it matters:** "FIFO/exclusivity behavior must not allow unsafe overtaking" — the overtaking
  here is safe in isolation but unbounded, which makes approval expiry a scheduling side effect.
* **Do existing tests catch it?** No. The scheduler test uses only three tasks and no sustained load.
* **Recommended fix:** Bound the overtaking window (e.g. only consider candidates within the first
  N pending entries) or reserve capacity for the oldest blocked task.
* **Required regression test:** A queue where one worker is saturated and another is continuously fed
  must still eventually claim the oldest task.

### V9-11 — Worker-artifact pinning still performs two full SHA-256 passes on the event loop

* **Severity:** MEDIUM
* **File/function:** `app/developer_artifacts.py::pin_artifact` invoked from
  `app/developer.py::run` via `artifacts.enter_context(selected.worker.bind(request.worker))`.
* **Exact failure scenario:** v0.9 moved `tree_digest` and `validation_identity` onto threads
  (good), but the largest synchronous hash remains inline: `pin_artifact` reads the Codex binary
  (bounded at `MAX_BINARY_BYTES = 512 MiB`), hashes it, writes it into a memfd and then re-reads and
  re-hashes the sealed copy — all on the runner event loop. Under v0.9 concurrency, one execution's
  binding stalls every other developer execution, the runner claim loop, and every `async` API route.
* **Why it matters:** Acceptance criterion 10 ("trusted snapshot hashing is off the asyncio event
  loop") is only partially met; the worst offender for latency is untouched.
* **Do existing tests catch it?** No. There is no event-loop-blocking assertion anywhere.
* **Recommended fix:** Offload `pin_artifact`/`pin_executable` with `asyncio.to_thread`, keeping the
  sealing and fd lifetime inside the thread's context manager.
* **Required regression test:** A latency test asserting the loop stays responsive (a ticking
  background coroutine) while a large artifact is pinned.

### V9-12 — Cleanup no longer excludes running executions

* **Severity:** LOW
* **File/function:** `app/developer.py::cleanup` (now `maintenance_lock`) vs
  `app/developer.py::run` (now `worker_lock`).
* **Exact failure scenario:** In v0.8, `cleanup()` and `run()` shared one lock file, so cleanup could
  never run while any execution was active. v0.9 splits them into disjoint lock namespaces; only the
  `owner.json` `active` flag now separates them. `owner.json` is written immediately after
  `repo.parent.mkdir(mode=0o700)` with no intervening `await`, so the current code is safe, but the
  safety now depends on a statement-ordering accident rather than on a lock.
* **Why it matters:** `cleanup()` performs `shutil.rmtree` on a workspace; the invariant protecting a
  live execution from deletion was downgraded from a lock to an ordering assumption. `cleanup()` has
  no in-application callers today, which is the only reason this is LOW.
* **Do existing tests catch it?** Partially — `tests/test_developer_boundary.py:469` still asserts
  cleanup is busy-blocked by `runtime.lock()`, but `runtime.lock()` is now the maintenance lock, so
  the test no longer proves anything about execution.
* **Recommended fix:** Have `cleanup()` additionally verify it is not deleting a workspace whose slot
  is held, or re-establish an execution-visible lock for the deletion path.
* **Required regression test:** Attempt `cleanup(execution_id)` for an execution that is mid-run and
  assert it is refused.

### V9-13 — Alternate Codex entry points default to a hard-coded workspace-write authority

* **Severity:** LOW
* **File/function:** `app/codex_worker.py::command_config`, `::argv`, `::probe`,
  `::local_command_probe` (all `profile or authority_profile(("workspace-write",))`).
* **Exact failure scenario:** Only `CodexCliWorker.run` resolves authority from the approved snapshot
  via `request_authority()`. Every other public entry point silently substitutes the
  workspace-write profile when called without one. No production path does this today
  (`local_command_probe` is test-only), but the default makes "authority is derived from the approved
  profile" a convention rather than a structural guarantee.
* **Why it matters:** "No alternate execution path may bypass authority enforcement" should be
  enforced by the type system, not by callers remembering an argument.
* **Do existing tests catch it?** No — `tests/test_developer.py:560` in fact exercises
  `bound.argv(repo)` with the implicit default.
* **Recommended fix:** Make `profile` a required parameter on `argv`/`command_config`/`probe`.
* **Required regression test:** Assert `TypeError` / explicit failure when these are called without a
  profile.

### V9-14 — `request_authority()` validates the snapshot against itself

* **Severity:** LOW (contingent on `DeveloperRuntime.run`)
* **File/function:** `app/codex_worker.py::request_authority`.
* **Exact failure scenario:** `authority_snapshot_matches(selection.descriptor.authority,
  selection.authority_profile, selection.authority_policy_version)` compares three fields of the same
  persisted snapshot against the global policy — it never consults the registry. The genuine
  registry binding happens earlier, in `DeveloperRuntime.run` via `self.supervisor.matches(...)`.
  Any future worker invoked outside that runtime gets only the self-consistency check.
* **Why it matters:** The check reads like a trusted authority validation but is only a policy-version
  check; a reviewer could rely on it in a new code path.
* **Do existing tests catch it?** No — `request_authority` has no direct test at all; neither
  `worker_authority_changed` nor `worker_authority_unsupported` appears in the test suite.
* **Recommended fix:** Document the precondition explicitly, or pass the registry-resolved profile
  into `run()` instead of re-deriving it from the request.
* **Required regression test:** Tamper `worker_selection.authority_policy_version` and
  `descriptor.authority` in a request handed straight to `CodexCliWorker.run` and assert
  `worker_authority_changed` / `worker_authority_unsupported`.

### V9-15 — Slot lock file descriptor can leak on the pre-`try` failure path

* **Severity:** LOW
* **File/function:** `app/developer.py::worker_lock` and `::run`
  (`lock = self.worker_lock(request)` then `emit("developer_started", {})` — both before `try:`).
* **Exact failure scenario:** If `os.fdopen` raises, the raw fd leaks; if `emit(...)` raises after the
  lock is acquired, the slot stream is only released by garbage collection. Under a long-lived
  process with a callback that can raise, a slot can be held with no execution behind it, ultimately
  producing V9-04-style failures.
* **Why it matters:** Lock acquisition should be exception-safe by construction.
* **Do existing tests catch it?** No.
* **Recommended fix:** Acquire the lock inside the `try`/`ExitStack` so release is structural.
* **Required regression test:** Force `emit` to raise on `developer_started` and assert the slot is
  free afterwards.

### V9-16 — Per-poll revalidation cost scales with the pending queue

* **Severity:** LOW
* **File/function:** `app/tasks.py::_claimable_task`.
* **Exact failure scenario:** Every claim attempt loads all `RUNNING` tasks plus up to
  `max_waiting_tasks` pending tasks and runs `DeveloperRequest.model_validate` on each, inside the
  runner event loop and inside a `BEGIN IMMEDIATE` transaction. The greedy inner loop repeats the
  whole scan per claim.
* **Why it matters:** Writer serialization is held longer than necessary and the loop is blocked;
  it is a scaling cliff rather than a correctness bug.
* **Do existing tests catch it?** No.
* **Recommended fix:** Persist `worker_id`/`max_concurrency` as indexed columns, or cache validated
  specs per task version.
* **Required regression test:** None required for release; a benchmark guard is sufficient.

---

## 2. Investigated areas with NO issue found

These were examined adversarially and no finding is raised. Listing them makes the absence auditable.

1. **Unsupported authority fails closed at registration.** `WorkerRegistry.__init__` calls
   `authority_profile(trusted_descriptor.authority)`, which raises `ValueError
   ("worker_authority_unsupported")` for any unknown tuple. Covered by
   `test_registry_rejects_unsupported_authority`.
2. **Authority tuple canonicalization.** `WorkerDescriptor._canonicalize` rejects unsorted or
   duplicated `authority`/`capabilities`, so `("workspace-write","workspace-write")` cannot smuggle a
   different key into `_PROFILES`; profile lookup is exact-tuple and fails closed otherwise.
3. **Registry immutability.** `MappingProxyType` plus frozen/slots `RegisteredWorker` plus
   `model_validate` re-parsing of every descriptor. Mutation attempts raise
   (`test_descriptors_and_selection_snapshots_are_immutable`).
4. **Deterministic selection.** `min(eligible, key=lambda i: (-priority, worker_id))` is total and
   stable; disabled workers are filtered in `eligible()`.
5. **Public/default worker equals the routing winner.** With an explicit registry, `_worker` is now
   assigned from `supervisor.select(...)`, and a supplied compatibility worker must be that exact
   object. The `worker` setter still refuses reassignment when the registry is explicit.
6. **Registry version is content-derived and now includes authority.** `_registry_version` hashes the
   schema tag, `AUTHORITY_POLICY_VERSION` and each descriptor together with its resolved profile
   name; any descriptor change (including `max_concurrency`, `enabled`, `priority`) changes the
   version and therefore invalidates approvals through `Supervisor.matches`.
7. **Disabled or changed workers cannot execute old approvals.** `Supervisor.matches` requires
   `current.descriptor == snapshot.descriptor`, `current.descriptor.enabled`, matching
   registry/routing versions and matching authority profile + policy version. Enforced at
   `claim()` (`_valid_action(live=False)` → `compatible`) and again inside `DeveloperRuntime.run`.
8. **Descriptor/identity mismatch fails closed.** `DeveloperRuntime.request` raises
   `worker_descriptor_mismatch` when `identity().implementation != descriptor.implementation`, and
   `approval_compatible` re-checks the same pair.
9. **Approval revalidation is not run on the event loop.** `approval_compatible` explicitly raises
   `approval_revalidation_unavailable` if `asyncio.get_running_loop()` succeeds; the runner path uses
   `live=False`. Confirmed no `live=True` call reachable from `Runner._loop`.
10. **Temporary conditions do not permanently invalidate approvals.**
    `approval_revalidation_unavailable` is re-raised and converted to HTTP 503 in
    `_live_approval_validity`; `_expire_or_invalidate` returns `False` (no cancellation) in that case.
11. **Execution-time TOCTOU sequence.** `run()` re-checks, in order: `compatible()`, live source
    `HEAD == base_sha`, primary snapshot capture *before* any worker bytes, registry/supervisor match,
    artifact rebinding with digest+path+seal verification, `preflight() == request.worker`, and
    `validation_identity()`. Artifact sealing (`F_SEAL_*` + memfd) still prevents post-approval byte
    substitution.
12. **Concurrent binding is isolated.** `CodexCliWorker.bind()` deep-copies settings and yields a new
    worker instance with its own memfd-backed `PinnedExecutable`s, so two concurrent executions of the
    *same* registry worker do not share mutable pinned state. No shared temp path is used
    (`memfd_create` names are not filesystem paths).
13. **Codex permission table still denies engine/auth and networking.** `ENGINE: "deny"`,
    `REPO/.git: "read"`, `approval_policy="never"`, `web_search="disabled"`, the `features.*=false`
    list and `skip_host_skill_discovery=true` are unchanged by this diff; no MCP/plugin/hook/browser
    toggle was enabled.
14. **Boundary probe remains fail-closed and profile-independent for the dangerous bits.** `probe()`
    still asserts `auth_denied`, `network_denied`, `workspace`, `secret_env_absent` as hard-coded
    `True`; a future profile with `network=True` would fail the probe rather than silently gain
    networking.
15. **Worker gains no repository/commit authority.** Workspace is a standalone `git init` + local
    fetch + detached checkout with no remote (`checkout_remote_forbidden`), `.git` is bind-mounted
    read only, `tree_digest(repo/.git)` is compared before/after worker execution and after
    validation, and `head_changed` raises `developer_boundary_violation`. Unchanged by v0.9.
16. **Supported CLI versions.** `SUPPORTED_CLI_VERSIONS` and the capability probe (`--ask-for-approval`,
    `--permission-profile`) are unchanged and still raise `codex_capabilities_unsupported`.
17. **Lock path safety.** Slot files are created under `no_symlinks(ROOT/"runtime")` with
    `O_CREAT|O_RDWR|O_NOFOLLOW` and mode `0600`; `worker_id` is constrained to
    `^[a-z][a-z0-9_-]*$`, so no traversal or symlink swap is possible. `flock` is released by the
    kernel on process death, so stale-lock-after-crash is not a real hazard (stale *files* are inert).
18. **Same-process slot conflicts are real.** `flock` is per open-file-description, so two `os.open`
    calls in one process do contend — the single-process ceiling test is not vacuous (it just does not
    prove the cross-process claim).
19. **Scheduler races.** `claim()` runs inside `BEGIN IMMEDIATE` (writer-serialized) and
    `Database.acquire_runner()` permits exactly one runner per database file, so DB-level
    double-claiming is not reachable.
20. **Migration atomicity and ordering.** `0009` chains from `0008_release_approval`, uses only
    `op.execute`/`op.drop_index` inside Alembic's transaction, and `downgrade()` raises. Because the
    dropped `uq_single_running_attempt` guaranteed at most one running attempt, creating
    `uq_running_attempt_per_task` on existing data cannot fail. Rollback/retry is covered by the new
    `test_failed_migration_rolls_back_and_can_retry` parameter.
21. **Restart recovery with multiple running developer attempts.** `TaskService.recover()` iterates
    *all* `RUNNING` tasks and interrupts each with its attempt; the new triggers only fire on
    `NEW.outcome='running'`, so recovery transitions are unaffected.
22. **Non-developer exclusivity in the scheduler.** `_claimable_task` returns `None` if any running
    task is non-developer and returns `None` at the first non-developer pending candidate, preserving
    the barrier in both directions. Covered by `test_non_developer_task_is_exclusive_barrier` and by
    the INSERT trigger.
23. **Malformed persisted developer specs fail closed.** A malformed running spec stops all claiming;
    a malformed pending spec is returned so the normal `claim()` validation cancels it
    (`approval_missing_or_invalid` / `action_changed`). No malformed spec can execute.
24. **Tampered snapshot fields are approval-bound.** `action_hash` covers the whole
    `developer_spec`, and `test_approval_binds_specification` now includes
    `worker_selection.authority_profile` and `worker_selection.authority_policy_version`.
25. **Duplicate in-flight task ids fail closed.** `_loop` raises `duplicate_running_task` if `claim()`
    ever returns a task already in `operations`, which trips the fail-closed runner path.
26. **Cancellation during `asyncio.to_thread` work.** The offloaded callables (`tree_digest`,
    `validation_identity`, `_primary_snapshot_digest`) are read-only; a cancelled `to_thread` future
    can therefore not mutate state later. Results computed after cancellation are discarded.
27. **v0.8 promotion / merge / release separation.** `app/promotion*.py`, GitHub CI identity
    verification and protected-primary verification are untouched by this diff; the only shared
    surface is `Runner`, whose exclusivity for those kinds is preserved by the non-developer barrier.

---

## 3. Test-coverage gaps

| # | Gap | Related finding |
| --- | --- | --- |
| T1 | No two-process test of the filesystem slot ceiling — acceptance criterion 8 is asserted only single-process. | V9-04, V9-05 |
| T2 | No scheduler test with `max_concurrency > 1`: `CapacityRuntime.request` always builds `max_concurrency=1`, so the `running_counts[...] < max_concurrency` branch for >1 never executes. "Same worker must never exceed its approved maximum" is untested above 1. | C |
| T3 | No test that changing `max_concurrency` (or any descriptor field) after approval invalidates the approval end to end. | V9-05 |
| T4 | Every runner concurrency test uses `CapacityRuntime`, a fake with no filesystem, Git, locks or sandbox — real races (slot locks, primary snapshots, artifact pinning) are mocked away. | V9-03, V9-04, V9-11 |
| T5 | No cancel-before-operation-start test; the acknowledged lifecycle gap is only commented, never asserted. | V9-02 |
| T6 | No timeout-path test for concurrent developer executions, and no crash/kill test for slot release. | V9-02, V9-04 |
| T7 | UPDATE trigger has zero coverage (positive or negative); the INSERT guard's "developer attempt while non-developer running" branch is also untested. | V9-07 |
| T8 | Malformed database states untested: NULL `outcome`, orphan `task_id`, two running attempts for one task after external edit. | V9-07, V9-08 |
| T9 | Malformed/tampered persisted `WorkerSelectionSnapshot` fields `descriptor.authority`, `descriptor.max_concurrency`, `descriptor.worker_id`, `registry_version`, `routing_policy` are not in the `test_approval_binds_specification` parameter list. | A/B |
| T10 | `request_authority()` is entirely untested — neither `worker_authority_changed` nor `worker_authority_unsupported` is asserted anywhere. | V9-14 |
| T11 | Authority-policy tampering is tested only at the dataclass level; no test proves that editing the real Codex permission table invalidates approvals. | V9-01 |
| T12 | `command_config` test asserts a read-only profile renders `"/workspace"="read"` but never asserts the corresponding `sandbox(..., writable=False)` wiring or a `network=True` rendering. | V9-01, V9-13 |
| T13 | Stale-lock / leftover-slot-file behavior untested. | V9-04, V9-15 |
| T14 | New audit events `worker_capacity_claimed` / `worker_capacity_released` are never asserted. | V9-09 |
| T15 | No event-loop-blocking assertion for any offloaded or still-inline hashing. | V9-11 |
| T16 | `tests/test_developer_boundary.py:469` still uses `runtime.lock()` to prove cleanup exclusion, but `lock()` is now the maintenance lock — the test silently stopped testing execution exclusion. | V9-12 |

Several new tests also restate implementation assumptions rather than challenge them: e.g.
`test_authority_policy_is_approval_bound` mutates the snapshot copy that `Supervisor.matches`
compares, which cannot fail given the equality-based implementation, and
`test_scheduler_enforces_worker_capacity_and_can_skip_to_another_worker` drives `claim()` directly
rather than through the runner, so it cannot observe any of the real race windows.

---

## 4. Acceptance-criteria verdicts

| # | Criterion | Verdict | Basis |
| --- | --- | --- | --- |
| 1 | Unsupported authority fails at registry construction | **MET** | `WorkerRegistry.__init__` → `authority_profile()` raises; tested. |
| 2 | Authority profile and policy version persisted in the approval snapshot | **MET** | `WorkerSelectionSnapshot` fields; included in `action_hash`; tamper-tested. |
| 3 | Changing the authority policy/profile invalidates compatibility | **PARTIALLY MET** | True for the two booleans on the dataclass; false for the actual Codex permission table and sandbox argv (V9-01). |
| 4 | Codex execution derives filesystem/network permissions from the bound authority profile | **PARTIALLY MET** | `run()` does; `argv`/`probe`/`command_config`/`local_command_probe` silently default (V9-13), and the profile self-check does not consult the registry (V9-14). |
| 5 | Scheduler claims never exceed a worker's `max_concurrency` | **PARTIALLY MET** | Logic is correct and transaction-serialized, but only the `max_concurrency=1` path is exercised; the ceiling value is read from the persisted spec before approval revalidation (T2, V9-05). |
| 6 | Different eligible workers can run concurrently when each has capacity | **MET (in-process, mocked)** | `test_runner_overlaps_different_workers`; never demonstrated with a real runtime, where V9-03 makes concurrent real executions actively unsafe. |
| 7 | Non-developer tasks remain exclusive while developer tasks are running | **MET** | Scheduler barrier + INSERT trigger; UPDATE trigger is NULL-unsafe (V9-07) but requires a malformed row. |
| 8 | Cross-process worker slot locks enforce the same concurrency ceiling | **NOT MET** | No cross-process test exists; the ceiling is taken from the request rather than the trusted registry (V9-05); exhaustion destroys approvals (V9-04). |
| 9 | Explicit registry cannot expose a primary worker different from the routing winner | **MET** | `_worker` assigned from `supervisor.select`; mismatch raises; tested. |
| 10 | Trusted snapshot hashing is off the asyncio event loop | **PARTIALLY MET** | `tree_digest`, `validation_identity`, `_primary_snapshot_digest` offloaded; `pin_artifact`'s two full binary hashes remain inline (V9-11). |
| 11 | All v0.8 approval/source/artifact/validation protections remain intact | **PARTIALLY MET** | Approval, artifact, identity, source-HEAD and validation bindings are intact; the primary-source boundary check becomes unreliable under concurrency (V9-03), cleanup exclusion was downgraded (V9-12) and the capacity audit ledger is not truthful (V9-09). |
| 12 | Ruff, Ruff format, full pytest pass | **MET (reported)** | Accepted as reported; not re-run in this read-only review. |

---

## 5. Summary

### 5.1 Release-blocking findings

| ID | Severity | Summary |
| --- | --- | --- |
| V9-01 | HIGH | Authority-policy fingerprint covers four dataclass fields, not the Codex permission table it claims to cover; permission-semantic edits do not invalidate approvals. |
| V9-02 | HIGH | Cancellation of a claimed-but-unstarted operation leaves the task permanently `RUNNING` and permanently consumes worker capacity; the `sleep(0)` mitigation is order-dependent and demonstrably loses the race. |
| V9-03 | HIGH | Concurrent developer executions mutate the primary `.git/index` through `git status`, producing false `developer_boundary_violation` alarms and destroying valid work. |
| V9-04 | HIGH | Filesystem slot exhaustion terminally FAILS an approved task (consuming the approval) instead of deferring it, and the lock namespace is shared across databases/workspace roots. |

### 5.2 Non-blocking findings

| ID | Severity | Summary |
| --- | --- | --- |
| V9-05 | MEDIUM | Slot ceiling derived from the persisted request, so the "independent" cross-process check is not independent. |
| V9-06 | MEDIUM | No global developer-concurrency ceiling (up to 32 × N concurrent sandboxed executions). |
| V9-07 | MEDIUM | UPDATE trigger is NULL-unsafe; a malformed attempt row can break non-developer exclusivity. |
| V9-09 | MEDIUM | Capacity audit events do not reflect actual slot ownership and never balance after a crash. |
| V9-10 | MEDIUM | Unbounded developer overtaking allows starvation that silently becomes approval expiry. |
| V9-11 | MEDIUM | Artifact pinning still performs two full binary hashes on the event loop. |
| V9-08 | LOW | Orphan-task attempts bypass both trigger branches when foreign keys are off. |
| V9-12 | LOW | Cleanup no longer excludes running executions by lock; safety now rests on statement ordering, and the existing test no longer proves it. |
| V9-13 | LOW | Alternate Codex entry points default to a hard-coded workspace-write authority. |
| V9-14 | LOW | `request_authority()` validates the snapshot against itself, not against the registry. |
| V9-15 | LOW | Slot lock can leak on the pre-`try` failure path in `run()`. |
| V9-16 | LOW | Claim-time revalidation cost scales with the pending queue inside a write transaction. |

### 5.3 Reviewed SHA

* Base: `e4610fd` (`main`)
* Head reviewed: **`bd266a08487e7694188ce6410f37554767101451`** (`feat/v0.9-worker-authority`)

### 5.4 Overall recommendation

**RELEASE BLOCKED.**

v0.9's structural direction is sound: authority resolution is genuinely fail-closed at registration,
the approval snapshot binding is materially stronger than v0.8, registry immutability and
deterministic routing hold up under attack, and the scheduler's developer-only concurrency rule is
correctly transaction-serialized. But the release cannot ship as-is:

1. the headline guarantee — permission-semantic changes invalidate approvals — is only true for two
   booleans and is overclaimed in the design document (V9-01);
2. the runner can strand a claimed task in `RUNNING` forever and leak its capacity (V9-02);
3. turning concurrency on makes Zelo's own primary-source boundary alarm unreliable (V9-03); and
4. capacity exhaustion consumes human approvals (V9-04).

Acceptance criterion 8 is not demonstrated at all, and criteria 3, 4, 5, 10 and 11 are only
partially met. Independently of the fixes, the concurrency test strategy must move off fakes: every
new concurrency test today exercises `CapacityRuntime`, which cannot expose any of the four
release-blocking issues above.
