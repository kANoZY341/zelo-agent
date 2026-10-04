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
The active work is v0.15 Media package-reader helper/runtime closure design after PR86
merge b1638a0e7f074c8ca8a2bc47326e315d510a0820. Continue Core; UI needs fresh acceptance.
Do not polish, deploy or acceptance-test UI in this phase.

## Current scope: package-reader closure design; current-context regression fix

PR86 merged at b1638a0e7f074c8ca8a2bc47326e315d510a0820 with exact reviewed tree
53f8fcd719b51058c99081557dc0bb37453c80e7. Pre-merge CI passed 3,737 tests,
two skips; post-merge CI 37191575787 also succeeded. Acquisition is one accepted
batch: eight archives / 8,745,084 bytes, without package-member parsing.
Accepted proof SHA256 is
c7af88e91dc08c92b83f218c5553574f6a4df6ba89b42ce70010990e22771f51.
The earlier acquisition and local-identity review records remain authoritative
only for their exact documented source/evidence scopes.

The three-path local identity observation is consumed, 1/1. It observed dpkg
1.22.6ubuntu6.6 amd64 with no Source field. No installed inputs were reopened by
the following source research and no package tool was executed.

The separately reviewed public-source research now pins official Ubuntu metadata
and an applied source commit ba5fe18eb68084206981fb7b3a7c281eb2d38372. Fifteen
retained source files / 1,115,169 bytes passed a separate audit with 33 in-memory
tamper controls. The 16-request source budget is consumed, including one retained
HTTP404 and the reviewed correction to src/deb paths. Public identity discovery
used 11/12 logical operations, with gateway failures and a bounded tag-read
chunking deviation disclosed. Do not present these as native qualification proof.
Read docs/v0.15-media-package-reader-source.md,
docs/reviews/media-package-reader-source-review.md,
docs/reviews/media-package-reader-source-gates.json and
docs/v0.15-media-package-member-reader-design.md.

Source-host metadata, observed blob hashes and matching copyright do not prove
installed executable membership, a verified dsc signature, Git tree membership,
archive/tree equivalence, complete runtime/library closure or ABI compatibility.
Do not silently promote those unresolved claims. No downloaded source was built,
imported, executed or copied into product implementation.

Next: a bounded explicit closure proposal for missing initialization/options/
command helpers, the passthrough enum/build facts and a mature secondary tar reader.
Do not recursively fetch sources or repeat the spent 16-request corpus. Any new
research must have a reviewed literal manifest and retain its original failures.
The candidate design is NOT cleared for native launcher/parser/materializer
implementation or execution. It documents child-process ownership, hard decoder
memory/output limits, SIGPIPE-success refusal, temporary writes in information
commands, input-format and helper-execution risks that must be addressed first.

PR87's original documentation head f8dcbd9 failed full CI: renamed status heading
left ContextResolver's active source empty (1 failed, 3,736 passed, 2 skipped).
The correction restores the stable heading and aligns current roadmap facts,
with tests for nonempty current context and historical-section exclusion. This
is a documentation/test fix; application resolver/authority code stays unchanged.
The corrected head requires fresh review/full CI; the earlier failed run and
old documentation review cannot be claimed as matching passes. Preserve exactly
one `## Active increment:` heading in docs/zelo-project-status.md: it is a
machine-consumed interface. Keep decisive current facts within its 2200-character
context budget. The canonical v0.15 checkpoint must not contradict that status.

The interrupted Codex review is not a completed verdict. Continue with direct
ChatGPT tools and truthful review attribution; do not retry exhausted Codex jobs.
Retain source review, actual evidence, matching CI and exact-tree merge gates.
Retain all original scopes: origin4/4 and old-loader4/4 exhausted; index2/4,
ELF-version2/4, acquisition1/4 and local-identity1/1. No scope reset, proof repair,
overwrite or extra qualification attempt. Shared admission/ownership helpers and
all runtime/user data stay unchanged.

No package/member tool, Pillow/image decoder, native ctypes/filter, Docker/cgroup/
daemon/system/updater change, live config/data/model call/listener, Core/Mind/Agent/
route binding or enabled flag. Resource/filter gates still precede image decoding.
Continue v0.15 then canonical v0.16 -> v0.17 -> v0.18. UI #61 stays parked and
v0.14.0 unchanged. No release or later milestone completion is claimed.

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
