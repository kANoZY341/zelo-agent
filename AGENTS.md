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
The active work is v0.15 Media fixed Ubuntu origin evidence after PR82 merge
a473f46da617f0472603fb011c2d571e90a1792f. Continue Core; UI needs fresh acceptance.
Do not polish, deploy or acceptance-test UI in this phase.

## Current scope: v0.15 Media fixed Ubuntu origin evidence

PR82 matched its reviewed tree, passed 3194 CI tests and 44 actual cases/12 inputs.
Read docs/reviews/media-platform-dependency-final-{review,gates}.md. Two of four
version attempts are consumed; all four loader attempts remain exhausted/retained.
Never reset/rename/delete those quotas or automatically rerun qualification.

Astra Medium cleared docs/v0.15-media-platform-origin.md for offline implementation;
see docs/reviews/media-platform-origin-design-review.md. Implement the fixed
origin validator/launcher and synthetic adversarial/ownership tests. No native
GPG execution before exact source, protocol, mount/exec/FD identity, UID/modes,
root evidence, quota and ledger findings are closed by source review. Index
retrieval/decompression and deb member inspection require separate execution gates.
No apt install/update, host keyring/config/service changes or Chisel execution.
Reuse mature GPGV and later dpkg-deb; no custom crypto/deb decoder or general resolver.

Preserve shared admission runtime/media-elf-metadata-admission, observation before
creation, repeated-cancel/pending settlement, support FD retention/close-once,
conservative helper-failure fencing and pending proof before release. No proof-file
I/O after successful release. Late Lease.finish failure can leave an empty marker;
refuse receipts without universal fencing claims. Origin attempts1/4 and2/4 are retained; the second reached GPG exit0 but refused
an unrelated Release declaration above1GiB. See docs/reviews/media-origin-r4-refusal.md.
R4 mount/FD source findings are closed. Separate design review cleared the
metadata-only12digit declaration-domain correction with selected128MiB/32MiB bounds
unchanged; a second offline review cleared literal @ in discarded bounded relative
path metadata with all traversal/URI/selection refusals retained. New exact-source
review must clear any third attempt; no automatic
retry or retroactive acceptance. New origin quota must be one
fixed namespace across every origin policy revision, never a bypass for ELF quotas.

Focused tests, causal faults, matching-head full CI, standalone supplements and
full source/actual-evidence review precede normal exact-tree merge. Origin evidence
is conditional historical attribution, not freshness, safe code or current key
validity. Platform notices/import/frozen/builtin/dlopen/search/ABI closure,
materializer/immutable context, build/daemon settlement, resources/startup/installer/
filter and decoder remain separate. Original resource/filter gates before decoder.

No Pillow/decoder, ctypes/libseccomp/filter execution, Docker build/create, cgroup/
host/daemon changes, runtime/task/route/registry/capability/Core/Agent/Mind binding,
model requests, signing/OAuth or live env/data/config/listener changes. Flags
disabled, v0.14.0/UI61 preserved. Dedicated worktree only, no unrelated/user data
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
