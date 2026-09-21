# Zelo Core v0.9 — Final Independent Corrective Review

Read-only, release-blocking re-review of the second round of corrective fixes. The reviewer is
independent of the implementation agent. No implementation file was modified, no finding was fixed,
nothing was merged, and no claim from the implementation agent's summary was taken on trust. Every
status below is derived from reading the actual code, migration and diff, and from adversarial
probes executed against production code paths.

| Item | Value |
| --- | --- |
| Repository | `kANoZY341/zelo-agent` |
| Branch | `feat/v0.9-worker-authority` |
| **Exact SHA reviewed** | **`f816b076fad74495289e43a9fa784900df63c6e6`** |
| Previous corrective implementation | `3ed0472d14ea2da491f77111ac77d60f0bf0a9ce` |
| Previous independent review | `docs/reviews/zelo-core-v0.9-corrective-review.md` |
| Corrective diff reviewed | `3ed0472..f816b07` — 6 app modules, 1 new migration, 6 test modules, 1 design doc |
| Environment | Linux, Python 3.12.3, git 2.55.0, **Bubblewrap 0.9.0 present and functional** |

### SHA verification

The SHA under review was verified before analysis, not assumed:

```
$ git rev-parse FETCH_HEAD          # origin/feat/v0.9-worker-authority
f816b076fad74495289e43a9fa784900df63c6e6
$ git log --oneline -2 FETCH_HEAD
f816b07 fix worker authority corrective review findings
119b621 docs: add Zelo Core v0.9 corrective review
```

`f816b07` is the tip of `feat/v0.9-worker-authority` and is a direct descendant of the previously
reviewed `3ed0472`. The working tree was confirmed byte-identical to that commit for the whole
review (`git diff f816b07` empty).

### Environment note — this is the first fully executed suite in the review series

Both prior reviews ran without Bubblewrap and could not validate the safety-relevant paths
(the original: 375 passed / 51 environment failures / 75 skipped). For this review Bubblewrap was
installed **and made functional** (`kernel.apparmor_restrict_unprivileged_userns=0`), so the entire
suite genuinely executed. This materially increases confidence relative to both earlier reports, and
it is how the V9-01 and capacity findings below were confirmed against real `bwrap` argv.

An intermediate observation worth recording: with `bwrap` installed but **non-functional**
(`setting up uid map: Permission denied`), the suite reports **101 failures / 2 skips** instead of
skipping. The suite gates on file presence (`Path("/usr/bin/bwrap").is_file()`), not on capability.
See W-5.

---

## 1. Status of the previous findings

| ID | Severity at `3ed0472` | Status at `f816b07` | Residual severity |
| --- | --- | --- | --- |
| N-1 RUNNING retry / scheduler poisoning | HIGH (blocking) | **RESOLVED** | — |
| N-2 capacity retry hot loop / audit growth | HIGH (blocking) | **RESOLVED** (bounded) | LOW residual |
| N-3 operator cancellation during deferral | MEDIUM | **RESOLVED** | — |
| N-4 cancellation during threaded worker bind | MEDIUM | **RESOLVED** | — |
| N-5 repeated cancellation during cleanup | MEDIUM | **RESOLVED** | LOW residual (N-9) |
| N-6 design documentation not updated | LOW | **MOSTLY RESOLVED** | LOW (still overclaims, N-10) |
| N-7 V9-03 invariant undefended by tests | LOW | **RESOLVED** | — |
| V9-01 residue: bwrap argv in fingerprint | MEDIUM | **PARTIALLY RESOLVED** | **MEDIUM (open)** |
| V9-03 `GIT_OPTIONAL_LOCKS=0` / primary index | NOT REPRODUCED | **CONFIRMED + now defended** | — |

### N-1 — RUNNING retry / scheduler poisoning — RESOLVED

Two independent corrections were applied, and both are real:

1. `app/policy.py` — `TRANSITIONS[State.RUNNING]` no longer contains `State.PENDING`. The public
   state machine is back to `{SUCCEEDED, FAILED, CANCELLED, INTERRUPTED}`.
2. `app/tasks.py::retry` — explicit `if task.status == State.RUNNING: raise DomainError(...)`
   guard placed *before* `validate_transition`, so the rejection does not depend on the table.

The deferral requeue now uses a **narrowly scoped internal path**, `defer_transition()`
(`app/tasks.py:89-95`), which re-checks `task.status != State.RUNNING` itself and is not routed
through `validate_transition`. I verified by grep that `defer_transition` has **exactly one caller**
(`TaskService.defer`, `app/tasks.py:1582`) and that the only other `→ PENDING` transitions are
`approve()` (`AWAITING_APPROVAL → PENDING`, legal) and submit-time initialisation. There is no API
surface that reaches `defer_transition`.

Verified against the **real FastAPI operator API** (not the service in isolation):

```
PROBE api retry on RUNNING -> 409 {"detail":"Cannot retry task in state running"}
PROBE scheduler healthy after rejected retry -> True
```

The orphan-attempt consequence is gone: the task stays `RUNNING` with its single `RUNNING` attempt,
`finish()` still succeeds afterwards, and the next `claim()` works (the pre-fix defect raised
`sqlite3.IntegrityError` on every subsequent claim and permanently dropped readiness).

I also probed the adjacent mutations on a `RUNNING` task — `cancel()` correctly sets
`cancel_requested` without requeueing, and `finish()` then produces a single `CANCELLED` attempt.
No attempt is orphaned on any path I could construct.

Regression coverage is adequate and, unlike the previous round, correctly oriented:
`test_invalid_transitions` now asserts `RUNNING → PENDING` is **invalid**, and
`test_retry_rejects_running_tasks_without_orphaning_attempts` is parametrised over `echo`,
`tool_call`, `agent_run` and `developer_run` and asserts the attempt is not orphaned.

### N-2 — capacity retry backoff — RESOLVED (bounded), with a LOW residual

The design is persisted rather than in-memory, which is the right choice: `tasks.capacity_backoff_attempts`
and `tasks.capacity_backoff_until` (migration `0010`). `defer()` increments the counter and sets
`capacity_backoff_until = utcnow() + min(0.25 * 2**(n-1), 5.0)`; `_claimable_task` filters
`capacity_backoff_until IS NULL OR capacity_backoff_until <= utcnow()`. The counter is reset in
`finish()` and `retry()`; `claim()` clears only `capacity_backoff_until`, so consecutive deferrals
correctly keep escalating across attempts.

Measured with the **real `Runner` loop at the production default poll interval (0.05 s)** over a
12-second sustained-contention window — the measurement the previous review asked for:

```
PROBE sustained contention: 6 attempts / 43 audit rows in 12.0s (0.50 attempts/s),
                            backoff_attempts=6, status=pending
```

Against the pre-fix measurement of **9.3 attempts/s and ~68 audit rows/s**, this is an ~18× rate
reduction during the ramp and, once the 5 s ceiling is reached, a steady state of **0.2 attempts/s**
— approximately **720 attempts and ~5 000 audit rows per hour** of contention versus the previously
extrapolated ~33 000 attempts and ~240 000 audit rows. The hot loop is genuinely gone.

Capacity retry also still converges, and the approval is not consumed:

```
PROBE recovery after slot free -> succeeded  approval: approved  attempts: 4
```

**Residual (LOW, not blocking):** there is still no *absolute* deferral bound. A task blocked for a
full default approval TTL performs ~720 attempts, and because the slot is acquired after
`primary_snapshot()` and re-verified in `finalize()`, that is still roughly **1 400 primary
repository snapshots (~13 000 git subprocesses) per hour** for a single blocked task, plus unbounded
growth of the attempt list embedded in `GET /tasks/{id}`. The termination condition is approval TTL
expiry, not a deferral ceiling. This is a large improvement over `3ed0472` and I do not consider it
release-blocking, but a `max_capacity_deferrals` setting would close it properly.

The branch's own test is no longer tautological: `test_real_runtime_slot_contention_defers_with_bounded_backoff_and_retries`
now runs at the production poll interval (`0.05`, previously overridden to `0.2`) and asserts
`len(attempts) == 1` after 0.2 s and `<= 2` after a further 0.45 s — assertions a no-backoff
implementation genuinely fails. I confirmed these specific assertions pass on their own.

### N-3 — operator cancellation during deferral — RESOLVED

`defer()` now checks `task.cancel_requested` first and, when set, completes the cancellation
(`State.CANCELLED`, `error="cancelled_by_operator"`, terminal attempt, `cancellation_completed`
audit, plus `developer_failed` for `developer_run`) instead of requeueing. A second, independent
guard was added in `_claimable_task`: `Task.cancel_requested.is_(False)`.

```
PROBE defer+cancel -> cancelled cancelled_by_operator
(service.claim() is None — cancelled work is never re-claimed)
```

**Cancellation wins over capacity deferral** even though the two signals travel by different routes.
I traced this carefully because it is not obvious from the diff: when cancellation lands during a
capacity-exhausted run, `DeveloperRuntime.run` sets `result["cancelled"] = True` but leaves
`result["error"] == "developer_worker_capacity_exhausted"`, so line 814 still re-raises
`transient_error` and `Runner._run` routes to `defer()`. Cancellation is therefore honoured through
the **database** `cancel_requested` flag rather than the result payload — which works, and is the
only ordering that is race-free, but it is entirely undocumented at either call site.

**`cancel_requested` cannot become stale.** This was the highest-risk part of the fix, because the
new `_claimable_task` filter could silently starve any task carrying a stale flag. `retry()` clears
it (`app/tasks.py:1136`) together with both backoff fields, and I verified the full round trip:

```
PROBE cancel_requested after cancelling defer: True   (task is CANCELLED — terminal)
PROBE after retry: status=awaiting_approval cancel_requested=False
                   backoff_attempts=0 until=None
```

I could not construct a `PENDING` task carrying `cancel_requested = True`: `cancel()` on a task in
`PENDING`/`AWAITING_APPROVAL` transitions straight to `CANCELLED` in the same transaction, and both
`defer()` and `cancel()` run under `BEGIN IMMEDIATE`, so the interleaving is serialised in either
order. The filter is correct defence-in-depth rather than a starvation hazard.

### N-4 — cancellation during threaded worker bind — RESOLVED

`DeveloperRuntime.run` now wraps the threaded `__enter__` in `uncancellable()`:

```python
enter_task = asyncio.create_task(asyncio.to_thread(bound_worker_context.__enter__))
bound_worker, bind_cancelled = await uncancellable(enter_task)
bound_worker_entered = True
if bind_cancelled:
    raise asyncio.CancelledError
```

`bound_worker_entered` is set only after the thread has genuinely completed, so the `finally` always
sees a truthful ownership flag. `uncancellable()` loops on `asyncio.shield` until the task is done,
so it absorbs *repeated* cancellations rather than just the first. If `__enter__` itself raises, the
exception propagates with `bound_worker_entered` still `False`, which is correct.

I attempted to defeat this with a 50-cancellation storm straddling the bind (25 before the thread is
released, 25 after), which is well beyond what the branch's own test exercises:

```
PROBE n4 cancel-storm at bind -> developer_cancelled  exits: 1
```

Exactly one `__exit__`. No pinned-artifact leak.

### N-5 — repeated cancellation during cleanup — RESOLVED

The `finally` cleanup was restructured correctly. The ordering is the important part: the `flock` is
closed and `lock_released` set **before** any `await`, the release audit event is emitted
immediately, and only then is the awaited `__exit__` performed with `artifacts.close()` in a nested
`finally` — the whole block wrapped in `uncancellable()`.

Adversarial probe — cancel, wait until `__exit__` has started and is blocked, re-acquire the slot
from outside to prove it is already free, then fire 50 further cancellations:

```
PROBE n5 cancel-storm at unbind -> claims: 1  releases: 1  exits: 1
```

Slot release no longer depends on refcounting of the `run()` frame; it is structural.

**Every actual `worker_capacity_claimed` receives exactly one truthful release**, and filesystem
slots are immediately reusable. Verified across a sweep of cancellation timings that straddle the
`worker_lock` acquisition point:

```
PROBE ledger delay=0.0    : claims=0 releases=0   slot immediately reusable: True
PROBE ledger delay=0.001  : claims=0 releases=0   slot immediately reusable: True
PROBE ledger delay=0.005  : claims=0 releases=0   slot immediately reusable: True
PROBE ledger delay=0.02   : claims=1 releases=1   slot immediately reusable: True
PROBE ledger delay=0.05   : claims=1 releases=1   slot immediately reusable: True
PROBE ledger delay=0.1    : claims=1 releases=1   slot immediately reusable: True
```

The ledger is balanced at every timing, and cancellation before acquisition correctly emits neither
event — the V9-09 truthfulness property now holds on the cancellation path too. One narrow residual
remains on this path (N-9, LOW).

### V9-01 residue — Bubblewrap authority semantics — PARTIALLY RESOLVED (still open, MEDIUM)

This is the one previously-accepted finding that is **not** fully closed, so I probed it hardest.

**What genuinely improved.** `app/developer_sandbox.py` gained `sandbox_plan()`, and — critically —
the production `sandbox()` function now *consumes* it (`args = [str(BWRAP), *plan["argv_prefix"]]`,
`if plan["unshare_network"]`, `for path in plan["system_readonly_binds"]`, `plan["workspace"]["mode"]`,
`plan["git_metadata"]["mode"]`, …). `worker_authority.sandbox_permission_semantics()` hashes the
same function instead of the previous five-key hand-written mirror. This is a real structural fix,
not a relabelling, and the import direction introduces **no cycle**
(`worker_authority → developer_sandbox → developer_process`, one-way).

To test "changing a real enforced Bubblewrap permission changes the approval-bound authority
fingerprint" I mutated enforcement literals in isolated copies of the tree and recomputed
`authority_policy_version()` in a fresh interpreter each time:

| Mutation to a real enforced permission | Fingerprint changed? |
| --- | --- |
| remove `--unshare-pid` from the executed argv prefix | **yes** |
| bind host `/etc` read-only into the sandbox | **yes** |
| make `REPO/.git` writable (`--ro-bind` → `--bind`) | **yes** |
| add host `/` to `HOST_READONLY_BINDS` | **yes** |

Those are exactly the four cases the previous review named, and they are now genuinely bound. The
previous report's reproduction no longer passes in its original form.

**What is still open.** `sandbox_plan()` is a *partial* description of the argv. `sandbox()` retains
independent literals that no longer agree with the plan, and `sandbox_permission_semantics()` pins
exactly **one** plan configuration (`repo_present=True, include_venv=False, git_inspection=False,
service_network=True, workspace_write=profile.workspace_write`). Values only reachable in other
configurations are therefore constant in the hash or absent from it.

For each mutation below I asserted **both** that the executed bwrap argv really changes (so it is a
genuine authority change) **and** whether the approval-bound fingerprint changes:

```
argv changed  fingerprint changed  mutation
-----------------------------------------------------------------------------------
True          False                codex auth.json bind: --ro-bind -> --bind   <-- RESIDUE
True          False                validation venv bind: --ro-bind -> --bind   <-- RESIDUE
True          False                read-only workspace becomes writable        <-- RESIDUE
True          False                extra --bind of host / appended to argv     <-- RESIDUE
True          False                drop --unshare-net for isolated runs        <-- RESIDUE
```

Consequences, in descending order of seriousness:

* **`--bind / /hostroot` appended to the argv builder affects the Codex worker argv itself** — the
  highest-authority execution path — with a byte-identical policy fingerprint, registry version and
  approval snapshot. This is the previous review's headline reproduction, still reproducible at a
  different injection point.
* Making Codex's `auth.json` writable is a credential-integrity change on the Codex path and is
  invisible to approvals.
* The `writable=False` mount mode is unhashed, so the read-only inspection sandboxes
  (`app/developer.py:280,469`, `app/developer_review.py:45,61`) can be silently made writable.
* The `--unshare-net` *consumption* is unhashed; the hashed configuration passes
  `service_network=True`, for which `unshare_network` is already `False`, so network isolation of
  the non-service paths is outside the fingerprint.

The new test `test_authority_policy_matches_real_sandbox_semantics` is a good test — it asserts both
that the argv changes and that the fingerprint changes — but it covers only the
`SYSTEM_READONLY_BINDS` dimension, which is one of the dimensions that already works.

**`sandbox_plan()` is the same source of truth used by execution and fingerprinting for the argv
skeleton, but not yet for the whole argv.** Closing this requires `sandbox()` to read every mount
mode and bind path back from the plan (no independent literals), and the fingerprint to cover the
plan configurations actually used in production rather than a single one.

### V9-03 — `GIT_OPTIONAL_LOCKS=0` and primary repository index/snapshot — CONFIRMED and now defended

The invariant holds at `f816b07`: `app/developer_process.py:35` sets `GIT_OPTIONAL_LOCKS: "0"` in
`environment()`, every git invocation in `app/developer.py` and `app/promotion_git.py` routes through
`run_process` with that environment, and the derived environments (`_commit_environment`, Codex
`CODEX_HOME`) preserve it. A repository-wide grep finds no git call path that bypasses it.

The previous N-7 gap is closed. `test_concurrent_primary_snapshots_do_not_mutate_source_index` now
asserts the invariant explicitly *and* checks the property the boundary digest actually depends on:

```python
assert environment()["GIT_OPTIONAL_LOCKS"] == "0"
...
assert index.read_bytes() == before
assert tree_digest(index) == before_digest
assert index.stat().st_mtime_ns == before_mtime     # <-- the digest-sensitive property
```

Comparing `st_mtime_ns` rather than only index bytes is the correct fix: `tree_digest` hashes
`st_mtime_ns`, so the earlier bytes-only assertion could not have caught a regression. With
Bubblewrap functional, this test and the full boundary suite pass. Deleting the single environment
line now fails the suite, which was the stated requirement.

---

## 2. New findings

### N-8 — Unbounded exponent in the backoff computation raises `OverflowError` and strands a phantom `RUNNING` task

* **Severity:** MEDIUM — configuration-dependent, restart-recoverable. Not release-blocking at
  default settings.
* **File/function:** `TaskService.defer`, `app/tasks.py:1573-1577`.
* **Mechanism:** the *result* is clamped but the *exponent* is not:

  ```python
  task.capacity_backoff_attempts += 1
  delay_seconds = min(
      CAPACITY_BACKOFF_INITIAL_SECONDS * (2 ** (task.capacity_backoff_attempts - 1)),
      CAPACITY_BACKOFF_MAX_SECONDS,
  )
  ```

  `2 ** n` is an arbitrary-precision Python `int`. Multiplying it by the float `0.25` forces an
  `int → float` conversion that raises **before** `min()` can apply the 5 s ceiling. Since
  `capacity_backoff_attempts` is persisted and only reset by `finish()`/`retry()`, it grows without
  bound across consecutive deferrals.

  ```
  n=1024 -> 5.0
  n=1025 -> RAISES OverflowError int too large to convert to float
  ```

* **Reproduction** (real `TaskService` and real `Runner._run`; counter preset to a value reachable
  through sustained contention):

  ```
  PROBE preset=1023 defer -> ok        task status after defer: pending
  PROBE preset=1024 defer -> OverflowError int too large to convert to float
  PROBE preset=1024 task status after defer: running
  PROBE preset=1024 attempt outcomes: [State.RUNNING]
  PROBE Runner._run with overflowing backoff -> OverflowError int too large to convert to float
  ```

* **Consequence:** the `defer()` transaction rolls back, so the task and its attempt stay `RUNNING`
  with no in-flight execution. `OverflowError` is raised *inside* `Runner._run`'s
  `except DeveloperRuntimeError` handler, so the sibling `except Exception` does not catch it; it
  escapes `_run`. Because `_run` is launched via `asyncio.create_task`, it does not set
  `runner.failed` (confirmed: `runner.failed after overflow: False`), and
  `_finalize_unstarted_operation` returns early since the task *failed* rather than *cancelled* —
  so nothing finalises it. `_run`'s `finally` pops `operations`, leaving a **phantom `RUNNING`
  developer task**: it permanently consumes one scheduler-side worker slot and, via the
  `_claimable_task` exclusivity barrier, blocks **every pending non-developer task** for the life of
  the process. `recover()` clears it on restart because the status is `RUNNING`.
* **Reachability:** 1 025 consecutive capacity deferrals. Cumulative backoff is
  `0.25+0.5+1+2+4 + 5×1020 ≈ 5 108 s ≈ 85 minutes` of uninterrupted foreign slot contention. At the
  default `approval_ttl_seconds = 3600`, `expire_approvals()` cancels the task at ~720 deferrals, so
  the overflow is **not reachable with default settings**. It becomes reachable whenever
  `approval_ttl_seconds` is configured above ~5 110 s; the documented maximum is 86 400.
* **Do the new tests catch it?** No. No test sets `capacity_backoff_attempts` above single digits.
* **Recommended fix:** clamp the exponent, not just the product — e.g.
  `min(attempts - 1, 5)` before exponentiation, or `min(attempts, MAX)` when incrementing. A
  regression test presetting the counter to 1 025 would pin it.

### N-9 — A failing `worker_capacity_released` emit skips the worker unbind

* **Severity:** LOW
* **File/function:** `DeveloperRuntime.run` `cleanup()`, `app/developer.py:783-798`.
* **Mechanism:** the release `emit(...)` sits *outside* the `try` that guards `__exit__` and
  `artifacts.close()`. `emit` is `Runner._developer_worker`'s closure over
  `TaskService.model_event`, i.e. a database write, so it can fail independently of the slot.
* **Reproduction:**

  ```
  PROBE emit-failure raised -> RuntimeError audit backend down
  PROBE emit-failure __exit__ calls -> 0
  PROBE RESIDUAL: unbind skipped when release emit fails: True
  ```

* **Consequence:** strictly bounded and much smaller than N-5 was. `lock.close()` and
  `lock_released = True` execute *before* the emit, so **capacity is still released correctly**; only
  the pinned worker artifacts (`__exit__`) and `artifacts.close()` are skipped, and the audit row is
  missing. `Runner._run`'s generic `except Exception` catches the escaping error and finishes the
  task `FAILED`/`worker_error`, so the scheduler is not poisoned. Requires an audit-database failure
  to trigger.
* **Recommended fix:** move the release `emit` inside the same `try` whose `finally` performs
  `__exit__`/`artifacts.close()` — the symmetric change to the one already made for
  `emit("developer_started")` under V9-15.

### N-10 — Design document still overclaims fingerprint coverage

* **Severity:** LOW
* The N-6 gap is largely closed: `docs/v0.9-worker-authority-design.md` now documents the
  defer-with-backoff semantics, the "claimed means real slot ownership" audit contract with exactly
  one release, and the cancellation-safe threaded bind/unbind with release ordered before the awaited
  unbind. All three match the code I verified.
* It remains inaccurate on one point. The document states the fingerprint hashes "the canonical
  Bubblewrap mount/namespace plan used for execution" and that "a new host bind, or a different
  read/write mount therefore invalidates old approvals automatically". The table in §1 shows a new
  host bind (`--bind / /hostroot`) and three different read/write mounts that change the executed
  argv while leaving the fingerprint byte-identical. Either the code should close the residue or the
  document should state precisely which plan keys are bound.

### N-11 — Backoff filtering silently reorders the FIFO queue (observation)

* **Severity:** LOW — behavioural change, arguably desirable, but undocumented and untested.
* `_claimable_task` now excludes backed-off tasks from the `pending` list *before* the
  exclusivity-barrier logic runs, so a later-created non-developer task can overtake an earlier
  deferred developer task:

  ```
  PROBE first-created developer task: 1a82b5f3  status: pending   (backing off)
  PROBE later echo task:              4ab101e8
  PROBE claim during backoff -> ('echo', '4ab101e8')
  PROBE later task overtook earlier deferred developer task: True
  ```

* This prevents head-of-line blocking, which is almost certainly the intent, but it relaxes the FIFO
  property the surrounding comment asserts ("Preserve FIFO semantics"). Worth documenting and
  pinning with a test so it is a decision rather than a side effect.

---

## 3. Migration assessment — `migrations/versions/0010_capacity_backoff.py`

**Verdict: correct.** Assessed by building a real pre-fix v0.9 database from the `3ed0472` working
tree (head `0009_worker_concurrency`), populating representative rows across every lifecycle state
the new query touches (`pending`, `running`, `succeeded`, `failed`, `cancelled` with
`cancel_requested=True`, `interrupted`, plus `developer_run` in `pending`/`awaiting_approval`), then
upgrading with the `f816b07` tree and exercising it:

```
=== building pre-fix (3ed0472) database ===
OLD_HEADS ('0009_worker_concurrency',)
capacity columns present before: []      tasks: 8

=== upgrading with f816b07 ===
NEW_HEADS ('0010_capacity_backoff',)     CHECK_SCHEMA_OK

=== post-upgrade schema and data ===
capacity_backoff_attempts: type=INTEGER notnull=1 default='0'
capacity_backoff_until:    type=DATETIME notnull=0 default=None
rows with NULL attempts: 0     distinct attempts values: [0]
rows with non-NULL until: 0    task count preserved: True

=== exercising the migrated database with f816b07 code ===
CLAIM_ON_MIGRATED_DB: None     ORM_READBACK_OK     RECOVER_OK

=== downgrade behaviour ===
DOWNGRADE_BLOCKED: RuntimeError Destructive downgrade is disabled; restore a reviewed backup instead
```

Specific points checked:

* `server_default="0"` with `nullable=False` correctly backfills pre-existing rows; no row is left
  `NULL`, so the `Task.capacity_backoff_attempts += 1` arithmetic in `defer()` cannot hit `None` on a
  migrated task. This is the failure mode a `nullable=False` column without a server default would
  have produced, and it was avoided.
* `capacity_backoff_until` is correctly nullable, and the `_claimable_task` predicate explicitly
  handles `IS NULL`, so migrated rows are immediately claimable rather than silently filtered out.
* **Type consistency verified:** the migration declares `sa.DateTime()` while the model uses
  `UTCDateTime`. This is correct, not a mismatch — `UTCDateTime` is a `TypeDecorator` with
  `impl = DateTime` that stores naive UTC and returns aware datetimes, so the `capacity_backoff_until
  <= utcnow()` comparison binds through the decorator's `process_bind_param` and is timezone-correct.
* `down_revision = "0009_worker_concurrency"` is correct and linear; `check_schema()` passes;
  `PRAGMA foreign_key_check` is clean; data is preserved.
* The blocked downgrade matches the convention of every prior migration in this repository.
* Repository coverage was extended appropriately: `test_migration_preserves_foreign_key_dependents`
  now expects head `0010_capacity_backoff` and asserts both columns, and
  `test_failed_migration_rolls_back_and_can_retry` gained a `0009 → 0010` failure-injection case
  asserting rollback and retry.

The `CLAIM_ON_MIGRATED_DB: None` result is correct behaviour, not a defect: the fixture contains a
`RUNNING` `echo` task, which is an exclusivity barrier.

---

## 4. Test weaknesses

1. **No coverage of N-8.** No test drives `capacity_backoff_attempts` beyond single digits, so the
   unbounded exponent is invisible to the suite.
2. **No coverage of N-9.** No test injects an `emit` failure on the release path; the existing
   cleanup tests all use a non-throwing `emit`.
3. **`test_authority_policy_matches_real_sandbox_semantics` covers one dimension only.** It mutates
   `SYSTEM_READONLY_BINDS`, which already binds correctly. Nothing pins the mount *modes*
   (`auth`, `venv`, read-only workspace), nothing detects argv appended in `sandbox()` outside the
   plan, and there is no golden-hash pin on the realized argv. The five residues in §1 all survive a
   green suite.
4. **The fingerprint is only asserted for one plan configuration.** No test evaluates
   `sandbox_plan()` for `include_venv=True`, `git_inspection=True` or `service_network=False`, which
   are the configurations real validation and inspection runs use.
5. **Bubblewrap gating is by file presence, not capability.** `Path("/usr/bin/bwrap").is_file()`
   cannot distinguish "absent" from "present but unable to create user namespaces". On a host of the
   latter kind the suite reports 101 failures rather than skipping — a misleading signal for anyone
   reproducing CI locally in a container. Gating on a one-shot functional `bwrap --version`-style
   probe would fix this.
6. **N-11 is unasserted.** Nothing pins the new overtaking behaviour, so a future change to the
   `_claimable_task` predicate could silently restore or further alter queue ordering.
7. **Residual from the previous round, unchanged:** `test_cancel_before_first_operation_step_is_finalized`
   still populates `runner.operations` and attaches the done-callback by hand rather than driving
   `runner.start()`, so deleting the `add_done_callback` in `Runner._loop` would leave it green. I
   re-confirmed the production path behaves correctly, so this is a coverage gap, not a defect.
8. **Positives worth recording.** `test_runtime_releases_slot_before_uncancellable_unbind` is a
   genuinely strong test — it re-acquires the `flock` from outside while `__exit__` is still blocked,
   which is a real structural assertion rather than a restatement of the implementation. The
   `test_retry_rejects_running_tasks_without_orphaning_attempts` parametrisation and the corrected
   `st_mtime_ns` assertion in the V9-03 test are also material improvements over `3ed0472`.

---

## 5. Verification performed

| Command | Result |
| --- | --- |
| `git diff --check 3ed0472 f816b07` | clean |
| `git diff --check bd266a0 f816b07` | clean |
| `.venv/bin/ruff check .` | **All checks passed!** |
| `.venv/bin/ruff format --check .` | **80 files already formatted** |
| `.venv/bin/pytest -q` (Bubblewrap functional) | **508 passed, 2 skipped, 0 failed** (359 s) |

The 2 skips are `tests/test_developer_boundary.py:53` and `:264`, both gated on an installed Codex
CLI, which is not available in any review environment. Every other test — including the entire
Bubblewrap-dependent boundary, hardening and sandbox suite that neither prior review could execute —
ran and passed.

Independent adversarial probes were written and executed against production code paths (real
`TaskService`, real `Runner` loop, real `DeveloperRuntime`, real FastAPI operator API, real `bwrap`
argv, real Alembic upgrade). All probe files were created under `/tmp` and any file temporarily
copied into `tests/` was removed; the working tree was confirmed identical to `f816b07` afterwards.

### Acceptance-criteria results

| # | Criterion | Result |
| --- | --- | --- |
| 1 | Unsupported authority fails at registry construction | PASS |
| 2 | Authority profile/policy version persisted in the approval snapshot | PASS |
| 3 | Changing authority policy/profile invalidates compatibility | PASS for Codex config semantics and the bwrap argv *skeleton*; **partial** for mount modes and appended argv (V9-01 residue) |
| 4 | Codex execution derives permissions from the bound profile | PASS |
| 5 | Scheduler claims never exceed `max_concurrency` | PASS |
| 6 | Different eligible workers run concurrently | PASS |
| 7 | Non-developer tasks remain exclusive | PASS; the N-1 outage path is closed |
| 8 | Cross-process slot locks enforce the same ceiling | PASS (registry-derived ceiling; fork test passes). Lock namespace still shared across databases/workspace roots |
| 9 | Explicit registry cannot expose a non-winning primary worker | PASS |
| 10 | Trusted snapshot hashing off the event loop | PASS; cleanup correctness restored (N-4, N-5 resolved) |
| 11 | All v0.8 approval/source/artifact/validation/security protections intact | **PASS** — the task-lifecycle guarantee (N-1) and operator-cancellation authority (N-3) regressions are repaired, and the full v0.8 suite passes with Bubblewrap functional |
| 12 | Ruff, Ruff format, full pytest suite pass | **PASS** — 508 passed, 2 environment skips |

### Circular dependency and TOCTOU

* **No new circular dependency.** `worker_authority → developer_sandbox → developer_process` is
  strictly one-way; `developer_sandbox` imports nothing from `worker_authority`.
* **No new authority TOCTOU.** `authority_policy_version()` remains computed live on every call
  rather than frozen in a module constant, so `Supervisor.select`/`matches` cannot compare against a
  stale value. Measured cost after folding in the sandbox plan: **59.8 µs per call**, i.e. 0.12 % of
  the 0.05 s poll interval — no meaningful regression against the 53 µs measured at `3ed0472`.
* The V9-01 residue is a *coverage* gap in what the fingerprint hashes, not a time-of-check
  /time-of-use window.

---

## 6. Remaining release blockers

**None.** Both previously release-blocking findings are fixed and verified against production code
paths, and no new blocking defect was found.

Recommended before tagging v0.9 (none individually blocking):

| ID | Severity | Summary |
| --- | --- | --- |
| V9-01 residue | MEDIUM | Five real Bubblewrap authority changes — including an extra host bind and a writable Codex `auth.json` on the Codex worker argv — change the executed argv without changing the approval-bound fingerprint. |
| N-8 | MEDIUM | Unbounded exponent in the backoff computation raises `OverflowError` after 1 025 consecutive deferrals, stranding a phantom `RUNNING` task that blocks all non-developer work until restart. Not reachable at the default approval TTL; reachable when `approval_ttl_seconds` exceeds ~5 110 s. |
| N-9 | LOW | A failing `worker_capacity_released` emit skips the worker unbind (capacity itself is still released correctly). |
| N-2 residual | LOW | No absolute deferral ceiling: ~720 attempts and ~1 400 primary snapshots per hour of sustained contention, terminated only by approval TTL. |
| N-10 | LOW | Design document still claims complete Bubblewrap plan coverage in the fingerprint. |
| N-11 | LOW | Backoff filtering lets later non-developer tasks overtake an earlier deferred developer task; undocumented and untested. |

Non-blocking and unchanged from the original report, re-confirmed present at `f816b07`: V9-06 (no
global execution ceiling), V9-07 (NULL-unsafe UPDATE trigger), V9-08 (orphan attempts bypass the
trigger), V9-10 (unbounded developer overtaking → approval expiry, materially mitigated by the new
backoff), V9-12 (cleanup no longer excludes running executions by lock), V9-14 (`request_authority()`
self-validation), V9-16 (per-poll revalidation cost, measured negligible above). The worker slot lock
namespace (`ROOT/runtime/developer-worker-<worker_id>-<slot>.lock`) is still shared across runtime
databases and workspace roots.

---

## 7. Conclusion

This round of corrective work is substantially better than the previous one. Both release-blocking
regressions are genuinely fixed rather than papered over: the state machine was correctly narrowed
instead of widened, and the deferral requeue was given a single-caller internal transition with its
own guard, which is the right shape for the problem. The backoff is persisted rather than in-memory,
survives restarts, and is reset on exactly the right transitions. The cancellation fixes are
structural — slot release is ordered before any `await`, ownership flags are set only after the
threaded work has genuinely completed, and `uncancellable()` absorbs repeated cancellations — and
they withstood 50-cancellation storms at both the bind and unbind boundaries that go well beyond what
the branch's own tests attempt. The capacity ledger is balanced at every cancellation timing I could
construct, and filesystem slots are immediately reusable. The migration is correct, backfills safely,
and is properly covered including a failure-injection rollback case. Several of the tests added in
this round — particularly the slot re-acquisition during a blocked `__exit__`, and the `st_mtime_ns`
assertion for V9-03 — are real structural assertions rather than restatements of the implementation,
which was the principal criticism of the previous round.

Three qualifications. First, the V9-01 residue is narrower than before but still open: the
`sandbox_plan()` refactor genuinely binds the argv skeleton, yet `sandbox()` retains mount-mode
literals the plan does not drive, and the fingerprint pins only one plan configuration — so a
writable Codex `auth.json` or an appended host bind still changes real worker authority on the
highest-authority path with a byte-identical approval fingerprint, and the design document still
claims otherwise. Second, the new backoff introduced an unbounded exponent (N-8) that is unreachable
at default settings but produces a phantom `RUNNING` task and a scheduler stall for non-developer
work at raised approval TTLs. Third, the cleanup restructuring left one narrow ordering residue (N-9)
where an audit-write failure skips the unbind, though capacity release itself is safe.

This is also the first review in the series able to execute the full suite: with Bubblewrap installed
and functional, **508 tests pass, 2 skip on an absent Codex CLI, and none fail**, with `ruff check`,
`ruff format --check` and `git diff --check` all clean. The v0.8 approval, source, artifact,
validation and security protections are intact, and the v0.9 lifecycle and cancellation guarantees
that regressed at `3ed0472` are restored.

**Release readiness: READY, conditionally.** `f816b07` has no remaining release-blocking findings and
may be merged as v0.9. Before tagging a release I recommend closing N-8 (a one-line exponent clamp
with a regression test) and either closing the V9-01 residue or correcting
`docs/v0.9-worker-authority-design.md` so the documented approval-invalidation guarantee matches what
the fingerprint actually covers — the latter matters because operators will reason about sandbox
changes using that document. N-9, N-11 and the N-2 residual can reasonably be deferred to v0.9.x.

### Reviewed SHA

`f816b076fad74495289e43a9fa784900df63c6e6`
