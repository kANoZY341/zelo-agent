# Zelo live project status

Verified: 25 September 2026

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

## v0.11 pre-release audit closure

GPT-6 Astra audited pre-release main commit
`efd01242d42c167ee557d1d56aae7bd18597e036` and found three release blockers.
Corrective PR #16 closed them and the later focused review findings. Exact-head
CI and the final Codex re-review were clean before merge.

The historical audit remains at
`docs/reviews/zelo-core-v0.11-astra-pre-release-audit.md`.

Non-blocking follow-ups remain for OpenCode cancellation cleanup, stale
`ai_prompt` model-route approval, the promotion smoke entrypoint, self-hosted
runner isolation evidence, remote merge interleavings and deeper vendor-boundary
probes.

## Active increment: Early Zelo Chat

Branch: `feat/early-zelo-chat`.

The target path is:

```text
operator text
  -> persistent Zelo Mind conversation
  -> direct reply or one typed Core task proposal
  -> existing TaskService policy/approval boundary
  -> existing runner and workers
  -> authoritative task status/result projected back into conversation
```

This increment does not pull forward v0.12 autonomous events/wake cycles/campaigns,
v0.13 memory, v0.14 Agent Factory, v0.15 broad tools/capabilities, or v0.16
WhatsApp/email/live voice.

## Parked work

`feat/v1.0-agent-factory` remains parked. Reuse compatible ideas selectively at
v0.14; do not merge the branch wholesale.

## Next actions

1. Finish and verify the Early Zelo Chat Core contract.
2. Prove conversation -> typed Core task -> real result -> conversation projection.
3. Run full exact-head CI and focused review.
4. With the Core path proven, adapt the workspace UI without creating a parallel
   authority system.
5. Continue into v0.12 durable events, wake cycles and campaigns.
