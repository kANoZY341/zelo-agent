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
The active work is v0.15 native Media package-reader container contract after
PR91 merge 09b68a5355098e15f3549faa98126203c08309eb. Continue Core; UI needs fresh acceptance.
Do not polish, deploy or acceptance-test UI in this phase.

## Current scope: pure native-reader container contract and synthetic next gate

PR91 merged at 09b68a5355098e15f3549faa98126203c08309eb with exact reviewed
tree 4b39b2f253108e7bbf1461b4cccaffac321d270e. Exact-head CI 37204122789
passed 4,347 tests, two skips. One source-cleared 40-case ELF metadata batch ran
once and passed as version-proof attempt 3/4. Proof SHA256:
cc5c463a62ccd73534d88c37b2dbc9ad4b0343161066998343877e469a6601e5.
Independent stdlib-only audit accepted all 40 retained records and the eight-node
dependency graph; no target executable, loader, compressor or package member ran.

The graph closes dpkg-deb over fixed libz/liblzma/libzstd/libbz2/libmd/libc and
the fixed loader, with every declared required version present. This is declaration
consistency only: actual dynamic load, symbol resolution, ABI qualification,
platform completeness, independent binary provenance and runtime authority remain
false. Version proof quota is now 3/4 consumed; do not spend the final attempt
without a separately reviewed need.

Current tracked work may include only the consolidated offline boundary in
docs/v0.15-media-native-reader-container.md: deterministic inert image manifest,
four fixed synthetic Debian fixtures, exact vendored-default seccomp pin, canonical
container policy, Media-specific owner and fake-only lifecycle tests. No Docker
daemon call, image build/create/start/export, dpkg-deb/loader/compressor run,
acquired archive/member read or Pillow execution is cleared by this source slice.
Focused offline tests pass158 and combined Media/context checks pass2691.

The proposed future command is fixed loader + fixed library path + dpkg-deb
--threads-max=1 --fsys-tarfile -, stdin only, no host PATH/helper fallback. Runtime
policy remains fail-closed: content image/bundle identity, runc, network none,
read-only root, uid/gid65534, cap-drop ALL, no-new-privileges plus exact Moby
default seccomp,512MiB/no extra swap,pids8,half CPU,nofile32, private IPC/cgroupns,
no host user/PID/UTS namespace, init, mounts/devices/ports/DNS/sysctls/tmpfs,
ambient Docker config or daemon logs/restart.

After this offline slice passes its own full CI and exact-tree merge, implement a
separate one-shot setup/operator. That later branch may build/export/attest only
the inert fixed image and must receive new frozen-source clearance before one
synthetic native matrix. Use only the four fixed synthetic inputs; none of the
eight acquired archives. Actual cgroup/process-tree enforcement and cleanup must
be evidenced before receipt. Native reader and USTAR scanner remain separate
trust boundaries; GNU/PAX refusal returns to design. Real package/member/runtime/
import closure, resource/filter and image decoding, Core binding and release remain.

Original scopes remain preserved: origin4/4 and old-loader4/4 exhausted; index2/4;
ELF-version3/4; acquisition1/4; dpkg identity1/1; source16/16; helpers8/8+directory;
tarfile content observations consumed; USTAR isolation1/2. No reset, proof rewrite,
automatic retry, system/config/daemon/updater change or user-data mutation.
No model/Codex job. Preserve exactly one machine-consumed active status heading,
decisive facts <=2200 chars, parked UI #61/worktrees and v0.14.0. Finish v0.15,
then canonical v0.16 -> v0.17 -> v0.18.

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
