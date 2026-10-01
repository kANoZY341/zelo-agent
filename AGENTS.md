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

## Current scope: v0.14 Release preparation

Zelo Core v0.13.0 is the latest published release from
`adc43d856a29fdcacdba80a273566aefaa0b98f0`.

The complete v0.14 Agent Factory implementation is merged on `main` through
PRs #47-#53 at merge commit
`927f63b55babeb8eca8f6b9a67d59d49241b3088`:

- registry foundation: PR #47;
- agent lifecycle and project binding: PR #48;
- bounded parent-child delegation: PR #49;
- generic Coding/Research execution identity: PR #50;
- durable organizational hierarchy: PR #51;
- hierarchy-aware permanent child creation: PR #52;
- deterministic activation/evaluation gates: PR #53, exact implementation head
  `4e23c8ad49f2132cf27f12a5a11062dfd61f17a0`.

The release-preparation branch is:

`chore/v0.14-release-prep`

This branch is release preparation only for v0.14.0.

Rules for this slice:

- bump and verify public Core version metadata for `0.14.0`;
- align README, live status and canonical roadmap with the completed v0.14
  implementation;
- preserve Managing Director -> Zelo CEO -> VP -> Director -> Manager ->
  Specialist -> Worker -> Tool;
- preserve the separation between durable organizational reporting and temporary
  task delegation;
- preserve existing task, tool, worker, approval, scheduler, budget, promotion,
  merge and release authority boundaries;
- do not add features, migrations, dependencies, new AgentSpecs or v0.15
  capabilities from this branch;
- do not weaken fail-closed lifecycle, delegation, hierarchy, execution-identity
  or activation checks;
- keep this branch based on the merged PR #53 `main` baseline;
- run diff check, Ruff lint, Ruff format and the full pytest suite from a real
  repository clone;
- require independent/adversarial review and exact-head GitHub CI before merge;
- after a clean release-prep merge, tag and publish `v0.14.0` from the resulting
  `main` commit;
- do not begin v0.15 capability/skills implementation until v0.14.0 is published.

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
