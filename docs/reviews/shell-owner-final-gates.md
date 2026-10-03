# Shell identity and durable owner final gates

PR [#70](https://github.com/kANoZY341/zelo-agent/pull/70) merged at
`befaefa6d2460b4bbaeed46b0e764d06c8c6802c` from reviewed/tested head
`132b4456213710657b60ba21b0bd2000b62a1416`. Both Git trees are
`a7f62ddb04f15453bd914cf2bb8c3aa260ce1e84`.

This qualification binds fixed immutable Shell identity to actual execution,
reuses durable admission unchanged and shares only policy-neutral lifecycle
mechanics with the strict Browser wrapper. Per-call native/source/pin settlement
observations precede creation and gate publication/release. Uncertain cleanup or
owner death fences replacements; no TTL, global/error-string inference, marker
reconciliation, Shell Core task or default activation was added.

| Gate | Exact-head result |
|---|---|
| CI run 37090660359 | 1,729 passed, 2 skipped, 11 warnings; pytest 634.34 seconds |
| Installed-Codex non-model supplements | Both skipped checks passed in standalone exact-head clone |
| Affected focused suite | 218 passed |
| Lint/format/diff checks | Passed |
| Owned normal and invalid ambient native proofs | Each passed 17 cases at clean exact head |
| Direct Shell boundary compatibility | 14 cases passed at clean exact head |
| Controlled Core/DNS/HTTP/Chromium lifecycle | 8 real cases passed; all labelled setup cleanup confirmed |
| Pin-evidence negative control | Discarding close uncertainty failed three expected regressions |
| Independent Astra Medium static review | Full review and exact-head followup: no remaining blockers in combined scope |

Review corrections require explicit artifact close settlement while attempting
all closes, protect startup cleanup and acknowledgement together through a
second cancellation during suspended cleanup, and bind each crash-descendant
pidfd to revalidated ancestry/start-time/kernel identity plus the whole tree.
The proof kills only its exact directly owned harmless fixture parent; descendant
pidfds observe exit without signalling numeric descendants. Its restart marker
remains fenced. Reaped boundary launchers and observed crash exits are distinct.

Ignored private evidence remains under `runtime/`: owner normal
`owner-proof-cvsy8jkv`, ambient `owner-proof-bewoheru`, direct
`shell-proof-dk7a7xcu`, Browser `core-proof-1z_9amcv`, full/followup review reports,
CI log and gate JSON. Source hashes identify observed installed bytes, not
independent provenance. Host dependencies, complete relevant process visibility,
trusted ancestors/operator and a live owning event loop remain assumptions.

Next is a separately reviewed Core binding with exact persisted worker/spec/action
identity and submission/retry/claim/Runner gates, still disabled by default and
without Agent/Mind routes or broader process/filesystem access. Published v0.14.0,
production Browser disabled and parked UI #61 remain unchanged.
