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
The active work is v0.15 Browser operator qualification after Core task
binding PR #67 at `bc8404e`. Continue the established Core roadmap; later UI
rework requires fresh acceptance. Do not spend this phase polishing, deploying or acceptance-testing UI.

## Current scope: v0.15 Browser operator qualification

Core binding PR #67 merged at `bc8404e3257573701683448255311d79f3d27cdf`.
Its tree equals reviewed/tested head `0486e15153adaf5983d5341a69cdbf01390ec93e`:
exact-commit CI passed 1,617 tests with two installed-Codex skips; both passed in
an exact-commit standalone clone. Core/capability tests passed 90 cases; all eight
real controlled Core/Chromium/container cases passed with fixture HTTP/DNS and
no setup cleanup errors. Astra Medium full static review plus exact-commit
follow-ups found no remaining blocking defect within combined static scope.
See `docs/reviews/browser-core-binding-final-gates.md`.

Read `docs/v0.15-browser-operator-qualification.md` before implementation. Qualify
actual DNS/TLS/document fetch through the default adapter in an isolated temporary
Core instance, with one fixed public documentation origin/path and explicit host
opt-in. Reuse the qualified preparation command, receipt loader, task service,
Runner, renderer and admission boundary. Separate real public-network evidence
from the preceding fixture HTTP/DNS proof. Offline CI must not contact websites
or run Docker; test the opt-in guard and deterministic harness behavior instead.

Production Browser Worker remains disabled. Do not modify live settings/databases,
install services, expose network listeners, publish a release, add a default
Browser Agent or Mind route, or merge parked UI PR #61. Keep v0.14.0, user chats,
UI worktrees and the canonical Agent hierarchy/milestone ordering intact.

Preserve narrow endpoint/path/timeout task inputs, exact identity and versioned
retry/claim/Runner checks, native sandbox, no-network Chromium, nonroot/NNP,
cap-drop ALL, cgroups and cleanup-gated results. No CDP/evaluation/navigation/
interaction, custom flags/headers/cookies/credentials, screenshots or downloads.
No privileged/no-sandbox fallback. Unknown cleanup stays fenced; no TTL, stale-PID
inference, automatic marker reconciliation or unrelated resource deletion.

Work in this dedicated worktree and use Astra Medium. Require focused tests,
controlled host proof, independent exact-commit review and exact-commit full CI
before merging runtime/harness changes. Clearly distinguish static, fixture and
actual host/public-network evidence. Operator receipts are structural configuration,
not signatures or independent image provenance. Source artifacts, same-UID
operators, filesystem ancestors and Docker remain trusted.

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
