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
The active work is v0.15 qualified Shell Core binding after identity/owner
qualification PR #70 at `befaefa`. Continue the established Core roadmap; later UI
rework requires fresh acceptance. Do not spend this phase polishing, deploying or acceptance-testing UI.

## Current scope: v0.15 qualified Shell Core binding

Shell identity/admission/owner PR #70 merged at
`befaefa6d2460b4bbaeed46b0e764d06c8c6802c` from reviewed/tested head
`132b4456213710657b60ba21b0bd2000b62a1416`. Full CI passed 1,729 tests;
both installed-Codex supplements passed separately. 218 affected focused tests,
seventeen owned native cases under normal and invalid ambient settings, fourteen
direct Shell cases and eight real Browser lifecycle cases passed. The pin-evidence
negative control detected discarded uncertainty. Independent Astra Medium full
review and exact-head followup found no remaining blockers in combined scope;
merged/tested Git trees match. See `docs/reviews/shell-owner-final-gates.md`.

Read `docs/v0.15-shell-core-binding.md` before implementation. Bind only the
qualified fixed sealed Dash/prlimit/Bubblewrap built-in compute lane to Core.
Payload is bounded script text only; no stdin data stream, executable/flags/env/
cwd/mount/path/source/runtime/admission selection or broader process budget.
Preserve no workspace, network, credentials, writes and process creation, exact
resource/output ceilings, nonroot identity and one absolute deadline.

Add distinct Shell role/capability/skill/authority and strict persisted execution
specification, bound to trusted worker selection, policy and operator/runtime
identity. Reuse existing action hashing, approvals, budgets, scheduler, audit,
Agent WorkerRequirements and migration/trigger conventions. Submission/retry,
claim and Runner revalidate exact identity; missing/stale/tampered tasks fail
closed, never silently rebind. Source preparation is outside writer transactions;
actual execution rechecks/seals approved bytes. Output remains untrusted text.

Default disabled: no default AgentSpec or Mind route, live configuration change,
service or release. Reuse qualified owner/admission helpers without browser or
Python authority relabelling. One trusted private persistent admission directory
at assembly, not per task. Only explicit cleanup permits publication/pruning;
creation pending/grace expiry retains ownership, unknown cleanup/crash preserves
the fence. No automatic reconciliation, PID-age/TTL inference or host fallback.

Use this dedicated worktree and Astra Medium. Require focused tests, real opt-in
Core/native TaskService+Runner lifecycle proof, independent exact-head full review
and exact-head CI plus installed-Codex supplements before merging runtime changes.
Keep provenance/license/host visibility/operator assumptions explicit. Test reused
Browser/Python/Developer/OpenCode paths if affected. No system installs, sudo,
public listeners, fork bombs, production data or credential/model requests in
native fixtures. Keep the broader process/filesystem surface separate.

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
