# Browser operator qualification — final gates

PR #68: https://github.com/kANoZY341/zelo-agent/pull/68

Merged main: `025d01971ab7cca92c0e9e8226fdd65128736cac`.
Exact reviewed/tested candidate: `c10725f48d31a84d8e3b7bb0d29bc9139b328827`.
Their Git trees were checked equal after merge.

| Gate | Evidence |
|---|---|
| Full CI | Run 37081055030; 1,647 passed, 2 skipped, 11 warnings; 648.26s |
| Installed-Codex supplement | Both skipped non-model boundary checks passed in an exact-commit standalone clone |
| Focused tests | 146 passed; Ruff lint/format and diff checks passed |
| Real public DNS/TLS | Two Core tasks at fixed https://example.com/ passed, including readmission, persisted untrusted evidence, shutdown and cleanup |
| Ambient isolation host proof | Same exact clean commit passed with deliberately invalid Docker context/host/config and ZELO settings |
| Controlled host proof | Eight fixture HTTP/DNS cases passed with real Core, Chromium, Docker and cleanup; no setup cleanup errors |
| Post-run image proof | Stopped-image bytes/modes matched prepared inventory; diagnostic-owned setup resources removed; clear admission |
| Independent static review | Astra Medium full review at 609eb39 found Docker environment/daemon blocker; final c10725f follow-up closed it with no remaining blocker in combined scope |
| Negative control | Removing subprocess environment isolation in a disposable clone caused the credential-isolation regression test to fail |

Diagnostic evidence in the dedicated worktree:

- `runtime/browser-operator/operator-proof-shj5zs33/result.json` — final normal public run.
- `runtime/browser-operator/operator-proof-zofaip2c/result.json` — final ambient-override public run.
- `runtime/browser-core-binding/core-proof-e46vsrhx/result.json` — eight controlled lifecycle cases.
- `runtime/operator-isolation-review.md` and `runtime/operator-review.md` — combined static review.
- `runtime/operator-final-gates.json` and `runtime/operator-ci-summary.txt` — gate record.

Real public runs observed 15 DOM nodes, 156 text characters and no truncation.
Source content is mutable; this is one origin at recorded retrieval times. The
controlled proof remains distinct from real DNS/TLS evidence. Static review did
not independently execute tests or verify host/CI results. Trusted source files,
same-UID operator, filesystem ancestors and Docker remain assumptions. Receipts
are structural configuration, not signatures or independent provenance.

Production Browser Worker remains disabled; release v0.14.0 and parked UI #61
remain unchanged. Next v0.15 slice is Shell boundary qualification, not production
activation or a default Browser Agent/Mind route.
