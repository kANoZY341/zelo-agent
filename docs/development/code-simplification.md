# Code simplification

Use when a completed change contains unnecessary complexity or a review identifies
a concrete clarity problem. A successful pass makes the code easier to understand
while preserving its behavior.

## Procedure

1. Understand the changed code, its callers, and the reason for its current
   structure. Inspect neighboring conventions and relevant history before
   removing an apparent duplication or abstraction.

2. Name the specific problem: unclear responsibility, misleading name, excessive
   nesting, genuine duplication, or unreachable code. Leave already clear code
   alone.

3. Check the contract to preserve: inputs, outputs, errors, side effects, ordering,
   cancellation, cleanup, identity checks, and permission refusals.

4. Make a small, reviewable change within the authorized scope. Prefer explicit
   control flow and useful names. Keep comments that explain constraints or why
   a check exists.

5. Run relevant verification and inspect the resulting diff. A simplification
   that requires changing behavioral expectations needs a separate explanation
   as a behavior change.

6. Keep broad refactoring distinct from a bug fix or feature so each can be
   reviewed and reverted. Do not create additional PRs or cleanup work merely
   to satisfy this procedure.

## Zelo-specific checks

Repeated checks on opposite sides of an `await`, callback, approval, claim, or
retry can enforce different moments in time. Do not merge or remove them merely
because their conditions look identical.

Preserve default-deny behavior, exact identity bindings, bounded resource use,
cleanup guarantees, and distinctions between declared intent and effective
authority. A shorter path that changes any of these is not a simplification.

Do not remove an adapter or testable boundary without understanding its current
purpose. Do not introduce a new abstraction solely for a hypothetical future use.

## Verification and stopping

Use existing tests for the behavior being preserved, plus a new regression only
for a concrete uncovered risk. Run established CI gates for the final change;
there is no requirement to rerun the whole repository suite after every edit.

Finish when the diff is clearer, scoped, and behaviorally equivalent. If a
proposed simplification obscures a contract or lacks convincing evidence, leave
that part unchanged and explain the reason in review.

## Attribution and adaptation

Adapted from Addy Osmani's MIT-licensed
[code-simplification](https://github.com/addyosmani/agent-skills/blob/1401c8b8030e023baeebb31781a6653fe8e93026/skills/code-simplification/SKILL.md),
which credits inspiration from Anthropic's Code Simplifier.

Preserves project conventions, exact behavior, incremental changes, and clarity
over line count. Zelo makes async authorization and refusal behavior explicit,
uses relevant verification during editing, and does not impose a separate PR
for every small refactor.
