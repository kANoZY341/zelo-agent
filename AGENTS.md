# Zelo Agent development rules

## Scope and safety

- Work only inside `~/zelo/core` (the repository root). Ask the user before
  modifying anything outside it, using sudo, changing system configuration, or
  installing system-wide services.
- Bind development services to `127.0.0.1`. Ask before exposing services publicly,
  binding to other interfaces, or changing firewall/network settings.
- Never delete user data or repositories without explicit approval. Preserve
  `workspaces/`, `memory/`, and `secrets/` and their contents.
- Never commit API keys, tokens, credentials, or other secrets. Keep local secrets
  in ignored files; examples must contain placeholders only. Avoid logging secrets.
- Do not commit or push changes unless the user asks.

## Architecture

- Keep a modular architecture with replaceable, loosely coupled components.
- Avoid vendor lock-in. Future LLM integrations must go through a model-router
  abstraction so providers can be swapped without rewriting agents.
- Agents and workers must not receive unrestricted system access by default. Grant narrowly
  scoped capabilities and use the existing isolated sandbox/workspace boundaries for execution.
- Future sensitive, external, or destructive agent actions must pass through an
  explicit approval layer before execution.
- Keep a clear audit trail of meaningful actions and approvals where practical,
  without recording credentials or unnecessary private data.
- Do not over-engineer early versions or implement future components speculatively.
  Add orchestration, agents, storage, authentication, and other subsystems only
  when their scope is explicitly requested.

## Implementation and verification

- Write maintainable, typed Python with small functions and clear boundaries.
- Keep application assembly in `app/main.py`, HTTP routes in `app/api.py`, and
  environment-backed settings in `app/config.py`.
- Keep runtime dependencies minimal and separate test dependencies.
- Add automated tests for important behavior and regressions. Run relevant tests
  after changes; verify service changes locally and stop temporary servers.
- Update the README and credential-free `.env.example` when usage or settings
  change. Explain material decisions, test results, and unresolved errors.

## Canonical roadmap

- Treat `docs/zelo-canonical-architecture-roadmap.md` as the project north star
  and `docs/zelo-workspace-decision.md` as the current workspace decision.
  Feature designs may refine the active milestone but must not silently redefine
  Zelo's identity or reorder future milestones.
- Zelo is the persistent personal AI and Group CEO under the human Managing
  Director. Zelo Mind is the reasoning/conversation identity; Zelo Core is the
  trusted authority and execution boundary. Factory-managed authority descends
  VP -> Director -> Manager -> Specialist. Models think, agents specialize,
  workers work, tools act, and Zelo decides.
- Map newly discovered frameworks/repositories to the canonical architecture
  before integrating them. Do not reshape Zelo around a third-party project.
- Reuse before rebuild: inspect mature open-source implementations first, verify
  license/provenance/security, and prefer narrow adapters or focused vendoring.
  Borrowed code must never bypass Zelo-owned permissions, approvals, secrets,
  routing, audit, memory, validation, or promotion boundaries.

## Latest priority — Core first; UI parked (2 October 2026)

Ahmad reports that the UI still needs major work and directs Core development to
continue now, with UI redesign/integration revisited at the end. This supersedes
the earlier U2-first delivery plan and provisional design acceptance.

Preserve UI PR #61 as a draft and do not merge it automatically, even if CI passes.
Preserve `feat/v0.15-live-conversation-ui` at `95f35cf` and the independent original
design worktree. Do not delete UI code, chats, backups or local beta data.
The active work is v0.15 Shell identity/admission/owner qualification after
Shell boundary PR #69 at `a33dc9c`. Continue the established Core roadmap; later UI
rework requires fresh acceptance. Do not spend this phase polishing, deploying or acceptance-testing UI.

## Current scope: v0.15 Shell identity/admission/owner qualification

Shell boundary PR #69 merged at `a33dc9cdf43662ce9c158ad4cba0b51bdb97735d`
from reviewed/tested head `32f843a3adef9f6b89d0b05aab6bf7f1ecb50dbc`.
Full CI passed 1,686 tests with two installed-Codex checks passed separately;
132 affected focused cases passed. Normal and invalid ambient native proofs each
passed fourteen cases and reaped fourteen launchers. Negative controls detected
incidental restriction failures and missing effective process limits before fork.
Independent Astra Medium full review and correction follow-ups found no remaining
blockers in combined scope. Merged/tested Git trees match. See
`docs/reviews/shell-boundary-final-gates.md`.

Read `docs/v0.15-shell-owner-qualification.md` before implementation. Qualify a
separate immutable Shell identity and durable capacity-one admission/one-shot
resource owner. Reuse existing admission and policy-neutral lifecycle mechanics;
retain strict Browser policy validation and do not reuse Browser contract fields
as Shell identity. Bind execution to the exact frozen identity. Admission precedes
verification/pins/spawn and uses one trusted private persistent directory.

Add explicit per-execution native ownership/cleanup observation before spawn;
creation-pending is not no-resource proof. Require child/group termination/reap,
pipe/task closure and sealed-pin closure before successful publication/release.
Keep cancellation/cleanup tasks retained through grace expiry; uncertain cleanup
or owner death leaves a durable fence. No error-string/global-registry inference,
TTL, stale-PID recovery, automatic marker clearing or unrelated resource deletion.
Cover shared Python/Developer/OpenCode and Browser owner compatibility if reused
helpers change. Only harmless fixed opt-in native scripts; no fork bombs, broader
process budget, host fallback, system installs, services or user data.

This is qualification only: no Shell task/schema/API/registry/capability/authority,
Agent/Mind route, configuration activation or Core startup import of qualification.
Work in this dedicated worktree using Astra Medium. Require focused tests, actual
host proof, independent exact-head review and exact-head full CI before merging
runtime changes. Preserve source/provenance/license and host-trust limits.

Production Browser Worker remains disabled. Do not modify live settings/databases,
install services, expose listeners, publish a release or merge parked UI PR #61.
Keep v0.14.0, user chats, UI worktrees and hierarchy/milestone ordering intact.
Unknown cleanup stays fenced; no TTL, stale-PID inference, automatic marker
reconciliation or unrelated resource deletion. v0.16 channels -> v0.17 specialist
organizations -> v0.18 learning/healing/autonomy remains the roadmap order.

## Workspace decision

- The current workspace product direction is one Zelo experience with three
  connected views: Talk to Zelo, Company World, and Control Desk.
- The proof gate is complete. The selected direction is a Zelo-owned workspace
  with Mission-Control-derived Control Desk UX/components and a later
  Agent-Town-derived Company World.
- Mission Control and Agent Town are MIT-licensed reference/reuse sources, not
  Zelo's runtime or authority boundary. Preserve required attribution for copied
  or materially adapted code.
- UI code must remain a projection/adapter over canonical Core state. It must not
  create a parallel task, approval, scheduler, memory, or authority system.
