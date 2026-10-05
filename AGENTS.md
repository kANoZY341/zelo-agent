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

## Latest priority — Core first; UI parked (2 October 2026)

Ahmad reports that the UI still needs major work and directs Core development to
continue now, with UI redesign/integration revisited at the end. This supersedes
the earlier U2-first delivery plan and provisional design acceptance.

Preserve UI PR #61 as a draft and do not merge it automatically, even if CI passes.
Preserve `feat/v0.15-live-conversation-ui` at `95f35cf` and the independent original
design worktree. Do not delete UI code, chats, backups or local beta data.
The active work is v0.15 Media native-reader admission reconciliation after
PR93 attempt1 refusal. Continue Core; UI needs fresh acceptance.
Do not polish, deploy or acceptance-test UI in this phase.

## Current scope: native-reader reconciliation; attempt 1/2 permanently refused

PR92 merged at f05abe11cd6b659a8339132028ca710e16393c48. PR93 corrected
head c6127669cda7923c851febe1b4f8530402f58ef8/tree
d6eaa8a1fa029cd049384f66d27de5218ba7c8cd passed matching CI37231757784:
4,569 tests/two skips. The one source-cleared synthetic native-reader run REFUSED
at lease-preflight before image build, setup create, dpkg/loader execution, package
matrix or cgroup evidence. Native-reader attempt1/2 is permanently consumed.

Retain attempt native-reader-proof-uvjet91z unchanged. Policy SHA
a566a81238d144163e82ce91d728067c8afe36011fea404594f03f200ac868c3;
refusal proof SHA027e7165fc21001eafb19cdb0a9c89bb0315012931c0a862ad742e5883655378;
marker SHA f88573e617e66156815491696f8a51fed078e07df85658800cc1cb259bb34472;
failed lease9dd76284f54fe91361c4646fafc97a78c98f5179437345d54f9c815b8ea8bd0f.
Do not rerun, delete, rewrite, repair or reset that evidence. No success receipt exists.

Admission marker is intentionally fenced. Later read-only exact-lease discovery
found zero matching Docker containers/images and the admission lock free; these
facts do NOT retroactively prove cleanup or success. An unrelated Aegis Docker
build overlapped the failure window; record it only as a material confounder, not
proven causation.

Current uncommitted recovery work is a separate reconciliation gate. It must:
authenticate the exact failed fixtures/evidence; take the admission lock exclusively;
require zero exact lease resources; observe a global two-second Docker event-silence
window with NO label filter; repeat zero-discovery/proof/marker checks; write+fsync
private pending reconciliation evidence; fsync its parent directory; only then
truncate+fsync the exact marker while still holding the lock. Release/unlock precedes
the in-memory reconciliation receipt. Any ambiguity refuses without retry/reset.
Recovery/operator validation currently passes211 focused tests and2790 combined
Media/context tests; Ruff/format/diff checks pass.

The final-attempt operator is hardened separately: historical attempt1 is recognized
only by exact hashes; the two-attempt ceiling is order-independent; Docker server
must be Linux/amd64 with an available valid runc component and the same global
event-silence window BEFORE admission/proof creation. The container contract fixes
runtime selection to runc; do not infer Docker's ambient default runtime. Do not
run attempt2 yet.

After reconciliation passes its own frozen review/full CI and one audited
reconciliation, integrate the THEN-CURRENT main into the Media branch before any
attempt2. Preserve historical evidence, resolve status/test conflicts, rerun focused
and full CI, freeze a new exact tree and review it. Only that integrated source may
seek clearance for the sole remaining attempt2/2.

Original scopes remain preserved: origin4/4 and old-loader4/4 exhausted; index2/4;
ELF-version3/4; acquisition1/4; dpkg identity1/1; source16/16; helpers8/8+directory;
tarfile observations consumed; USTAR isolation1/2; native reader1/2 refused.
No acquired .deb, Pillow, Core route, enabled flag, system/config/user-data change,
proof reset or model/Codex job. Preserve one active status heading <=2200 chars,
parked UI #61/worktrees and v0.14.0. Finish v0.15, then v0.16 -> v0.17 -> v0.18.

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
