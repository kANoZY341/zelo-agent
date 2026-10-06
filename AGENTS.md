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
- Keep reusable application assembly in `app/application.py`, the normal ASGI
  entry point in `app/main.py`, HTTP routes in `app/api.py`, and environment-backed
  settings in `app/config.py`. Explicitly configured factory imports must not
  instantiate default Settings or parse live environment/.env.
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
- Persistent factory-managed Agents are durable scoped identities: instructions,
  memory, skills, Space grants, tool grants and authority belong to the Agent
  package. Execution-capable Agents may own a persistent per-AgentInstance
  computer; computers never imply authority and must never fall back to the host.
- CopilotKit/OpenDots and CopilotKit/OpenBot are approved reference/reuse sources
  for the persistent-computer/Computer-Panel direction only. Pin reviewed
  revisions, preserve MIT notices, fail closed on upstream contract drift, and
  keep Zelo Core/Supervisor authoritative.
- Skill learning may propose versioned procedures from evidence/outcomes, but it
  may not silently widen capabilities, tools, Spaces, budgets or approvals.

## Latest priority — Core first; UI parked (2 October 2026)

Ahmad reports that the UI still needs major work and directs Core development to
continue now, with UI redesign/integration revisited at the end. This supersedes
the earlier U2-first delivery plan and provisional design acceptance.

Preserve UI PR #61 as a draft and do not merge it automatically, even if CI passes.
Preserve `feat/v0.15-live-conversation-ui` at `95f35cf` and the independent original
design worktree. Do not delete UI code, chats, backups or local beta data.
The active work is PR93's native-reader runtime-scope binding gate after the
authorized attempt-1 reconciliation. Attempt 1 remains refused and consumed;
attempt 2/2 is not cleared. Continue Core; UI needs fresh acceptance.
Do not polish, deploy or acceptance-test UI in this phase.

## Current scope: PR93 native-reader runtime-scope gate

PR93 remains an open draft against main. The runtime-scope implementation
baseline is commit `1f312c90a7d39c21d1b4bace9317352b899da313` (tree
`02af4737c230ea440f03e2efcb4f0183b2096570`), based on the integration of PR93
`bcf6c1a` with main `b67d0d8`. Exact-head CI run
`37395345867` passed 4,688 tests (two skipped, 11 warnings) on that baseline.
Later PR updates must be checked against their actual branch head and matching CI;
this historical run is not evidence for a newer head.

Native-reader attempt 1/2 remains permanently refused and consumed. Its private
policy and refusal proof hashes remain
`a566a81238d144163e82ce91d728067c8afe36011fea404594f03f200ac868c3` and
`027e7165fc21001eafb19cdb0a9c89bb0315012931c0a862ad742e5883655378`.
Reconciliation completed at source head `bcf6c1a`; durable evidence SHA256 is
`04fc0c4607922f24c3aea63fa5bd72a27334ae652174d4b51f12e039d155dad1`. It cleared
the admission marker only after writing that evidence; attempt-1 proof was not
changed, deleted or reset. No native attempt 2/2 has run.

The retained runtime evidence and reconciliation record currently live under the
old checkout `/home/zelo/zelo/core-v015-browser-renderer/runtime`, outside this
repository root. The integrated PR93 worktree does not contain that runtime
history. The operator must receive the same explicit persistent `--runtime-root`
from every checkout and validate its one-time path-bound scope record, exact
attempt-1 evidence, and reconciliation record before readiness or attempt
creation. A missing, copied-to-another-path, replayed or mismatched scope refuses.
Do not copy, repair, delete or reset consumed evidence.

The one-time binding command writes only a new private
`media-native-reader-scope.json` beside the retained runtime state. Because the
current runtime path is outside `~/zelo/core`, do not run that binding command
without the user’s approval required by this file. Native attempt 2/2 also needs
fresh exact-head CI/source review and separate explicit user authority. Any
failure after the final attempt directory is created consumes 2/2 and stops.
No Docker/native qualification, package acquisition, Core activation, merge or
deployment is cleared.

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

[executed on device: Zelo (e9a33897-63c0-4476-b309-eacb1dd2b7db)]