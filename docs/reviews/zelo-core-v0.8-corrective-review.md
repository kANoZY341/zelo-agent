# Zelo Core v0.8 — Independent Corrective Adversarial Review (PR #8)

- Repository: `kANoZY341/zelo-agent`
- Base: `main` @ `e5adf16e37fdf17701ddfc6fb4ca378421624864`
- PR head: `4f53a3466bed09ed7e24113ba3e4808cb868cde5` (`fix/v0.8-adversarial-review`)
- Scope: read-only review of the diff `e5adf16...4f53a34` (19 files, +271/-96)
- Reported CI at head: Ruff check, Ruff format, full pytest — green (not treated as evidence of correctness)

Method: static review of the full diff plus independent dynamic reproduction against both
base and head trees (a throwaway copy of the base tree was created under `/tmp`; the
repository working tree was not modified except for adding this report).

**Headline result: PR #8 fixes most of the reported findings correctly, but the H1/M5
implementation introduces a reproducible CRITICAL regression that disables the entire
`developer_run` feature in production. PR #8 must not be merged as-is.**

---

## CRITICAL

### C-N1 — `approval_compatible()` runs `asyncio.run()`/`run_sync()` on the runner event loop; every `developer_run` approval is invalidated and the task is cancelled

- **File / function:** `app/developer.py::DeveloperRuntime.approval_compatible`
  (lines 206–227), reached from `app/tasks.py::TaskService._valid_action` (line 803),
  `_expire_or_invalidate` (869), `expire_approvals` (882) and `claim` (1115), which are
  called synchronously from `app/runner.py::Runner._loop` (lines 71–72) and from the
  FastAPI lifespan (`app/main.py:96`).
- **Exact problem:** PR #8 replaced the cached `selected.worker.identity()` call with

  ```
  head = run_sync(git_argv("rev-parse", "HEAD"), cwd=self.source_repository)   # asyncio.run(...)
  ...
  with selected.worker.bind(request.worker) as bound_worker:
      identity = asyncio.run(bound_worker.preflight())
  ```

  `run_sync` is documented in `app/developer_process.py:197-202` as *"Submission/preflight
  only; HTTP submission already runs in a worker thread. Execution uses run_process
  directly, so Git never blocks the runner event loop."* Both new calls are `asyncio.run`,
  which raises `RuntimeError: asyncio.run() cannot be called from a running event loop`
  when invoked from `Runner._loop`. That `RuntimeError` is swallowed by the broad
  `except (DeveloperError, OSError, ValueError, RuntimeError)` handler and converted into
  `return False`, i.e. "approval no longer valid".

  Before PR #8 the process-lifetime identity cache (populated during
  `DeveloperRuntime.request()`, which runs in the FastAPI thread pool) meant
  `approval_compatible` performed no `asyncio.run` on the loop path and returned `True`.
  The cache that H1 removed was, accidentally, the only thing keeping this path working.
- **Realistic failure scenario:** operator submits a `developer_run`. Within one runner
  poll (`runner_poll_seconds` default **0.05 s**, `app/config.py:27`) `expire_approvals()`
  runs `_valid_action` → `approval_compatible` → `RuntimeError` → `False`. The approval is
  marked `invalidated`, an `approval_invalidated` audit event is written and the task is
  transitioned to `CANCELLED` — before a human can even see the approval. The same happens
  for already-approved tasks in `claim()`. The developer agent can never execute again.
  Secondary effect: a `RuntimeWarning: coroutine 'run_process' was never awaited` is raised
  on every poll (an un-awaited coroutine is leaked each time).
- **Reproduced (head vs base), one runner-style poll inside a running loop:**

  | tree | `asyncio.run(lambda: service.expire_approvals())` result |
  |---|---|
  | base `e5adf16` | `awaiting_approval`, approval `pending` |
  | head `4f53a34` | `cancelled`, `error=approval_invalidated`, approval `invalidated` |

  Equivalent direct probe: `approval_compatible(request, 30)` returns `True` when called
  from a sync context and `False` when called from inside a running event loop at head;
  at base it returns `True` in both.
- **Does an existing test catch it?** No. Every test calls `service.expire_approvals()` /
  `service.claim()` from synchronous test functions (`tests/test_developer.py:409`,
  `tests/test_developer_hardening.py:94`, `tests/test_worker_registry.py:212,244`,
  `tests/test_promotion_task.py:164,285`) where `asyncio.run` is legal, and the only
  runner-driven tests call `Runner._run(...)` directly with an execution obtained outside
  the loop (`tests/test_developer_boundary.py:136,533`, `tests/test_promotion_task.py:32`).
  `Runner._loop` is never exercised with a `developer_run` approval. This is precisely the
  blind spot that let green CI hide a total feature outage.
- **Recommended fix:** do not perform approval revalidation with `asyncio.run` on the loop.
  Options, in order of preference:
  1. Make the revalidation path asynchronous end-to-end: `async def approval_compatible`
     that `await`s `run_process`/`bound_worker.preflight()`, with async
     `expire_approvals()`/`claim()` variants used by `Runner._loop`; keep thin sync
     wrappers for the HTTP thread-pool paths (`decide()`, submission).
  2. Or offload the whole blocking revalidation to a worker thread
     (`await asyncio.to_thread(...)`) *outside* the SQLite transaction, then apply the
     boolean result in a short transaction.
  3. Independently: narrow the exception handler so an infrastructure `RuntimeError`
     ("cannot be called from a running event loop") cannot masquerade as a security
     decision. Fail closed, but emit a distinct diagnostic code
     (e.g. `approval_revalidation_unavailable`) instead of `approval_invalidated`.
- **Required regression test:**
  1. `async` test that submits a healthy `developer_run`, then runs one iteration of the
     real runner path (`await` an async expire/claim, or drive `Runner._loop` for one
     poll) and asserts the task is still `awaiting_approval`/claimable and the approval is
     still `pending`/`approved`.
  2. The same harness with a replaced Codex executable, asserting invalidation still
     happens from the loop path (so the fix cannot be "always return True").
  3. Run the suite with `-W error::RuntimeWarning` (or `filterwarnings = error`) so an
     un-awaited `run_process` coroutine fails the build.

---

## HIGH

### H-N2 — Approval revalidation performs unbounded blocking work inside an open SQLite transaction

- **File / function:** `app/tasks.py::TaskService.expire_approvals` /
  `_expire_or_invalidate` / `claim` → `app/developer.py::approval_compatible`.
- **Exact problem:** `expire_approvals()` opens `self.database.transaction()` and then, for
  every waiting task, calls `_valid_action`, which after PR #8 performs: a `git rev-parse`
  subprocess, a full content hash of `ROOT/.venv` (`validation_manifest`, ~102 MB in this
  checkout), and a complete Codex `bind()` + `preflight()` (copying the executable and the
  code-mode host into sealed memfds, hashing each twice, then spawning five bubblewrapped
  processes). Measured cost with a *fake* worker: **0.32 s per call**; with the real
  `CodexCliWorker` it is seconds. All of it happens while holding the SQLite write
  transaction, and (once C-N1 is fixed) on the runner poll loop with a 0.05 s period.
- **Realistic failure scenario:** with C-N1 fixed but this unchanged, the runner spends
  100 % CPU re-hashing the venv and re-launching Codex ~20×/s per pending approval, the
  SQLite writer lock is held for seconds at a time, HTTP writes get `database is locked` /
  503s, and every other task kind starves. This is a self-inflicted DoS.
- **Does an existing test catch it?** No; no test measures revalidation cost, transaction
  duration or poll throughput.
- **Recommended fix:** compute revalidation *outside* the transaction and rate-limit it.
  Cache the worker identity keyed by the *observed artifact identity*
  (`st_dev, st_ino, st_size, st_mtime_ns, st_ctime_ns` + digest) rather than for the whole
  process lifetime — that satisfies H1 (a replaced executable changes the key) without
  re-executing Codex on every poll. Do the same for `validation_manifest` and
  `publisher_identity`. Optionally decouple revalidation cadence from
  `runner_poll_seconds`.
- **Required regression test:** a test asserting that N consecutive
  `expire_approvals()` calls with an unchanged environment perform at most one
  `preflight()`/`validation_manifest()` (counter/monkeypatch), and a test asserting
  revalidation still detects a replaced executable/venv on the next call.

---

## MEDIUM

### M-N3 — Transient environment/IO errors irreversibly cancel approved work

- **File / function:** `app/developer.py::approval_compatible`;
  `app/developer_validation.py::validation_manifest`; `app/tasks.py::_expire_or_invalidate`.
- **Exact problem:** `validation_identity()` raises `validation_environment_changed` /
  `validation_environment_unavailable` if any venv file's `st_size/mtime/ctime` changes
  *while it is being hashed*, or on any `OSError`. `approval_compatible` maps every such
  error to `False`, and `_expire_or_invalidate` reacts by permanently cancelling the task
  and marking the approval `invalidated` (no retry, no distinct reason). At a 0.05 s poll
  cadence over a 102 MB tree, an operator running `pip install`, a backup job touching the
  venv, or a slow disk will destroy pending approvals.
- **Does an existing test catch it?** No — `tests/test_worker_registry.py`'s new
  `test_validation_identity_change_invalidates_pending_approval` monkeypatches
  `validation_identity` to a constant, so it only covers deliberate drift, not transient
  failures.
- **Recommended fix:** distinguish "changed" (invalidate) from "temporarily unavailable"
  (leave pending, emit a diagnostic, retry with backoff).
- **Required regression test:** simulate `OSError`/`validation_environment_unavailable`
  from `validation_identity` and assert the approval remains pending rather than cancelled.

### M-N4 — M1 fails closed but has no pagination, an ambiguous diagnostic, and accepts booleans

- **File / function:** `app/promotion_ci.py::PromotionCIVerifier.verify` (lines 81–92).
- **Exact problem:** the new guard is
  `if not isinstance(total_count, int) or total_count < 0 or total_count != len(runs)`.
  Correct as a fail-closed truncation check for the `per_page=100` request, but:
  (a) there is still no pagination, so a repository that legitimately produces >100 check
  runs on a head SHA becomes permanently unmergeable; (b) truncation and a malformed
  response both raise `promotion_ci_response_invalid`, so operators cannot tell a GitHub
  schema change from a real anomaly; (c) `isinstance(True, int)` is `True` in Python, so
  `{"total_count": true, "check_runs": [one_run]}` passes (harmless today, but it is an
  unintended type acceptance in a trusted-parse routine).
- **Does an existing test catch it?** Truncation: yes
  (`tests/test_promotion_ci.py::test_truncated_check_run_response_fails_closed`).
  Pagination, diagnostic separation, boolean `total_count`: no.
- **Recommended fix:** paginate until `len(collected) == total_count` with a hard page cap,
  reject `bool`, and raise a dedicated `promotion_ci_response_truncated`.
- **Required regression test:** two-page check-run response verified successfully; >cap
  pages rejected with the truncation code; `total_count: true` rejected.

### M-N5 — H4 is documented only in a class docstring

- **File / function:** `app/developer_contracts.py::WorkerDescriptor` docstring.
- **Exact problem:** the metadata-only semantics of `authority` and `max_concurrency` are
  stated only in source. There is no v0.8 design document (`docs/` contains only
  `v0.7-review-promotion-design.md`) and `README.md` does not describe the supervisor or
  registry at all. Nothing in code, audit events (`supervisor_worker_selected` logs
  `worker_id`, `implementation`, `registry_version`, `priority` only) or the API falsely
  claims enforcement — so the finding is not actively misleading — but the operator-facing
  documentation gap remains.
- **Must be fixed before real Claude/Ox workers are added:**
  1. `authority` must be *enforced*: the value in the selected descriptor has to drive the
     sandbox profile passed to `app/developer_sandbox.sandbox(...)`, and a descriptor
     requesting authority the worker implementation cannot honour must fail closed at
     registration.
  2. `max_concurrency` must be enforced by an actual scheduler; today `claim()` allows
     exactly one `RUNNING` task globally (`app/tasks.py:1086`), so any descriptor value
     other than `1` is already a lie the moment a second worker exists.
  3. `descriptor_for_worker()` hard-codes `worker_id="codex_core"`,
     `authority=("workspace-write",)` and `priority=100`; multi-worker assembly needs
     explicit per-worker descriptors plus a documented routing/tie-break policy.
  4. Registry identity should cover the *bound implementation* of each worker, not only its
     descriptor (see L-N9).
- **Recommended fix:** add `docs/v0.8-worker-registry-design.md` and a README section that
  state the metadata-only semantics and the enforcement roadmap above.

---

## LOW

### L-N6 — Stale audit-event documentation
`docs/v0.7-review-promotion-design.md:236` still lists `promotion_reverified`, which is
emitted nowhere in `app/`. The document also omits `promotion_branch_reused` and
`promotion_pr_reused`, which `app/promotion.py:57,65` do emit. M3 renamed the merge/release
events consistently across code, tests and docs, but this neighbouring drift remains.
Fix: delete `promotion_reverified` and document the two `*_reused` events.

### L-N7 — `pin_artifact` `finally` can still leak the second descriptor
`app/developer_artifacts.py:93-96`: if `os.close(source_fd)` raises, `artifact_fd` is never
closed. Wrap each close in `contextlib.suppress(OSError)` or use an `ExitStack`.
No test covers this.

### L-N8 — `PUT` widened the transport verb allowlist without an endpoint allowlist
`app/promotion_github.py::GhApiTransport.request` now accepts `PUT` (with body) for any
path starting with `/repos/`. Today all callers are trusted and endpoints are built from
`settings.promotion_repository` plus validated integers, so this is not exploitable, but
`PUT /repos/{o}/{r}/…` also covers branch protection, collaborators and content writes.
Fix: allowlist the exact (method, endpoint-shape) pairs the promotion flow needs.

### L-N9 — Registry version covers descriptors only, not the bound worker instance
`app/worker_registry.py::_registry_version` hashes descriptors only, so replacing a
registered worker object with a different instance of the same implementation yields an
identical `registry_version`. This is mitigated — `approval_compatible` and the execution
path both compare the full `WorkerIdentity` (executable path + sha256 + code-mode host),
and `descriptor.implementation` must equal `identity.implementation` — so it is a
defence-in-depth note, not a hole. Verified by
`test_selected_worker_identity_and_execution_are_bound_to_registry`.

### L-N10 — Merge/CI re-verification does not re-check `head.repo.full_name`
The M6 guard exists only in `GitHubPromotionPublisher._pull_request`.
`PromotionCIVerifier.verify` and `PromotionMergeRuntime._current_pr` re-verify number,
state, refs and SHAs but not the head repository. GitHub does not allow a PR's head
repository to change after creation and `pr_number` is approval-bound, so this is not
currently exploitable; adding the same check there would make the invariant uniform.

### L-N11 — Explicit-registry runtimes can still be constructed desynchronized
`DeveloperRuntime.__init__` accepts `worker` and `registry` independently and never checks
that `worker` is the registered worker; only the setter is now guarded
(`worker_registry_explicit`). Execution always goes through `self.registry.get(...)`, so
production behaviour is safe, but `runtime.worker` can still read a worker that is not the
one that will run. Fix: when `registry` is explicit, either reject a `worker` argument or
validate membership. Also note `self.registry` / `self.supervisor` remain public mutable
attributes that tests reassign independently (`tests/test_worker_registry.py:207-210`).

### L-N12 — No regression test for genuine source-HEAD drift
The M5 HEAD check has no test that moves the *real* source repository HEAD while a spec is
untouched; `tests/test_developer.py` only tampers with `developer_spec.base_sha`, which is
already caught by `action_hash`. Add a test that commits to the fixture source repo and
asserts the pending approval is invalidated.

---

## NO ISSUE / VERIFIED

- **C1 — merge endpoint.** `app/promotion_merge.py:141` now sends `PUT` to
  `/repos/{repo}/pulls/{n}/merge`, matching GitHub's documented contract.
  `GhApiTransport.request` allows `{GET, POST, PUT}` and accepts a body only for
  `POST`/`PUT` (`--input -`); `GET` with a body is still rejected; `DELETE`/`PATCH` remain
  rejected. `tests/test_promotion_merge.py` asserts the method, endpoint and exact body,
  and the "no side effect" assertions were tightened to `method not in {"POST","PUT"}`.
- **H1 — identity/executable revalidation.** `_identity_cache` is gone from
  `CodexCliWorker`; `identity()` is now a thin `asyncio.run(self.preflight())`.
  `approval_compatible` binds `request.worker`, so `pin_artifact` compares the resolved
  path and the sha256 of the *copied bytes* against the approved values **before** yielding
  and therefore before anything is executed — replacement of either the Codex executable or
  `codex-code-mode-host` raises `worker_configuration_changed` with zero instructions from
  the new bytes (`test_approved_binary_replacement_invalidates_approval_without_execution`
  asserts the marker file is never created). memfd sealing, the independent re-hash of the
  sealed copy and the `before/after` `fstat` check are unchanged, and execution still
  re-binds and re-pins (`app/developer.py:513-519`). No new TOCTOU window: the approval
  check pins a throwaway copy and the execution path re-pins under the same approved
  digest. **The security intent is met; it is the event-loop mechanics that fail (C-N1).**
- **H2 — registry version.** `_registry_version` hashes a canonical JSON payload
  (`sort_keys`, `separators`, `ensure_ascii`, `allow_nan=False`) over descriptors sorted by
  `worker_id`. Independently verified: adding, removing or mutating any descriptor field
  changes the version; registration order does not; the digest is 64 hex chars, exactly the
  `WorkerSelectionSnapshot.registry_version` limit. `routing_policy` is *not* folded into
  the version and is compared separately in `Supervisor.matches`, so it stays independently
  approval-bound. `WorkerDescriptor.model_validate(descriptor.model_dump(mode="json"))`
  re-runs the canonicalisation validator, so a `model_copy(update=...)` descriptor cannot
  enter the registry (covered by `test_registry_revalidates_descriptor_instances`).
  The removal of the caller-supplied `version=` kwarg closes the spoofing path.
- **H3 — `DeveloperRuntime.worker`.** The setter raises
  `DeveloperError("worker_registry_explicit")` when an explicit registry was supplied, so a
  worker/registry desynchronization can no longer be created silently; the legacy
  single-worker path still rebuilds descriptor, registry and supervisor together. Existing
  tests that assign `runtime.worker = pinned_worker` use implicit-registry fixtures and are
  not masking a production bug, because execution resolves workers exclusively via
  `self.registry.get(...)`. (See L-N11 for the residual constructor gap.)
- **M2 — promotion publisher binding.** `_valid_action` now compares
  `request.publisher != publisher_identity(self.settings)` for `promotion_run` (merge/
  release already did) and catches `DeveloperRuntimeError` so a broken/missing `gh` fails
  closed instead of raising. `publisher_identity` re-hashes the `gh` binary and re-resolves
  the config directory each time, so an executable or `GH_CONFIG_DIR` swap invalidates the
  approval. Covered by `test_promotion_publisher_change_invalidates_pending_approval`.
- **M3 — audit semantics.** `merge_reverified` → `merge_execution_started` and
  `release_reverified` → `release_execution_started`; both are emitted *before*
  `promotion_merge_runtime.run(...)` / `promotion_release_runtime.run(...)`, so no event
  claims a successful re-verification that has not happened, and a failed verification now
  leaves only a truthful "started" record followed by `merge_failed`/`release_failed`.
  Code, tests and `docs/v0.7-review-promotion-design.md` agree (except L-N6).
- **M5 — source/validation identity.** The intent is implemented: source HEAD drift and
  validation-environment drift are both checked at approval time, and the execution-time
  checks at `app/developer.py:506-519` are unchanged. `validation_manifest` reads only the
  venv tree and the five named project input files (`INPUTS`), never arbitrary untracked
  user files. **However the implementation sits on the broken loop path (C-N1) and pays the
  cost described in H-N2 and M-N3.**
- **M6 — PR head repository identity.** `head_repo.get("full_name") != self.repository`
  is enforced for both reused and newly created PRs; a missing/`null` `head.repo` raises
  `promotion_github_response_invalid` via `_dict`. The `head={owner}:{branch}` query alone
  would still match a different repository under the same owner, so the check is
  meaningful. Covered by `test_existing_pr_from_wrong_head_repository_fails_closed`.
- **M7 — `pin_artifact` exception handling.** Setup is now wrapped in an inner
  `try/except OSError` that still yields `codex_executable_unavailable`, while the `yield`
  sits outside it, so an `OSError` raised in the caller's `with` body propagates unchanged
  (`test_pin_executable_preserves_oserror_from_caller`). Both descriptors are still closed
  in the outer `finally`; no artifact-lifetime change (the fd stays valid for the whole
  `with` body). See L-N7 for the residual close-ordering nit.
- **H4 — metadata semantics.** No code path, audit event, API response or document claims
  that `authority` or `max_concurrency` are enforced; the descriptor docstring now states
  they are declarative metadata in v0.8. See M-N5 for the documentation gap and the
  prerequisites before real Claude/Ox workers.
- **Other adversarial checks with no finding:** no fail-open path was introduced (every new
  guard raises or returns `False`); no mutable trusted state was added (registry mapping is
  still a `MappingProxyType` of frozen dataclasses over frozen contracts); hashing is
  deterministic and order-independent; no retry/idempotency regression in merge (the
  already-merged reuse path still performs no write, now asserted for `PUT` too) or release;
  worker selection ordering (`-priority`, then `worker_id`) is unchanged; no new privilege
  or confused-deputy path (the worker still never receives GitHub credentials).

---

## Explicit verdicts

1. **C1 verdict — FIXED.** `PUT` is used for the merge endpoint, the transport supports it
   safely, bodies are restricted to `POST`/`PUT`, `GET`/`POST` behaviour is intact and the
   tests encode the real GitHub contract. (Optional hardening: L-N8.)
2. **H1 verdict — SECURITY INTENT MET, BUT NOT ACCEPTABLE AS SHIPPED.** The stale
   process-lifetime cache is gone and replacement of the Codex executable or the
   code-mode host is detected without executing the new bytes. However removing the cache
   exposed that the revalidation path calls `asyncio.run` on the runner event loop, which
   now invalidates every developer approval (**C-N1**), and it makes each revalidation
   unboundedly expensive (**H-N2**).
3. **H2 verdict — FIXED.** The registry version is content-derived, order-independent,
   sensitive to every descriptor change, routing policy stays separately bound, and
   descriptors are revalidated on insert so `model_copy` cannot bypass canonicalisation.
4. **H3 verdict — FIXED (minor residual, L-N11).** Explicit-registry runtimes can no longer
   silently desynchronize via the setter; legacy single-worker behaviour is preserved; the
   tests that use `runtime.worker` are not hiding a production bug.
5. **H4 verdict — PARTIALLY ADDRESSED.** Nothing falsely implies enforcement, but the
   metadata-only semantics are documented only in a docstring, and `authority` /
   `max_concurrency` enforcement plus per-worker descriptors are hard prerequisites before
   any second (Claude/Ox) worker is registered (M-N5).
6. **M1 verdict — FIXED for truncation, INCOMPLETE overall.** Truncated responses fail
   closed and normal responses still pass; malformed `total_count` fails safely; but there
   is no pagination (>100 checks are permanently unmergeable), the diagnostic is ambiguous,
   and `bool` is accepted as `int` (M-N4).
7. **M2 / M3 / M5 / M6 / M7 verdicts —**
   - M2: **FIXED** (publisher identity revalidated for promotion, merge and release;
     `DeveloperRuntimeError` handled).
   - M3: **FIXED** (no event claims re-verification before it happens; naming consistent in
     code, tests and docs; minor unrelated doc drift in L-N6).
   - M5: **LOGIC CORRECT, DELIVERY BROKEN** — HEAD and validation-environment drift are
     checked before execution and only bounded trusted inputs are read, but the check runs
     on the event-loop path (C-N1) and is expensive/brittle (H-N2, M-N3).
   - M6: **FIXED** (reused and newly created PRs must both have
     `head.repo.full_name == approved repository`; a same-ref/same-SHA fork is rejected).
   - M7: **FIXED** (setup `OSError` → `codex_executable_unavailable`; caller `OSError`
     propagates unchanged; no FD leak in normal operation; no artifact-lifetime change).
8. **New regressions introduced by PR #8:**
   - **C-N1 (CRITICAL)** — developer approvals are invalidated and tasks cancelled on the
     first runner poll, in production only; green CI cannot see it.
   - **H-N2 (HIGH)** — blocking, unbounded revalidation work (git, full venv hash, Codex
     pin + five sandboxed spawns) inside the SQLite transaction on a 0.05 s poll loop.
   - **M-N3 (MEDIUM)** — transient venv/IO errors irreversibly cancel approved tasks.
   - **Minor** — un-awaited `run_process` coroutine leaked on every failed revalidation
     (`RuntimeWarning`); `promotion_ci` accepts `bool` as `total_count`.
   No regression was found in promotion, merge, release, worker selection, sandboxing,
   hashing determinism or GitHub contract handling beyond the above.
9. **Is PR #8 safe to merge? — NO.** It is a net security improvement, but merging it as-is
   makes `developer_run` unusable: every submitted or approved developer task is cancelled
   with `approval_invalidated` within one poll interval. Fix C-N1 (and ideally H-N2) plus
   add the loop-path regression tests, then re-review. Everything else in the PR can stay
   as written.
10. **Does anything block v0.8.0? — YES.** C-N1 is release-blocking and H-N2 should be
    fixed in the same change. M-N3 and M-N4 should be resolved before tagging; M-N5
    (H4 enforcement/documentation) is blocking for any release that registers a second
    worker, not for v0.8.0 itself.

---

### Evidence appendix (reproduction notes)

- One runner-style poll executed inside a running event loop
  (`asyncio.run` wrapping `TaskService.expire_approvals()`), fresh DB, fake worker,
  disposable fixture source repository:
  - base `e5adf16` → task `awaiting_approval`, approval `pending`
  - head `4f53a34` → task `cancelled`, `error=approval_invalidated`, approval `invalidated`
- `DeveloperRuntime.approval_compatible(request, 30)` at head: `True` from a synchronous
  caller, `False` from inside a running event loop; at base: `True` in both.
- `approval_compatible` wall time at head with a fake worker and a 102 MB `.venv`: 0.32 s
  (real `CodexCliWorker` additionally copies/hashes the binary and spawns five processes).
- Registry determinism probe: order-independent version, version changes on add/remove/any
  field change, digest length 64.
- Targeted suites re-run locally and green:
  `tests/test_worker_registry.py`, `tests/test_promotion_ci.py`,
  `tests/test_promotion_github.py`, `tests/test_promotion_merge.py` (29 passed, 8 skipped).
  The sandbox-dependent suites were skipped/failed locally only because `bubblewrap` is not
  installed in this review environment.
