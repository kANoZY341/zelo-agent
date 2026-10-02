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

## Current scope: v0.15 live conversation UI

Ahmad accepted the conversation redesign with refined angular Z mark on 2 October
2026 and explicitly authorized importing it and updating the delivery plan. Work
in this dedicated `feat/v0.15-live-conversation-ui` worktree based on verified main
`8d33d563070c58c487dd06e91fd9af617744dab3`. Do not edit or reset the source UI
worktree at `~/zelo/core`; it contains intentional uncommitted design work.

Read `docs/zelo-live-ui-delivery-plan.md` and `docs/design/zelo-ui-import-manifest.json`.
The next user-facing checkpoint is a real text-chat beta in the accepted UI.
It must not wait for Browser Worker, Company World, channels or v0.18 autonomy.

- Preserve the accepted charcoal/mint layout, centered conversation and angular Z.
- Use existing current-Core conversation/task types and server-side proxy routes.
- Keep credentials server-side. No new chat database, authority or approval system.
- Keep original Workspace usable as fallback; add the new live UI at `/talk`.
- Keep sample replies and simulated voice/approvals only in the labeled design route.
- New `/talk` must show actual Core conversations/history/tasks or truthful errors.
- Do not simulate live voice, uploads, approval execution or remote cancellation.
- No backend edits, production DB migration, service installation or public bind here.
- Use Astra Medium. One implementation owner in this worktree; no overlapping tests.
- Run Workspace tests/typecheck/build and scoped UI tests; report exact results.
- Do not push/merge as part of delegated implementation; the parent owns release gates.

Browser qualification is preserved separately on
`feat/v0.15-browser-worker-qualification`. The canonical v0.16/v0.17/v0.18
milestone order is unchanged; this is a parallel user-facing integration checkpoint.

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
