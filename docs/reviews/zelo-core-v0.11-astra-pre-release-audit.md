## Audit result

**Three confirmed findings justify delaying v0.11.0.** Three additional findings are non-blocking follow-ups.

Audited commit: `efd01242d42c167ee557d1d56aae7bd18597e036`. The checkout matches that commit and remains clean. No repository contents were modified.

The supplied CI result—667 passed, 2 skipped, Ruff, formatting and diff checks passing—is acknowledged. The findings below concern paths those checks do not cover.

## Confirmed findings

| ID | Finding | Severity | Confidence | BLOCKS_RELEASE |
|---|---|---|---|---|
| Z11-01 | New Python packages bypass protected-module scope restrictions | high | high | yes |
| Z11-02 | Rejected self-improvement candidates remain eligible for promotion | high | high | yes |
| Z11-03 | Reviewer contention can repeat implementation under the same approval | medium | high | yes |
| Z11-04 | Repeated cancellation can interrupt OpenCode process cleanup | medium | high | no |
| Z11-05 | Approved `ai_prompt` tasks can execute a replacement model route | medium | high | no |
| Z11-06 | Promotion smoke entrypoint fails through nested `asyncio.run()` | low | high | no |

### Z11-01 — Protected-module policy can be bypassed by package shadowing

**Location:** [app/self_improvement_planner.py](/tmp/zelo-astra-v011-review/app/self_improvement_planner.py:52): `_HIGH_RISK_EXACT`, `_high_risk_scope()`, `SelfImprovementPolicy.validate()`.

**Affected invariant:** v0.11 self-improvement cannot modify authentication, permissions, sandboxing or other protected control-plane behavior.

**Explanation:** The policy protects existing module filenames such as `app/auth.py`, but broadly permits new paths under `app/`. It does not protect the corresponding Python import namespaces.

A package at `app/auth/__init__.py` takes precedence over `app/auth.py` when importing `app.auth`. The same problem applies to other protected modules.

**Validation/reproduction:**

A read-only policy probe using the actual repository inspection produced:

```text
app/auth.py                         → proposal_high_risk_scope
app/auth/__init__.py                → allowed
app/policy/__init__.py              → allowed
app/developer_sandbox/__init__.py   → allowed
```

Python’s package-over-module resolution was independently confirmed with a virtual filesystem import-resolution probe; no package was created in the repository.

A proposal can therefore declare a new package as a medium-risk change while replacing protected behavior through normal imports. Changing only that declared path can also satisfy the evaluator’s exact-path scope check.

**Impact:** The trusted policy can authorize a control-plane modification it explicitly intends to forbid. This is not an immediate unauthenticated exploit: implementation approval and later adoption remain necessary, and review could catch it. Those controls do not repair the policy misclassification.

**Recommended fix:** Protect import namespaces, including package equivalents and import hooks, rather than only existing filenames. For this milestone, a narrow positive allowlist of permitted modification paths would be easier to defend. Apply the policy to the final candidate manifest as well as the proposal.

**BLOCKS_RELEASE: yes.** This directly contradicts v0.11’s protected-control-plane restriction.

### Z11-02 — Evaluation rejection does not prevent promotion

**Locations:**

- [app/tasks.py](/tmp/zelo-astra-v011-review/app/tasks.py:324): `TaskService._evaluate_self_improvement_child()`
- [app/tasks.py](/tmp/zelo-astra-v011-review/app/tasks.py:413): `_promotion_source()` and `_submit_promotion()`
- [app/runner.py](/tmp/zelo-astra-v011-review/app/runner.py:214): developer outcome selection

**Affected invariant:** A rejected or unproven self-improvement candidate must stop at evaluation; promotion must preserve the meaning of that evaluation.

**Explanation:** Self-improvement evaluation is recorded as an audit event on the parent. It does not change the successful developer outcome or constrain subsequent promotion eligibility.

`_promotion_source()` requires the latest successful developer attempt and `validation_passed=True`. It does not require an accepted self-improvement evaluation or reject independent-review blockers.

Consequently, passing mechanical validation can leave a developer task marked `succeeded` even when the evaluator rejects its scope or reviewer findings.

**Validation/reproduction:** An in-memory database probe exercised the real task service with a linked self-improvement child:

1. Approved proposal scope: `docs/note.md`.
2. Developer result: validation passed, but changed `app/auth.py`.
3. Evaluation: `rejected_candidate`, `scope_valid=False`.
4. Developer task: `succeeded`.
5. `_promotion_source()` accepted the attempt.

This confirms eligibility bypass. The probe did not publish, merge or release anything.

**Impact:** A scope-escaping or otherwise rejected self-improvement result can enter the ordinary promotion pipeline. Separate human promotion, merge and release approvals still exist; this finding does **not** establish autonomous publication or merging. However, the promotion specification does not encode an explicit override of the failed self-improvement evaluation.

There is also a continuity gap to address in the fix: [PromotionReviewer.create()](/tmp/zelo-astra-v011-review/app/promotion_review.py:315) builds a new manifest from the retained workspace. It does not compare that manifest with an immutable artifact identity captured at independent review/evaluation.

**Recommended fix:** Persist evaluation eligibility bound to the exact developer attempt and candidate manifest. Require `accepted_candidate` when submitting, claiming and executing promotion for self-improvement children. Missing, rejected or inconclusive evidence should fail closed. Any operator override should be a distinct, explicit, audited action.

**BLOCKS_RELEASE: yes.** The evaluation boundary currently produces a verdict without enforcing its downstream consequence.

### Z11-03 — Reviewer capacity failure repeats completed implementation

**Locations:**

- [app/developer.py](/tmp/zelo-astra-v011-review/app/developer.py:688): `run_independent_review()`
- [app/developer.py](/tmp/zelo-astra-v011-review/app/developer.py:1019): capacity-error handling in `run()`
- [app/runner.py](/tmp/zelo-astra-v011-review/app/runner.py:265): capacity-error deferral
- [app/tasks.py](/tmp/zelo-astra-v011-review/app/tasks.py:1988): `TaskService.defer()`

**Affected invariant:** One approved v0.11 implementation must not become an automatic repeated execution loop.

**Explanation:** Producer and reviewer slot acquisition use the same `developer_worker_capacity_exhausted` error.

Reviewer acquisition happens **after** the producer has run and validation has completed. If the reviewer is busy, that error propagates to the runner, which defers the entire developer task.

Deferral preserves the approval, discards the attempt result and returns the task to pending. The next attempt runs implementation again in a fresh workspace. The explicit prohibition on retrying self-improvement children does not cover this scheduler path.

**Validation/reproduction:** A controlled runtime probe held reviewer capacity unavailable while allowing implementation and validation to complete:

```text
attempt 1 → capacity error; implementation calls: 1
attempt 2 → capacity error; implementation calls: 2
```

The ordinary contention tests exercise producer-slot exhaustion before implementation, which is a different and legitimate deferral case.

**Impact:** Concurrent developers sharing a reviewer can repeatedly consume model calls, create workspaces and generate different candidates under one approval. Backoff reduces retry frequency but does not bound total implementation executions.

**Recommended fix:** Distinguish pre-execution capacity deferral from post-implementation review contention. Reserve reviewer capacity before implementation, or retain the exact candidate and resume only review under a bounded continuation. Until that exists, post-implementation contention should stop a self-improvement child and require a fresh run.

**BLOCKS_RELEASE: yes.** This violates the documented single bounded cycle and no automatic repeated implementation requirement.

### Z11-04 — OpenCode cleanup is interruptible by repeated cancellation

**Location:** [app/opencode_worker.py](/tmp/zelo-astra-v011-review/app/opencode_worker.py:429): `_managed_server()`, especially its `finally` block at line 482.

**Affected invariant:** Cancellation must finish process termination and reaping before execution capacity is released.

**Explanation:** `_managed_server()` directly awaits `stop_process()`. A second cancellation can interrupt its wait before the SIGKILL/reap stages finish.

The general subprocess implementation already handles this situation with `uncancellable()`; the OpenCode managed-server path does not use that protection.

**Validation/reproduction:** A harmless TERM-resistant child substituted for the launcher remained alive after two cancellations interrupted managed-server cleanup. The probe explicitly killed and reaped it afterward.

This proves interruptible cleanup. It does **not** prove that the production Bubblewrap launcher survives the same sequence or establish a sandbox escape.

**Impact:** The adapter can unwind without proving its child has stopped. With a surviving process, later task finalization and capacity release could overlap continued execution.

**Recommended fix:** Protect termination, reaping and drain-task cleanup with the existing cancellation-resistant cleanup mechanism. Add repeated-cancellation tests using the actual sandbox launcher and verify descendants are dead before capacity release.

**BLOCKS_RELEASE: no.** OpenCode is disabled by default, and production launcher survival was not established. Treat this as a required fix before relying on cancellation safety in an OpenCode-enabled deployment.

### Z11-05 — Model-route drift does not invalidate `ai_prompt` approval

**Locations:**

- [app/tasks.py](/tmp/zelo-astra-v011-review/app/tasks.py:1183): `_valid_action()`
- [app/runner.py](/tmp/zelo-astra-v011-review/app/runner.py:336): `_model_worker()`
- [app/gateway.py](/tmp/zelo-astra-v011-review/app/gateway.py:150): `ModelGateway.generate()`

**Affected invariant:** Execution must use the model selection covered by approval.

**Explanation:** Approval validation reconstructs the action using the persisted model selection, but does not compare that selection with the current router for `ai_prompt`.

Execution calls `generate(profile, ...)`, which resolves the profile again using the current router. Unlike `generate_action()`, it receives no expected model and does not enforce returned model identity.

**Validation/reproduction:** An in-memory task-service probe submitted and approved an `ai_prompt`, replaced the route/configuration, then claimed and executed it:

```text
approved model: approved-model
executed model: replacement-model
task status: succeeded
```

**Impact:** A queued approved prompt can execute under a different model after configuration changes or restart. This is a stale-approval bug, not evidence of external network or tool-authority escalation.

**Recommended fix:** Revalidate alias, model and configuration at approval/claim, enforce expected selection during execution, and validate the returned model identity. Require fresh approval after drift.

**BLOCKS_RELEASE: no.** This affects the existing `ai_prompt` path; the agent and self-improvement paths have separate model-binding checks.

### Z11-06 — Promotion smoke command cannot reach its intended workflow

**Locations:**

- [scripts/smoke_promotion.py](/tmp/zelo-astra-v011-review/scripts/smoke_promotion.py:114): `smoke()`
- [scripts/smoke_promotion.py](/tmp/zelo-astra-v011-review/scripts/smoke_promotion.py:202): `main()`
- [app/developer_process.py](/tmp/zelo-astra-v011-review/app/developer_process.py:197): `run_sync()`

**Affected invariant:** The documented operational smoke test must be executable.

**Explanation:** `main()` invokes `asyncio.run(smoke(...))`. Inside that active event loop, `smoke()` calls synchronous developer request construction, which reaches another `asyncio.run()` through `run_sync()`.

**Validation/reproduction:** Invoking the smoke coroutine with configured settings failed before workspace creation:

```text
RuntimeError: asyncio.run() cannot be called from a running event loop
```

Other synchronous helpers in the smoke also need consistent event-loop handling.

**Impact:** Operators cannot obtain the advertised end-to-end promotion smoke evidence through this entrypoint.

**Recommended fix:** Make orchestration synchronous, or consistently offload synchronous service operations and await asynchronous operations. Test the complete entrypoint using a fake external publisher.

**BLOCKS_RELEASE: no.** This breaks the verification script, not the normal promotion API.

## Uncertain observations

These are not counted as confirmed vulnerabilities or additional blockers.

- **Self-hosted runner isolation:** [.github/workflows/ci.yml](/tmp/zelo-astra-v011-review/.github/workflows/ci.yml:1) runs push/PR dependency installation and tests directly on a self-hosted runner. Read-only repository permissions and disabled checkout credential persistence are useful, but do not isolate executed code. The repository does not establish runner ephemerality, separation from Core state/credentials, or external PR approval settings. Confirm those deployment properties.

- **Remote merge races:** Merge checks inspect PR identity, base and CI before issuing the merge request. The merge request binds the head SHA; repository-side protections are needed to enforce relevant conditions atomically. Branch protection and merge-queue configuration were not available for verification. No successful race was demonstrated.

- **Actual vendor permission enforcement:** Claude and OpenCode tests verify policy configuration and transport behavior, but provide less direct adversarial evidence about the actual CLI’s filesystem, credential and network enforcement than the Codex boundary checks. No confirmed escape was found. Fake-worker transport tests should not be presented as proof of vendor permission enforcement.

## Coverage

The audit inventoried all **113 tracked files** and reviewed the repository beyond the release diff.

Major components and boundaries inspected:

- AGENTS.md, canonical architecture/workspace decisions, milestone designs, operational documentation and prior review records.
- Authentication, API schemas, approval binding/invalidation, task transitions, idempotency, cancellation, retry and recovery.
- Model routing, native tools, bounded agent execution and repository inspection filtering.
- Self-improvement inspection, proposal grounding, protected scope, developer linkage, independent review and evaluation.
- Worker registry/supervisor, worker identity and artifact binding, concurrency limits and capacity deferral.
- Codex, Claude and OpenCode adapters; Bubblewrap construction, credentials, subprocess cleanup and trusted validation.
- Candidate manifests, promotion commits/publication, CI verification, merge and release authority.
- SQLite models, all migrations, constraints, triggers and migration rollback/retry behavior.
- Scripts, dependencies and CI configuration.
- Test coverage and fixtures across those components, with deeper review of security, lifecycle, migration, concurrency and promotion assertions.

Validation included targeted in-memory and controlled-process reproductions. The supplied full-suite result was not independently rerun during this read-only audit.

## Positive findings

Important controls appear correctly implemented:

- Task APIs consistently require operator authentication; no confirmed unauthenticated authority path was found.
- Developer approvals bind worker selection, identity, authority semantics, sandbox configuration, validation identity and limits.
- Worker artifacts are pinned to sealed bytes, addressing executable replacement between approval and execution.
- Reviewer selection excludes the producer worker instance, including alternate registry identities for that instance.
- Validation uses trusted tooling and a constrained environment; executable Git configuration is neutralized.
- Promotion manifests account for content, modes and symlink targets, with rechecks after the promotion snapshot is created.
- Publication, merge and release remain separate approval-bound operations. No automatic merge/tag/release/deploy path from self-improvement was found.
- SQLite serialization, foreign keys, running-attempt constraints and append-only audit protections provide substantial lifecycle safeguards.
- Released migrations were preserved; the new migration follows the existing transactional migration approach.
- Model proposals cannot directly select executables, credentials, concrete workers or promotion authority.

## Test gaps

Prioritized additions:

1. **Protected namespace attacks:** package shadowing, import hooks and new executable paths that affect protected modules.
2. **Evaluation-to-promotion enforcement:** rejected, inconclusive and missing evaluations; high-severity reviewer findings; candidate mutation between evaluation and promotion snapshot.
3. **Reviewer contention after implementation:** producer executes exactly once; retries cannot silently create a new candidate.
4. **Repeated cancellation:** actual OpenCode sandbox process and descendants, including cancellation during startup and teardown.
5. **Stale model approval:** route/configuration changes across restart for `ai_prompt`, plus provider-returned identity mismatch.
6. **Complete smoke entrypoint:** exercise orchestration rather than only helper functions.
7. **Vendor boundary probes:** actual supported CLI versions attempting forbidden reads, credential access and network use.
8. **Remote promotion interleavings:** base/check changes immediately before merge, with documented server-side protection requirements.

## Release blockers

Only these findings independently justify delaying v0.11.0:

- **Z11-01:** Protected control-plane behavior can enter the supposedly permitted self-improvement scope.
- **Z11-02:** Rejected self-improvement candidates remain promotion-eligible.
- **Z11-03:** Reviewer contention can turn one approved implementation into repeated automatic executions.

Fixes should be verified together: protected-scope enforcement, accepted evaluation bound to the exact candidate, and no automatic reimplementation after work has started.

## Non-blocking follow-ups

- **Z11-04:** Make OpenCode teardown cancellation-resistant before relying on that adapter’s stop guarantees.
- **Z11-05:** Enforce approved model selection for `ai_prompt`.
- **Z11-06:** Repair and test the promotion smoke entrypoint.
- Establish documented runner isolation and remote branch-protection evidence.

## Final verdict

**HOLD RELEASE**
