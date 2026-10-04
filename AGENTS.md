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
The active work is the v0.15 offline Media member contract after PR87
merge 63d2aaea451c4639f117efbc9bd1c6e65ecacb44. Continue Core; UI needs fresh acceptance.
Do not polish, deploy or acceptance-test UI in this phase.

## Current scope: offline member contract; native reader still blocked

PR87 is merged at 63d2aaea451c4639f117efbc9bd1c6e65ecacb44, tree
c14328d55701812d5c2ca0fb41dc4474c5b58d3c matching reviewed source. Corrected-head
CI37193229146 passed 3,740 tests with two skips. The earlier f8dcbd9 failure remains
archived, not relabeled a pass. Preserve exactly one `## Active increment:` heading
and <=2200 decisive current characters; it is consumed by ContextResolver.

Current branch feat/v0.15-media-member-contract adds only the pure validation
boundary in qualification/media_member_contract.py, synthetic tests and docs.
Read docs/v0.15-media-member-contract.md. Direct design review clears only this
metadata contract, not a tar/ar/native parser, launcher, extractor or materializer.
It accepts bounded JSON and separate caller-owned binding/outcome dataclasses;
validated output means metadata consistency, never authentication or authority.
No app, routes, shared admission/ownership helpers, dependencies or flags change.

The six in-memory guard-bypass controls bind the exact module source SHA; all
181 synthetic contract cases, 284 focused checks and 2,101 Media checks passed.
Fresh source review, matching full CI and exact-tree merge remain new-PR gates.
Never substitute PR87 CI for this increment. Do not weaken old failed tests.

Read docs/reviews/media-reader-helper-review.md and its static gate JSON. The
new eight-file helper scope (31,201 bytes, eight requests plus one directory read)
is consumed; no retry or recursive source discovery. DPKG_TAR_PASSTHROUGH=0 closes
that enum question, not binary/build/runtime provenance or complete initialization.
Locale/debug/report/error descendants and exact installed libraries remain open.
Python tarfile API docs were read but two source fetches failed; its patched
source, extension preallocation and termination semantics remain unqualified.
The candidate is NOT approved for import/parsing or native execution. A further
source request needs a distinct bounded, explicit plan, not a quota reset.

PR86 remains the single accepted archive acquisition: eight files / 8,745,084
bytes; proof SHA c7af88e91dc08c92b83f218c5553574f6a4df6ba89b42ce70010990e22771f51.
Original source research retains 15 files /1,115,169 bytes from dpkg commit
ba5fe18eb68084206981fb7b3a7c281eb2d38372. Preserve its failed404, all records and
conditional official-host attribution. dsc signatures, Git/source-archive/binary
membership and full ABI/import closure are not established by these observations.

Do not repeat exhausted Codex jobs. Reviews here are direct ChatGPT work, not an
independent Codex execution. Retain source/actual/CI/exact-merge gates honestly.
Original scopes stay: origin4/4 and old-loader4/4 exhausted; index2/4,
ELF-version2/4, acquisition1/4, local-identity1/1; original source corpus16/16.
No proof reset, repair, overwrite, extra installed-file read or native attempt.

No package/member tool, Pillow/image decoder, ctypes/filter, Docker/cgroup/daemon/
system/updater change, live config/data/model call/listener, Core/Mind/Agent/route
binding or enabled flag is cleared. Resource/filter gates still precede decoding.
Continue v0.15 then v0.16 -> v0.17 -> v0.18 in canonical order. UI #61 and both UI
worktrees, user data and v0.14.0 remain untouched. No v0.15 release is claimed.

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
