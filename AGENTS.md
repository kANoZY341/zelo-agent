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
The active work is v0.15 Media pure syscall-policy qualification after immutable
file-image PR #77 at `3381ada6`. Continue Core; UI needs fresh acceptance.
Do not polish, deploy or acceptance-test UI in this phase.

## Current scope: v0.15 Media pure syscall candidate policy

PR #77 merged at3381ada6 with matching reviewed/tested tree, fullCI2,596 passes,
two supplemented skips,121newimage/launcher tests and13native scanner operations.
Read docs/reviews/media-image-final-gates.md and docs/v0.15-media-syscall-policy.md.
Astra Medium design review recommends a pure fixed policy first; it grants no native
installer/decoder execution. Implement qualification/media_syscall_policy.py with
immutable candidate bytes/digest, exact current-policy validation and a fixed
stdlib-only syscall tuple/rationales from authenticated original Moby unconditional
allow rules. Preserve vendor LICENSE/NOTICE and Browser additions/helpers unchanged.
No caller policy/path/ABI/action/flags/syscall/compiler/FD/environment selection.
The canonical artifact expresses fixed x86-64/defaultEPERM/unsupportedABI-kill
intentions and exact readonly-open/no-new-executable-memory argument restrictions;
no compilation, installation, enforcement receipt or decoder-ready claim follows.
Fresh bounded vendor reads before parsing and on every validation; strictbytes and
canonical identity, no shared mutable cache or constructor true flags.

Add adversarial offline fixtures and guarded fresh-interpreter import audit. Astra
Medium full static source/evidence review, focused checks, matching-head fullCI and
standalone supplements precede merge. Record post-merge image CI separately.
Written installer contract must preserve exact libseccomp/platform/loader/dependency
identity and ABI, x32 handling, single-thread installation, compiled policy evidence,
causally sensitive safe probes, unchanged owned-process/image lifetimes and uncertain
cleanup fencing. This phase implements none of those native stages.

Keep original PNG RLIMIT/cgroup/seccomp gate BEFORE decoder qualification. No Pillow
install/import/native member load/decoder, ctypes/libseccomp execution/filter load,
new process guard probe, dependency installation, Media runtime/task/route/registry/
capability/Core/Agent/Mind binding, model request, signing/OAuth, live environment/
data/config/service/listener or host/daemon/cgroup change. No aggregate-resource or
runtime acceptance is granted. Production flags stay disabled, v0.14.0/UI61 preserved.
Use only the dedicated worktree, retain bounded private evidence, delete no unrelated
or user data. Computer/external adapters and v16->v17->v18 stay separate and ordered.

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
