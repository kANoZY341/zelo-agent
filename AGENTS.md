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
The active work is v0.15 Media pure package-index selection after PR83 merge
8b34bd66a1178b8c1f78141dc11ab93b9a9ac30a. Continue Core; UI needs fresh acceptance.
Do not polish, deploy or acceptance-test UI in this phase.

## Current scope: v0.15 Media pure package-index selection

PR83 matched its reviewed tree, passed3416 CI tests and18actual origin cases.
Read docs/reviews/media-origin-final-{review,gates}.md. Origin3/4, ELFversion2/4,
oldloader4/4 attempts are retained; no further origin/ELF run or quota reset.
Historical selected-root Release authenticity and signed index declarations only.
No package bytes or installed-member origin, freshness or current key validity.

Astra Medium cleared the complete frozen proposal docs/v0.15-media-package-index.md
for pure offline implementation; see docs/reviews/media-index-parser-design-review.md.
Implement the bounded incremental selector for exactly eight pinned amd64 packages,
strict metadata syntax, normalized Source, and complete three-suite conflict checks.
No file/network/process I/O in this module. Only synthetic input tests; no real
index retrieval, decompression or package/member inspection before separate gates.
No code from the inspected GPL python-debian reference is incorporated/imported.
No dependency/version resolver or maintainer-script/package execution.

Source review, focused/adversarial boundary tests, causal faults, matching-head
full CI and standalone supplements precede normal exact-tree merge. Metadata-only
is true; package_member_origin_verified and runtime_authority remain false.
Preserve all previous origin proofs, source/actual bindings and admission lifecycle.
No changes to shared ownership helpers or existing native qualifiers.

Platform notices/import/frozen/builtin/dlopen/search/ABI closure, materializer/
immutable context, build/daemon settlement, resources/startup/installer/filter and
decoder remain separate. Original resource/filter gates precede decoder.

No Pillow/decoder, ctypes/libseccomp/filter execution, Docker build/create, cgroup/
host/daemon changes, runtime/task/route/registry/capability/Core/Agent/Mind binding,
model requests, signing/OAuth or live env/data/config/listener changes. Flags stay
disabled, v0.14.0/UI61 preserved. Dedicated worktree only, no unrelated/user-data
deletion. Continue v0.15 then v0.16 -> v0.17 -> v0.18.

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
