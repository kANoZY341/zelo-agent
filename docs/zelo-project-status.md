# Zelo live project status

Verified: 24 September 2026

This file records current implementation state. Architecture and roadmap intent
live in `docs/zelo-canonical-architecture-roadmap.md`; workspace direction lives
in `docs/zelo-workspace-decision.md`.

## Released baseline

- Zelo Core v0.10.0 is published.
- Release commit:
  `666a0a4d12d280187af02a621e1ab4b57ce604c6`.
- v0.10 established the multi-worker foundation: tasks, approvals, audit, Model
  Gateway, Tool Gateway, Developer Runtime, Worker Registry/Supervisor, Codex,
  Claude Code and OpenCode adapters, worker authority, validation, independent
  review, promotion boundaries and recovery/concurrency controls.

## Active milestone

- Milestone: v0.11 — Self-Improvement Seed.
- Branch: `feat/v0.11-self-improvement`.
- Verified branch head before this status update:
  `e98e572cbb8075e1bd4a0b0e134a066b395682d3`.
- Version metadata: `0.11.0`.
- Final CI on that head: success.
- Verification: `git diff --check`, Ruff, Ruff format, and pytest all passed.
- Test result: 637 passed, 2 skipped, 1 existing Starlette/httpx deprecation
  warning.
- Pull request: not yet opened at the time this status entry was written.

## v0.11 implemented behavior

The branch implements one bounded cycle:

```text
trusted repository inspection
  -> strict policy-checked ImprovementProposal
  -> exact goal/SHA/inspection/model binding
  -> persisted self_improvement_run
  -> approval-gated developer_run child
  -> Supervisor worker selection
  -> implementation
  -> trusted validation
  -> approval-bound independent review context
  -> deterministic ImprovementEvaluation
  -> stop before merge/release
```

Protected control-plane/self-improvement paths are excluded from v0.11
self-modification. The planner cannot select a concrete worker, executable,
credential, authority profile, merge method or release tag.

## Parked work

`feat/v1.0-agent-factory` remains parked. Reuse compatible ideas selectively at
v0.14; do not merge the branch wholesale.

## Next actions

1. Keep the canonical roadmap/workspace decision synchronized with the operator's
   latest explicit decisions.
2. Open the v0.11 pull request to `main`.
3. Perform independent/adversarial review of the exact PR head.
4. Fix legitimate findings and rerun the full acceptance gate.
5. Demonstrate one complete bounded v0.11 cycle and retain its evidence.
6. Present merge/release evidence to Ahmad.
7. Merge/tag/release only after the separately authorized decision.
