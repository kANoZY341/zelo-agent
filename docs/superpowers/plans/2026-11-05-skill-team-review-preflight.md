# Skill publication team-review preflight

## Context

The optional Skill sender already requires protected `main`, immutable source,
exact CI, a durable no-retry attempt, and an approved local review in solo mode.
Team mode currently accepts the branch-protection review policy without reading
the current PR review state itself. This plan adds a bounded provider GET at
the exact reviewed PR head before the durable attempt and again before the only
possible merge PUT. The live repository currently has zero required GitHub PR
approvals, so this cannot qualify team publication by itself.

## Choice and threat boundary

Keep the existing GitHub transport, sender and one-attempt ledger. Read PR
identity and paginated reviews through that same transport. Require the
effective latest submitted review by each independent human reviewer to be
`APPROVED` at the frozen head, with at least the branch policy's required
count. Missing, malformed, stale, dismissed, superseded or truncated data
refuses. A provider policy or review change after the attempt consumes the
attempt and sends zero PUT. Do not store reviewer prose or credentials.

The alternative is to rely solely on GitHub branch protection at the PUT.
That external gate remains essential, but a local exact-head observation is
needed to explain and refuse an unreviewed send before consuming the one-use
attempt whenever possible. The GET does not create approval authority.

## Implementation

1. Add no-network tests for current exact-head approval, author/self review,
   stale/dismissed/superseded review, malformed/paginated data, and review
   drift between the durable attempt and PUT. Preserve solo-mode behavior.
2. Implement a bounded team-only review preflight in
   `SkillPublicationOneAttemptService`, reusing the attested transport and
   returning only a boolean decision. Bind the required count from the fresh
   protection policy at both use points.
3. Verify targeted sender/API tests, Ruff, an independent read-only review,
   exact-head CI and separate main CI. Keep the sender default-disabled.

No live Skill PR is sent or merged by this code slice. Its runtime enablement,
operator decision and broader Skill publication qualification remain separate.
