# PR #98 integrated live topology review — 7 October 2026

Direct continuing-ChatGPT review; no independent Codex/model run is claimed.

## Source binding

PR #98 integrated current main at code-bearing commit `0aac2b7`, tree
`4bd2cdc5f88fd6c8d266d72547aaeb2439aa830f`. Before the live proof the focused
Agent-computer/egress/worker/context selection passed 56 tests and Ruff,
formatting and diff checks passed.

The supervisor image was rebuilt with no cache from the PR #98 deployment files
and exact pinned OpenBot revision
`b6932d31a8d6e7896c15139dfc27a6c6911deb27`. The resulting supervisor image ID
was `sha256:02f291d4e58234335aaa73932ad237d0e60e5bba82753524efd7a43d5a92efa0`.

## Matching-head live proof

A disposable topology used:
- internal network `zelo-pr98-live-sandbox`, `172.30.253.0/24`;
- Core proxy/gateway `172.30.253.1:4393`;
- actual Agent computer control `172.30.253.3:4100`;
- allowlisted origin `https://example.com`.

The real supervisor returned the private URL and no published port. Core accepted
the exact private endpoint and rejected an explicit `127.0.0.1:4100` substitute
while internal topology was configured. The Agent container contained neither
host master token.

A direct public curl from the Agent container failed. Real Chromium navigation
to the allowlisted origin succeeded through the authenticated Core proxy with
HTTP 200 and title `Example Domain`. Browser permission was then revoked while
the computer remained alive; the next navigation failed with HTTP 502.

This used OpenBot's direct `/navigate` endpoint only as a qualification harness.
PR #98 still exposes no Agent-executable navigate/click/type/write/shell action.

## Cleanup and evidence

After the proof, the exact Agent container, both persistent proof volumes, the
supervisor container, both disposable networks and proof listeners were removed.
Post-cleanup inventory was zero for the proof namespace.

The ignored sanitized receipt is
`runtime/pr98-live-proof.json`, SHA-256
`53b4174eb55b22d5e43365d01c8f621817ae0c014835214ba1965113a8444139`.

No blocker remains in the internal-topology scope. Merge still requires fresh
exact-head full CI on the documentation-bearing head. PR #99 remains the separate
named-endpoint navigation gate; click/type/write/shell and arbitrary URLs are not
cleared here.
