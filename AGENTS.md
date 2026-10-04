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
The active work is v0.15 fixed reader-bootstrap ELF declarations after PR90
merge8c52e7c12eec69aa50fa37a11f92be3995bbedab. Continue Core; UI needs fresh acceptance.
Do not polish, deploy or acceptance-test UI in this phase.

## Current scope: fixed reader-bootstrap ELF declaration source review

PR90 merged at8c52e7c12eec69aa50fa37a11f92be3995bbedab with exact reviewed tree
456f1b5c260439f1ff9347791b900b492d073b9c. CI37197794882 passed4,257/two skips;
one18-case native SYNTHETIC matrix passed, audited with8 semantic tamper controls.
ProofSHA1539de958da77b1650d0f4a9538892e81989ba6df945d89a2864ad39819d45ac.
That new USTAR scope is1/2 consumed, not real-package or aggregate-cgroup authority.

Read docs/v0.15-media-reader-bootstrap.md and its source review. One fixed inert
observation copied8 binaries/3,718,288bytes and6 notices/60,489bytes. Retained audit
passed; root bootstrap is conditional, not independently authenticated platform.
The lock SHA753df1ca08e5be5b29ae8b73d220bf6bfea052b8bed9ffdc0f64ac12091a8e4f
binds only their private copies. No installed file reread is needed by this phase.

The new scripts/qualify_media_reader_elf.py composes existing version _native,
loader/version validators and source/tool pins unchanged. Its32 controls+8 fixed
data inputs=40 calls fit the prior44-call envelope. Use SAME existing
runtime/media-elf-version-proofs/version-proof-* quota of4 attempts across all
revisions:2 already consumed, at most ONE source-cleared batch consumes3/4.
No new prefix/reset, retry, proof rewrite, old-loader or origin/acquisition run.
No target executable/dynamic loader is run: these8 files are ELF parser data only.
Source freeze/review precede native --run; actual evidence, matching own full CI
and exact-tree merge remain.90 new tests,2,533 combined Media/context checks and
5 causal guard controls passed at the implementation checkpoint.

The pure cross-file graph receives already source-bound outputs accepted by both
unchanged validators; it is not replacement child-output validation. Every needed
provider/required version must be present by exact recorded SONAME, interpreter
fixed, loader search/textrel refused. Preserve weak flags and leave actual load,
symbol/ABI/runtime/provenance claims false. Missing dependencies never trigger
recursive filesystem/network discovery. Keep all copy/source FDs through cleanup
and final readback, preserve native-written refusal files, release once and emit
only the separate post-cleanup proof-bound receipt. Existing marker-close caveats
remain; no guessed owner or rawFD retry. Same96-entry/8MiB/version attempt caps.

After accepted declarations, a separate mature native package reader container
and supported-format design are required. No dpkg/compressor/Docker/Pillow run,
actual .deb member operation, source install, Core route, system/config/daemon/
updater change, user-data or enabled flag from this metadata source checkpoint.

Original scopes: origin4/4 and old-loader4/4 exhausted; index2/4; ELF-version2/4
before this proposed third batch; acquisition1/4; dpkg identity1/1; source16/16;
helpers8/8+one directory read; tarfile contentsR1/R2=2/2 and one metadata diagnostic.
All failures preserved, no chmod/repair/reset. Root tarfile retained hash
664f862558196d80a46663a81bc16cd51061ccec6c1fdda5950a106e97e12512.
Archiveproofc7af88e91dc08c92b83f218c5553574f6a4df6ba89b42ce70010990e22771f51.
No model/Codex jobs; reviews direct ChatGPT only. Preserve exactly one active
status heading, decisive facts<=2200chars and matching roadmap. Finish v0.15
then v0.16->v0.17->v0.18; parked UI #61/worktrees and v0.14.0 stay untouched.

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
