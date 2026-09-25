# Zelo live project status

Verified: 25 September 2026

This file records current implementation state. Architecture and roadmap intent
live in `docs/zelo-canonical-architecture-roadmap.md`; workspace direction lives
in `docs/zelo-workspace-decision.md`.

## Released baseline

- Zelo Core v0.10.0 is the latest published release.
- Release commit:
  `666a0a4d12d280187af02a621e1ab4b57ce604c6`.
- v0.10 established the multi-worker foundation: tasks, approvals, audit, Model
  Gateway, Tool Gateway, Developer Runtime, Worker Registry/Supervisor, Codex,
  Claude Code and OpenCode adapters, worker authority, validation, independent
  review, promotion boundaries and recovery/concurrency controls.

## v0.11 integration state

- Milestone: v0.11 — Self-Improvement Seed.
- Version metadata: `0.11.0`.
- PR #14 — `v0.11 — Self-Improvement Seed` was squash-merged into `main`.
- Verified main commit:
  `efd01242d42c167ee557d1d56aae7bd18597e036`.
- Main CI run #562 succeeded on the Zelo self-hosted runner.
- Verification: `git diff --check`, Ruff, Ruff format, and pytest all passed.
- Test result: 667 passed, 2 skipped, 1 existing Starlette/httpx deprecation
  warning.
- v0.11.0 has **not** been tagged or published.

## v0.11 implemented behavior

The merged implementation provides one bounded cycle:

```text
trusted repository inspection
  -> strict policy-checked ImprovementProposal
  -> exact goal/SHA/inspection/model binding
  -> persisted self_improvement_run
  -> approval-gated developer_run child
  -> Supervisor worker selection
  -> implementation
  -> trusted validation
  -> approval-bound independent review
  -> deterministic ImprovementEvaluation
  -> stop before merge/release
```

The planner cannot select a concrete worker, executable, credential, authority
profile, merge method or release tag. Promotion, merge and release remain
separate approval-bound operations.

## Pre-release audit

GPT-6 Astra performed a read-only full-repository pre-release audit of exact main
commit `efd01242d42c167ee557d1d56aae7bd18597e036`.

The audit verdict is **HOLD RELEASE**. Three findings are release-blocking:

1. `Z11-01` — protected Python module namespaces can be shadowed by package
   paths not covered by the proposal policy.
2. `Z11-02` — a rejected or inconclusive self-improvement evaluation can still
   leave the developer attempt eligible for ordinary promotion.
3. `Z11-03` — reviewer-capacity contention after implementation can defer the
   whole developer task and repeat implementation under one approval.

The full report is retained at
`docs/reviews/zelo-core-v0.11-astra-pre-release-audit.md`.

Non-blocking follow-ups are tracked for OpenCode repeated-cancellation cleanup,
stale `ai_prompt` model-route approval, the promotion smoke entrypoint, runner
isolation evidence, remote merge interleavings and vendor-boundary probes.

## Corrective work

- Corrective branch: `fix/v0.11-astra-blockers`.
- Scope is limited to the three release blockers plus their regression tests and
  audit/status documentation.
- No v0.11.0 tag or release may be created until the exact corrective head passes
  CI and the corrective delta receives an independent re-review.

## Parked work

`feat/v1.0-agent-factory` remains parked. Reuse compatible ideas selectively at
v0.14; do not merge the branch wholesale.

## Next actions

1. Finish and verify the three Astra release-blocker corrections.
2. Run full CI on the exact corrective branch head.
3. Perform an independent corrective review against the original Astra findings.
4. Merge the corrective PR only after the fixes and review are clean.
5. Re-run CI on the resulting `main` commit.
6. Tag and publish `v0.11.0` only after Ahmad separately authorizes release.
