# Qualified Shell Core binding final gates

PR [#71](https://github.com/kANoZY341/zelo-agent/pull/71) merged at
`fd686a9044ddee01e1221d35117e72220c9af0ec` from reviewed/tested head
`484257bc40abd2c62b8372d892b3d7dfc1a7908f`. Both Git trees are
`6a5acc111b6dd8fd53a95f513612627805718d37`.

The default-disabled lane binds script digest, trusted worker selection, fixed
sealed policy and operator/admission identity to persisted Core task action.
Submission/retry verify sources outside writer transactions; claim/Runner reject
missing, stale or tampered identity. Contention defers a valid queued action.
Explicit enabled assembly validates prerequisites; disabled assembly does not
scan sources or create a guest. No default Agent/Mind route or broader Shell,
process/filesystem authority, live enablement, service or release was added.

| Gate | Result |
|---|---|
| Exact-head CI 37094399860 | 1,792 passed, 2 skipped, 11 warnings; pytest 681.88 seconds |
| Installed-Codex non-model supplements | Both passed in a standalone exact-head checkout |
| Clean-source developer/self-improvement checks | Both passed in that checkout |
| Focused Shell/metadata/import regressions | 255 passed |
| Other development compatibility checks | 203 Core/owner/Browser cases and 28 database cases passed |
| Exact normal and invalid-ambient Core/native proofs | 9 cases passed in each clean-head run |
| Owned Shell compatibility | 17 native cases passed at clean exact head |
| Controlled Core/DNS/HTTP/Chromium compatibility | 8 real lifecycle cases passed; setup cleanup confirmed |
| Negative controls | Removed script identity and premature validation pruning caused 2 expected failures |
| Independent Astra Medium static review | Full review plus exact-head followup found no remaining blockers in combined scope |
| Lint/format/diff checks | Passed |
| Merge comparison | Tested and merged trees match |

The initial review corrected two issues: metadata admission contention must defer
claim rather than invalidate its action, and enabled assembly must verify approved
sources/admission before registration. Further regressions cover missing specs,
selection and action-hash tampering, strict authority/identity, decoded text byte
ceilings, and resource-free callback failure. One absolute deadline includes
validation/sealing/startup/compute. A canceled source-verification thread retains
its record until source FD and durable lease settlement. Unknown native/source/pin
cleanup preserves the fence; no partial successful output, PID-age/TTL inference
or automatic marker reconciliation is permitted.

Private ignored evidence remains under `runtime/`: normal Core
`core-proof-4zku2d19`, ambient `core-proof-4c814icb`, owner
`owner-proof-cb728969`, Browser `core-proof-kn5d4kwh`, exact/followup reviews,
negative-control logs, installed supplements, CI log and final gate JSON.
The deliberate diagnostic lost-ack fence is preserved. Installed byte hashes
are change detection, not independent provenance. Trusted operator/ancestors,
local filesystem/kernel, fixed installed sources, relevant process visibility
and a live owning event loop remain explicit assumptions.

Continue with full application/ASGI operator qualification; production flags remain
false, published v0.14.0 remains unchanged, and draft UI #61 stays parked. Computer,
Media and external adapters remain separate v0.15 slices; v0.16 channels -> v0.17
organizations -> v0.18 autonomy retains its order.
