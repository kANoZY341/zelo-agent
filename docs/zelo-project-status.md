# Zelo live project status

Verified: 26 September 2026

This file records current implementation state. Architecture and roadmap intent
live in `docs/zelo-canonical-architecture-roadmap.md`; workspace direction lives
in `docs/zelo-workspace-decision.md`.

## Released baseline

- Zelo Core v0.11.0 — Self-Improvement Seed is the latest published release.
- Exact release commit:
  `b2c4a7d13fd8e64c6badfbae89d2a29629fd3c12`.
- Release tag: `v0.11.0`.
- PR #14 introduced the bounded self-improvement seed.
- PR #16 corrected the Astra pre-release blockers and subsequent focused review findings.
- Main CI run #636 succeeded on the exact release commit.
- The final focused Codex review of the corrective head reported no major issue.

## v0.11 released behavior

The release provides one bounded cycle:

```text
trusted repository inspection
  -> strict policy-checked ImprovementProposal
  -> exact goal/SHA/inspection/model binding
  -> persisted self_improvement_run
  -> approval-gated developer_run child
  -> Supervisor worker selection
  -> implementation
  -> trusted validation
  -> independent review bound to an immutable candidate snapshot
  -> deterministic ImprovementEvaluation
  -> stop before autonomous merge/release
```

The planner cannot select a concrete worker, executable, credential, authority
profile, merge method or release tag. Promotion, merge and release remain
separate approval-bound operations.

## Early Zelo Chat — completed and live-proven

The post-v0.11 conversation prerequisite is complete on `main`.

Relevant merged work:

- PR #17 — persistent Early Zelo Chat and Mind -> Core conversation contract.
- PR #18 — keep large Core result context available to Mind.
- PR #19 — enforce the serialized Unicode/result projection budget.
- PR #20 — final Ruff formatting correction.

Current verified main commit:

`f920036d9c1631b8ddf999554b3c10437602cbf5`

Main CI #695 succeeded with:

`697 passed, 2 skipped, 1 warning`

A live local proof was completed on 26 September 2026 (Asia/Dubai):

```text
Ahmad -> Zelo Mind direct conversation
      -> Mind requests an agent_run Core task
      -> Core Agent Runtime executes repo_status
      -> task succeeds with authoritative repository state
      -> later Mind turn reads the persisted Core projection
      -> Zelo answers from that result without rerunning or guessing
```

The live task correctly reported branch `main` and the then-current dirty working
tree. This proves the required conversation -> Core task -> real result ->
conversation-context path outside the automated test suite.

## Active increment: Zelo Workspace Foundation

The workspace implementation choice is now made:

- Zelo owns the workspace application and contracts.
- Mission Control is the primary control-desk UX/component reference and a
  selective MIT-licensed reuse source.
- Agent Town is the Company World/Phaser reference for a later visual slice.
- Neither project becomes Zelo Core, task authority, scheduler, memory owner or
  agent runtime.

The first implementation slice is intentionally narrow:

```text
browser
  -> local Zelo Workspace server
  -> server-side authenticated Core proxy
  -> existing conversation API
  -> Zelo Mind
  -> optional canonical Core task
  -> projected progress/result
  -> browser
```

The browser must never receive the Core operator token. Workspace task state is
always projected from Core rather than duplicated in a UI database.

## Parked work

`feat/v1.0-agent-factory` remains parked. Reuse compatible ideas selectively at
v0.14; do not merge the branch wholesale.

## Next actions

1. Land and smoke-test the first Zelo-owned Talk-to-Zelo workspace.
2. Verify browser -> Mind -> Core task -> live projected result with the same
   contract proven by curl.
3. Add practical Control Desk projections over existing Core state.
4. Enter v0.12 with durable GoalSpec/project/campaign ownership, events,
   checkpoints, budgets and bounded scheduling.
5. Add Company World only as a projection over real Core/company state; do not
   pull Agent Factory or broad autonomy forward.
