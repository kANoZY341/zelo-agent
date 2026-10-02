# Browser Core binding implementation handoff

Base: `cde7338996b5b5d06f83fa0ccbf26de2009c3349`, qualified renderer merge
`f728df7`. Working-tree implementation only; no commit, push, PR, release or
production activation. UI and later milestones remain unchanged. No subagents.

## Implementation

- `app/browser_contracts.py`, `app/browser_worker.py`, `app/browser_operator.py`:
  immutable full-contract/operator/request binding, strict private receipt loading,
  narrow real `DocumentRenderer` adapter, shared persistent admission, safe errors,
  byte-bounded untrusted evidence, retained-owner shutdown. Receipt parsing never
  claims actual image inspection or signing/provenance.
- `app/config.py`, `app/main.py`: separate browser origin registry; default disabled;
  registration only after explicit receipt/bundle/directory validation. No runtime
  build/pull. Factory injection is an in-process trusted seam only.
- `app/schemas.py`, `app/models.py`, `app/tasks.py`, `app/runner.py`, `app/workers.py`:
  `browser_run`, BrowserExecutionSpec/BrowserRunResult, exact persisted action
  identity, retry selection, claim/Runner request/config revalidation, events,
  cancellation/deadline, result dispatch and task projections.
- Worker registry/authority/capabilities and Agent executable-kind contracts:
  `browser_core`, `browser_document`, `public-document-render`; no default Agent,
  department, skill route or Mind route. Existing API/Python execution is retained.
- Migration `0029_browser_worker_selection`: browser column/kind; original task
  and attempt trigger SQL restored, with only the Agent kind guard extended for
  browser. New per-action spec immutability permits normal terminal-to-pending
  versioned retry. The prior SQL guard's API/Python Agent-insert exclusion remains
  unchanged; that pre-existing discrepancy is outside this slice.
- `qualification/renderer.py`: optional absolute deadline clips broker/pipe work.
  `qualification/resource_owner.py`: read-only cleanup status properties expose
  retained ownership without changing lease/fence semantics.
- `scripts/prepare_browser_worker.py`: opt-in offline preparation reuses actual
  stopped-image archive verification from `prepare_candidate`; emits a 0600 full
  inventory receipt and preserves only the verified reusable image. Existing
  `cleanup_owned_setup` accepts an explicit verified-image preservation argument.
- README, `.env.example`, binding design: exact setup/configuration and trust limits.
  `.gitignore` also ignores a root `.venv` symlink, used here to reference the
  installed interpreter without installing anything.

## Completed offline verification

Interpreter: `/home/zelo/zelo/core/.venv/bin/python`. All temporary fixtures and
pytest base directories were inside this worktree. No Docker, browser, image build,
compiler, external networking, live settings/database or system service invocation.

1. **459 passed**, three existing warnings:
   `tests/test_browser_core_binding.py tests/test_api_worker.py
   tests/test_browser_renderer.py tests/test_browser_runtime_owner.py
   tests/test_renderer_host_harness.py tests/test_renderer_protocol_host_regressions.py
   tests/test_browser_broker.py tests/test_browser_seccomp.py`.
2. After final drift/cancellation tests and migration expectation updates:
   **82 passed, 5 deselected**, three existing warnings:
   `tests/test_browser_core_binding.py tests/test_database.py -k
   'browser or migration or running_attempt_database or developer_kind'`.
3. Final direct application-lifespan assembly tests: **2 passed, 59 deselected**
   (`tests/test_browser_core_binding.py -k application_assembly`). Scheduler and
   runner start methods are stubbed; actual operator configuration/registration
   and shutdown assembly are exercised. No external resources are started.

4. Campaign-control and scheduler migration preservation cases: **2 passed**,
   three existing warnings (the two migration tests whose head expectations changed).

These runs overlap; do not add their counts. The new Core test file contains 61
cases, covered by the last two targeted runs. Tests exercise actual Core migrations,
TaskService/Runner/Agent binding and qualified ResourceOwner/DocumentRenderer with
fixture HTTP and container handles. Archive tests exercise the verifier algorithm;
preparation orchestration uses a fixture setup helper. None is Docker/browser proof.

Ruff lint, Ruff format check and `git diff --check` passed for implementation-owned
files. Existing qualification-era no-production-import assertions now permit only
the two explicit adapter/config modules. Existing migration-head assertions now
expect 0029; the prior API-only trigger test explicitly upgrades to 0028. No
unrelated runtime tests were weakened.

Earlier broader attempts were interrupted and are **not completed evidence**.
Their reported migration-head and qualification-import failures were addressed by
the tests above. An isolated existing worker-registry run stalled at
`test_selected_worker_identity_and_execution_are_bound_to_registry` and was stopped;
no pass or linked-worktree diagnosis is claimed for that run. Full application
TestClient/developer suites remain parent clean-CI gates; a linked worktree is not
an accepted developer `source_repository`. No full pytest was run here.

## Parent gates and limitations

- Parent-owned `scripts/qualify_browser_core_binding.py` and
  `tests/test_browser_core_host_harness.py` were left untouched. Their presence in
  worktree status is not this implementation's verification evidence.
- Run controlled real-host task-to-renderer probes, including immutable inspected
  image/profile reinspection, cancellation/cleanup and second-process admission.
- Run full pytest in a clean supported checkout, host-only installed-Codex checks,
  independent exact-head review, and exact-head CI after the parent commits.
- Review operator setup on a real host before activation. Receipts trust the local
  operator; runtime cannot reconstruct proof of an earlier Docker export from JSON.
  Unknown cleanup remains fenced with no automatic reconciliation procedure.
- No production activation or published-version change is authorized by these
  offline results. Preserved UI work and later roadmap ordering remain parked.
