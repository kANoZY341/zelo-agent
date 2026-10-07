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
The active work is PR #93 finalization after four preserved synthetic
native-reader refusals. **All four authorized attempts are consumed. Do not create,
retry, reset, delete or reclassify another native-reader proof attempt.** Continue
Core; UI needs fresh acceptance.
Do not polish, deploy or acceptance-test UI in this phase.

## Current scope: PR93 failed native-reader qualification record

PR #93 remains an open draft. Current correction work aligns Docker CLI execution
between the successful build-only diagnostic and the full qualifier. The retained
runtime evidence lives under
`/home/zelo/zelo/core-v015-browser-renderer/runtime`; do not copy, repair, move,
delete or reset that evidence.

The four refused attempts are immutable:
- attempt 1 `native-reader-proof-uvjet91z`: lease-preflight refusal;
- attempt 2 `native-reader-proof-672ubxw7`: build refusal;
- attempt 3 `native-reader-proof-x7s79c20`: build refusal;
- attempt 4 `native-reader-proof-kvhyvgri`: build refusal.

Attempt-4 policy SHA256 is
`51a8532bf63d2f603d6e671815850aa86e7984b7040918e0e86fe04569a364b6`;
proof SHA256 is
`2c3bd1f7b130d3ce57bf317b5119971cdb30b68820b2e4f19503c1c0d14e1251`.
Its build exited2 after stderr reached20,851bytes and overflowed the bounded
capture. Cleanup found zero exact labelled containers/images and admission
marker/lock empty. No native success receipt or runtime authority exists.

The v2 append-only grant SHA256 is
`4c3dbda2ae77db193c296dbffb105b18f14d306541c6793675e04f0e600d1271`.
It authorized only attempt4 and is consumed. **No fifth attempt is authorized.**
Any future proof requires a new, separately reviewed explicit user budget decision.

The build-only diagnostic receipt SHA256 is
`ee68674ecd2ba41feab06676ca60fa4de619f8a6ee0cc09d2f1d59f4897a3411`.
It succeeded without touching the proof ledger. Fresh comparison proves the
diagnostic and qualifier use the same retained bundle
`0f53fb8375237850a0f3aacb850159ae9453e346f31c936ce85bbd6de2031e04`,
the same Dockerfile
`a097907d925d3c2ac2efe9984fc91fa83cbf7c9def98e973b5fb742e5a1b4df9`,
and the same network-none/pull-false build flags.

A concrete mismatch was found in the old execution environment: the diagnostic
supplied fixed PATH/HOME/LANG/DOCKER_CONFIG, while qualification supplied only
DOCKER_CONFIG before fixed GOMAXPROCS=2. The implementation now uses one exact
fail-closed Docker environment across readiness, diagnostic and qualification.
Treat this as a plausible cause, **not proven causation**. Do not run a new native
proof merely because the environment is corrected.

Current correction validation:52 focused environment/diagnostic tests,412
native-reader/recovery tests and2,855 Media/context tests passed; Ruff/diff checks
passed. Require fresh full CI and exact-head review before merging PR #93 as a
preserved failed qualification record. After #93, continue the agreed queue
#98 -> #99 -> #100. UI #61 remains parked. No acquired package, Pillow decode,
Core activation, release or deployment is cleared.

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