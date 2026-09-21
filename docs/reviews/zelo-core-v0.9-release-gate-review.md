# Zelo Core v0.9 — Release Gate Review

Targeted release-gate review of the final hardening changes after the last independent corrective review.

| Item | Value |
| --- | --- |
| Repository | `kANoZY341/zelo-agent` |
| Branch | `feat/v0.9-worker-authority` |
| Exact implementation SHA reviewed | `0ea4503c76f1e541054033d9090931ddb85e19da` |
| Previous independently reviewed SHA | `f816b076fad74495289e43a9fa784900df63c6e6` |
| Diff reviewed | `f816b07..0ea4503` |
| Scope | V9-01 residue, N-8, N-9, deferred-task overtaking, regression risk |

## Verdict

**RELEASE READY**

No new release-blocking defect was found in the targeted final diff.

## V9-01 — Bubblewrap authority / sandbox fingerprint residue

**RESOLVED for the v0.9 release gate.**

The final implementation now separates and binds the relevant sandbox policy surfaces:

- worker sandbox policy is included in the authority-policy fingerprint through
  `worker_sandbox_plans()`;
- trusted validation/inspection sandbox policy is included in the request's
  `sandbox_version` through `trusted_boundary_sandbox_plans()`;
- the real `sandbox()` path builds mounts and filesystem operations from
  `sandbox_plan()`;
- namespace inclusion, lifecycle `die-with-parent`, network isolation and cwd are rendered from
  plan values rather than independently selected by the execution path;
- workspace mode, git metadata mode, auth mount mode, venv mounts, host binds, Codex artifact
  materialization and remount behavior are represented in canonical plan data.

The production call shapes were cross-checked against the bound configurations:

- Codex preflight -> worker preflight plan;
- Codex probe / local command probe / execution -> worker execution plan;
- validation -> validation boundary plan;
- git inspection / ignored-file inspection -> git-inspection boundary plan;
- review inventory -> read-only inventory boundary plan.

The added regressions cover the previously demonstrated escape cases, including auth mount widening,
venv mount widening, read-only workspace widening, extra host bind, isolated-network removal,
PID-namespace removal, `die-with-parent` removal and cwd changes. These changes alter the real argv
and the corresponding policy digest / compatibility decision.

The renderer remains trusted code that translates canonical semantic plan entries into Bubblewrap
arguments; the security-relevant decisions themselves are now carried by the hashed plan data.

## N-8 — capacity-backoff exponent overflow

**RESOLVED.**

`capacity_backoff_seconds()` clamps the exponent before exponentiation using a bounded maximum
derived from the configured initial/max delays. Large persisted counters therefore cannot force an
unbounded integer-to-float conversion before the delay ceiling is applied.

Regression coverage includes counters at 1,025 and 1,000,000 and verifies:

- task returns to `PENDING`;
- attempt becomes `INTERRUPTED`;
- approval remains approved;
- retry deadline remains bounded by `CAPACITY_BACKOFF_MAX_SECONDS`;
- the Runner capacity-deferral path does not strand a phantom `RUNNING` task.

## N-9 — release-audit failure and structural cleanup

**RESOLVED.**

The cleanup sequence now closes the filesystem worker slot before any awaited unbind, captures a
failure from the `worker_capacity_released` audit emission, then still performs worker
`__exit__` and artifact cleanup before re-raising the audit failure.

The regression test verifies that when release-event persistence fails:

- the task reaches a terminal `FAILED` state rather than remaining phantom-`RUNNING`;
- the bound worker exits exactly once;
- artifacts are closed exactly once;
- the filesystem slot is immediately reusable.

This preserves structural capacity safety even when audit persistence itself fails.

## Deferred-task overtaking

**ACCEPTED / INTENTIONAL.**

Tasks whose developer-capacity backoff window is still closed are intentionally excluded from the
currently claimable queue. A later runnable task may therefore execute first. This prevents
head-of-line blocking and is now both documented and covered by a scheduler regression.

## Regression assessment

The final hardening diff is narrow and does not reopen the previously resolved lifecycle,
cancellation or concurrency paths:

- normal `RUNNING -> PENDING` remains forbidden outside the internal capacity-deferral path;
- operator cancellation remains authoritative over deferral;
- cancellation-safe bind/unbind and structural slot release remain intact;
- V9-03 remains defended by the trusted `GIT_OPTIONAL_LOCKS=0` invariant and index mtime/digest
  regression;
- the v0.8 approval/source/artifact/validation boundaries were not weakened by this final diff;
- migration behavior is unchanged from the previously reviewed `0010_capacity_backoff` migration.

## Verification evidence

For exact implementation SHA `0ea4503c76f1e541054033d9090931ddb85e19da`:

- GitHub Actions pull-request check: **success**
- GitHub Actions push check: **success**
- implementation-agent verification: **521 passed, 2 skipped**
- implementation-agent `git diff --check`: clean
- implementation-agent Ruff check: pass
- implementation-agent Ruff format check: pass

This release-gate review independently inspected the targeted implementation diff and the relevant
production/test paths. It did not independently rerun the full Bubblewrap test suite outside GitHub
CI; the exact-SHA CI results above provide the execution gate.

## Final release decision

**RELEASE READY**

The implementation at `0ea4503c76f1e541054033d9090931ddb85e19da` has no remaining
release-blocking finding in the targeted v0.9 gate. The PR can proceed to final ready-for-review,
human approval, merge, and v0.9.0 release steps.
