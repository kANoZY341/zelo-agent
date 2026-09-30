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

## Current scope: v0.14 Generic agent execution identity

Zelo Core v0.13.0 is published from
`adc43d856a29fdcacdba80a273566aefaa0b98f0`.

Merged v0.14 slices:

- registry foundation: PR #47, merge `8609ee583513f24d312785b7158a90d40a8458ef`;
- agent lifecycle and project binding: PR #48, merge
  `8259141834869c09241dc22ea1fb5cbf89615565`;
- bounded parent-child delegation: PR #49, merge
  `d787c9cc86ea8ad680d2981e8d18b1e6b73d18a4`.

The active branch is:

`feat/v0.14-agent-execution-identity`

This fourth v0.14 slice binds the existing Coding and Research execution paths
to exact persisted Agent Factory identity without creating a parallel task
system.

Rules for this slice:

- create specialist work only through trusted active/current AgentSpec instances;
- support only the existing `developer_run` and `research_run` Core task kinds;
- persist exact agent spec, registry, Project and optional delegation snapshots
  on the Core task;
- include the agent/delegation execution binding in task request fingerprints and
  immutable action hashes;
- revalidate bound agent identity at approval, retry and claim boundaries;
- require delegated work to keep the exact active delegation, Campaign and
  reserved parent-owned budget proof;
- derive Coding worker capability requirements from trusted AgentSpec metadata;
- preserve Coding's explicit independent-review requirement and fail closed when
  no eligible independent reviewer exists;
- keep Research on the existing bounded read-only retrieval runtime;
- preserve ordinary unbound Core task behavior exactly;
- do not add new task kinds, automatic child creation, recursive/model-authored
  subagents, new tools, worker authority, automatic memory writes or
  approval/budget/scheduler/promotion/release bypasses;
- run diff check, Ruff lint, Ruff format and the full pytest suite;
- require independent/adversarial review and exact-head GitHub CI before merge.

After this slice, v0.14 first adds organizational role/unit/reporting-assignment
contracts. Only after that may it add hierarchy-aware bounded child-agent creation,
then activation/evaluation gates and release preparation.

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
