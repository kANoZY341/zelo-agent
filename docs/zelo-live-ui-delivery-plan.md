# Zelo live UI delivery plan

## Decision — 2 October 2026

Ahmad approved importing the accepted conversation redesign, including the refined
angular Z mark. The first useful delivery is live text chat and durable history,
not another visual prototype. Future visual refinement remains possible.

Base: verified main `8d33d56` (API Worker PR #60 merged).
Integration branch: `feat/v0.15-live-conversation-ui`.
Source: preserved `feat/zelo-definitive-ui-prototype` worktree; selected files are
recorded with SHA-256 provenance in `docs/design/zelo-ui-import-manifest.json`.
Only presentation is imported. The old v0.12 backend, local databases, secrets,
node_modules, caches and rejected concepts are not imported.

## When Ahmad can use it

The first-use gate is **U2 — live text beta**, immediately after import and live
Core integration pass validation. It does not depend on finishing Browser Worker,
Company World, all of v0.15, or v0.18. The first local-use target is the new `/talk` route; the normal release still
requires review, exact-head CI and a safe connection to the intended Core database.

| Gate | Deliverable and acceptance | Current status |
| --- | --- | --- |
| U1 | Accepted UI selectively imported onto current main; source preserved | Complete: accepted source preserved, scoped import verified |
| U2 — first use | `/talk`: real reply, saved/reopened history, reload persistence, task/result projection, truthful connection/error states, no mock execution | Implemented; 27 host tests/typecheck/build pass; local beta on port 3001, final acceptance/review/CI pending |
| U3 | Verified voice capture, transcription, synthesis and interruption in the same conversation; explicit unavailable state otherwise | After U2 |
| U4 | Real approval operations, additional control views and Company World over canonical Core state | Separate reviewed increments |

U2 release checks: idempotent submission/retry; stale response protection when
switching threads; bounded history loading; refresh resumes the selected Core
conversation; pending tasks update without fabricated success; token never enters
browser responses or storage; responsive/keyboard checks; Workspace tests,
typecheck and production build; independent review and exact-head Workspace CI.
A local integration preview and a merged/deployed release are distinct milestones.

## Safe rollout

Preserve the existing Workspace at `/` as fallback while `/talk` is evaluated.
Keep visual fixtures at the explicitly labeled design route only. Voice, upload
and approval controls that are not wired must be disabled with plain explanations.
Start any test services on loopback with isolated test data; do not migrate or
replace the user's existing SQLite database merely to demonstrate the UI.
Production startup requires selecting current-Core worktree/configuration,
confirming a backup and migration state, then a real conversation smoke check.

## Parallel roadmap

Continue bounded v0.15 worker qualification separately. This UI checkpoint does
not change Zelo's company hierarchy or reorder v0.16 channels, v0.17 specialist
organizations, or v0.18 advanced autonomy. UI work no longer waits behind those.

## Implementation handoff — 2 October 2026

`/talk` now adapts the accepted presentation to the existing conversation proxies.
The original Workspace remains intact with a small beta link. Live task details
and bounded history use Core projections; unavailable voice, attachment and
approval actions are disabled. No Python or proxy-route changes were made.

Core currently provides idempotency for turns only. The beta serializes creation,
blocks automatic retries after uncertain creation, and offers history recovery.
This conservative recovery is the first-beta behavior, not full backend create
idempotency. A future Core contract enhancement remains separate. Turn retry
identity is retained in memory only,
so refreshing during an uncertain send requires inspecting Core history.

See `zelo-live-ui-implementation-report.md` for exact checks and remaining gates.
No deployment, merge or release is implied by this handoff.

## Parent host checkpoint

The production build serves `/talk` on loopback port 3001, backed by current Core
on loopback port 8001 with a NEW isolated `runtime/live-beta` database. Existing
user conversations/databases and the original design worktree were not migrated
or overwritten. This is an opt-in local beta, not the production rollout.

Host Workspace tests: 27 passed, 0 failed; typecheck and production build pass.
A new history-abort race regression was observed failing before the fix and passes
after history-generation/loading cleanup. Browser keyboard testing also identified
Escape being consumed by the focused history search box; the live dialog now
handles Escape explicitly, with component regression coverage.

A real-model end-to-end turn is still unverified: the automatic authenticated
smoke action was rejected before execution and was not retried through another
route. Health and frontend contract tests must not be described as that proof.
The final user-data rollout requires backup/migration review and a real saved turn.

Browser validation now passes on the actual production build: desktop/mobile
rendering, actual Core health/list connection, and zero browser errors. An
intercepted-API fixture context separately verifies send, one idempotency key,
reply rendering, reload without replay, scoped history search and Escape. These
fixture checks do not establish a real-model end-to-end turn.
