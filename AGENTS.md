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
The active work is v0.15 synthetic native Media reader qualification after
PR92 merge f05abe11cd6b659a8339132028ca710e16393c48. Continue Core; UI needs fresh acceptance.
Do not polish, deploy or acceptance-test UI in this phase.

## Current scope: synthetic native-reader operator; no actual run yet

PR92 merged at f05abe11cd6b659a8339132028ca710e16393c48 with the exact reviewed
tree. CI37207303870 passed 4,505 tests and two skips. It added only the offline
container/image/fixture/seccomp/owner boundary; no Docker/native process ran in
that PR. PR91's dependency proof remains version attempt3/4 with proof
cc5c463a62ccd73534d88c37b2dbc9ad4b0343161066998343877e469a6601e5.

Current tracked work may implement the separate one-shot synthetic operator only.
Image build ownership MUST be bound to the unpredictable 64-hex admission lease
before create/start, including the built image label, so lost build acknowledgement
can be recovered by exact label. Zero or one resource per lease label is accepted;
multiple images/containers refuse. Cleanup verifies exact labels before removal
and never deletes unrelated resources.

The setup copies only eight retained bootstrap binaries into a private0700 build
context, uses FROM scratch, docker build --network=none --pull=false, and creates
a stopped setup container for bounded export verification. Reuse the reviewed
Browser image-export verifier; do not write another tar extractor. Verify exact
image bytes/modes and re-read build-context inputs before any start.

Runtime uses the fixed loader/library path and dpkg-deb --threads-max=1
--fsys-tarfile -, stdin only. Before stdin, inspect the actual running container
and read only its exact cgroup-v2 membership plus memory.max, memory.swap.max,
pids.max, pids.current and cpu.max. Require512MiB, zero extra swap, pids8 and
50000/100000 CPU. No recursive cgroup discovery.

The native synthetic matrix is four fixed Debian fixtures plus stdout saturation,
timeout, cancellation and repeated-cancellation controls. Cancellation or output
saturation is permanent refusal regardless of later exit status. Each case must
settle its exact labelled container; final success also removes the exact labelled
image, releases support once, and emits only a proof-bound post-cleanup receipt.
No acquired .deb, Pillow, Core route, enabled flag or UI work is eligible.

Offline/fake source currently passes176 focused operator/owner tests and2755
combined Media/context checks. Real --run is NOT cleared until this new source is
frozen, reviewed, passes its own full CI, and receives explicit one-attempt
clearance. Any actual refusal consumes the new native-reader attempt and stops.

Original scopes remain: origin4/4 and old-loader4/4 exhausted; index2/4;
ELF-version3/4; acquisition1/4; dpkg identity1/1; source16/16; helpers8/8+directory;
tarfile observations consumed; USTAR isolation1/2. New native-reader scope is0/2.
No reset, proof rewrite, automatic retry, system/config/daemon/updater/user-data
change or model/Codex job. Preserve one active status heading <=2200 chars,
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
