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
The active work is v0.15 Shell boundary qualification after Browser operator
qualification PR #68 at `025d019`. Continue the established Core roadmap; later UI
rework requires fresh acceptance. Do not spend this phase polishing, deploying or acceptance-testing UI.

## Current scope: v0.15 Shell boundary qualification

Browser operator qualification PR #68 merged at `025d01971ab7cca92c0e9e8226fdd65128736cac`
from exact reviewed/tested head `c10725f48d31a84d8e3b7bb0d29bc9139b328827`.
Full CI passed 1,647 tests with two installed-Codex checks passed separately.
Focused tests passed 146 cases. Two real public-document tasks/readmission and
post-run image inspection passed with normal and deliberately invalid ambient
Docker/ZELO settings. Eight controlled fixture DNS/HTTP lifecycle cases passed.
Independent Astra Medium full static review plus exact-head follow-up found no
remaining blockers within combined scope. Merged and tested Git trees match.
See `docs/reviews/browser-operator-final-gates.md`.

Read `docs/v0.15-shell-boundary-qualification.md` before implementation. This
slice is a native isolation proof only; no Shell Worker registration, Core task,
API schema, authority profile or Agent/Mind route. Reuse mature installed Dash,
Bubblewrap and prlimit behind fixed Zelo-owned policy. Verify actual effective
non-root identity/limits, no host files/credentials/network, read-only guest,
zero process creation, bounded output, deadline/cancellation and cleanup. Only
harmless fixed host scripts with explicit opt-in; no fork bomb or generic host
shell fallback. Root/unavailable prerequisites fail closed. Offline CI never
launches these optional native probes. Preserve source/license/provenance limits.

Work in this dedicated worktree, use Astra Medium, and require focused tests,
actual host proof, independent exact-commit review and exact-commit full CI before
merging proof/runtime changes. Do not widen Browser/Python authority or reuse
Browser-specific contract fields as a misleading Shell identity. Qualify any
broader process/filesystem surface separately before Core binding.


The independent Shell review also found a preexisting PID-reuse defect in shared
native process cleanup. Fix the reused Core process helper and its OpenCode server
caller within this branch: keep the exact child identity reserved until owned group
termination is confirmed, refuse released/lost identities, and cover real lifecycle
and offline reuse cases. This necessary helper correction adds no task, approval,
worker authority, service or default activation. Broaden review/tests to affected
Python/Developer/OpenCode paths. Keep Shell restriction probes truthful: require
specific denial evidence, a successful permitted pipeline and byte-free file
creation, not arbitrary nonzero exits.

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
