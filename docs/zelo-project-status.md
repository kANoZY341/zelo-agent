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

## Zelo Workspace Foundation — completed

The first Zelo-owned workspace is merged on main.

Current verified main commit before v0.12 work:

`5738d6f7e0435941abfafb13a5b4bb86e7dd4d66`

Relevant merged work includes:

- PR #21 — initial Zelo-owned Talk workspace.
- PR #22 — workspace feedback/latency and generated-file cleanup.
- PR #23/#24 — compact and then two-stage Mind routing.
- PR #25 — compact persistent Zelo identity.
- PR #27 — dedicated Mind-model route plus fixed-height scrollable chat viewport.

The browser keeps the Core operator token server-side, conversation/task state is
projected from canonical Core state, and the transcript now scrolls inside a
fixed workspace viewport rather than stretching the page.

A local live smoke on 26 September 2026 confirmed the dedicated Mind route with
`qwen3.5:4b`. Warm conversational turns were materially faster than the original
single-stage path and the model showed stronger conversational behavior. The
remaining gap was grounded persistent project context: Mind could converse but
could not reliably answer what Zelo is currently building without explicit
context.

## Active increment: v0.12 Campaign ownership and operational state

The Project/Goal + Context Resolver seed is merged on main through PR #28. The
workspace conversation-history follow-up is merged through PR #29.

Active branch:

`feat/v0.12-campaign-state`

Design:

`docs/v0.12-campaign-state.md`

This slice adds the next ownership/state boundary:

- durable Campaign records owned by Goals;
- Core tasks optionally owned by one Campaign;
- immutable task dependency edges within that Campaign;
- runner gating so a dependent task cannot be claimed before all dependencies succeed;
- append-only campaign events;
- immutable versioned campaign checkpoints.

This slice does not add scheduling, leases, budgets, automatic wake cycles,
persistent v0.13 memory, or the Research specialist.

## Parked work

`feat/v1.0-agent-factory` remains parked. Reuse compatible ideas selectively at
v0.14; do not merge the branch wholesale.

## Next actions

1. Land and live-prove Campaign ownership, dependency gating, events and checkpoints.
2. Add leases/recovery and shared budget accounting on top of Campaign state.
3. Add bounded scheduling and Mind wake cycles without weakening Core authority.
4. Add the first fixed Research specialist and workspace project/progress views.
5. Enter v0.13 with scoped persistent memory, provenance, correction,
   supersession and bounded retrieval after project/state ownership is stable.
