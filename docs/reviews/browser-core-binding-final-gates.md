# Browser Core binding final gates — 3 October 2026

PR #67 merged at `bc8404e3257573701683448255311d79f3d27cdf`. Its Git tree equals exact
reviewed/tested head `0486e15153adaf5983d5341a69cdbf01390ec93e`. Production Browser Worker
remains disabled; live settings, databases, services and UI were not changed.
Published release remains v0.14.0.

## Completed evidence

- Full exact-commit CI run 37078156201: **1,617 passed, 2 skipped**,
  639.27 seconds. Diff, Ruff lint and formatting passed in that run.
- Core binding/capability suite: **90 passed**. Includes independent SQLite writer
  access during submission/retry/claim scans; configuration/action drift;
  cancellation/shutdown during claims; initial Runner deadlines; sequential late
  cleanup and overlapping pre-lifecycle uncertain-owner retention.
- Both installed-Codex checks skipped by CI passed separately in a standalone
  exact-commit clone: **2 passed**, no model request.
- Final controlled host proof: **eight cases passed**, no setup cleanup errors.
  HTTP/DNS used fixtures; actual TaskService/Runner persistence, Chromium, Docker,
  admission and cleanup were exercised. This is not public-website evidence.
- Astra Medium full static review of `8fa5202`, followed by exact-commit reviews
  of cleanup remedies through `0486e15153adaf5983d5341a69cdbf01390ec93e`: **no remaining blocking
  defect within combined static scope**. Reviews did not claim execution success.

Counts overlap earlier focused runs and must not be added together. Superseded
or cancelled full-suite/CI runs are not completed verification for the final head.

## Controlled host cases

1. Actual Chromium text persisted with matching contract/origin and untrusted evidence.
2. Long-node previews preserved truthful truncation.
3. Success/evidence waited for cleanup; a separate contender was denied.
4. Lost Docker-create acknowledgement failed without releasing evidence.
5. Cancellation after rendering withheld evidence and confirmed cleanup.
6. Deadline after rendering withheld evidence and confirmed cleanup.
7. A terminal task with pending cleanup retained its fence; a second task made
   no HTTP request or container before cleanup was confirmed.
8. A fresh task succeeded after confirmed cleanup.

Final host report on the development host:
`runtime/browser-core-binding/core-proof-snwveca1/result.json`.
Supplement: `runtime/pr67-overlap-supplement.log`.
Static review chain: `runtime/pr67-preflight-review.md`,
`runtime/pr67-retention-review.md`, `runtime/pr67-overlap-review.md`.
Negative controls in separate disposable clones detected deliberately removed
Runner deadline enforcement and removed retention pruning. Candidate files were
not mutated by those controls.

## Limits and next gate

Same-UID operators, filesystem ancestors, source artifacts and Docker are trusted.
Receipts establish structural identity; operator stopped-image inspection supplies
image payload evidence. Neither is signing or independent provenance. Two-phase
validation does not freeze the filesystem; mandatory execution rescans remain.
Unknown cleanup stays fenced and retained, with no automatic reconciliation.

Next qualify real default DNS/TLS/public-document fetching in an isolated Core
instance under `docs/v0.15-browser-operator-qualification.md`. No production
activation, new Agent routing, UI merge or release is authorized by these proofs.
