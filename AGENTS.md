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
The active work is the v0.15 restricted USTAR inventory adapter after PR88
merge 59dc5f540ec8028b7e016c0ba078f62c4a5a3266. Continue Core; UI needs fresh acceptance.
Do not polish, deploy or acceptance-test UI in this phase.

## Current scope: restricted USTAR adapter, synthetic qualification only

PR88 merged at59dc5f540ec8028b7e016c0ba078f62c4a5a3266 with exact reviewed tree
1d3a7237ee4722f1fbd11667e02e94e8fd4ae942. Its matching CI37194678705 passed
3,921 tests, two skips. The pure member contract remains metadata consistency
only; no fabricated owner outcomes or authenticated-payload claims.

Read docs/v0.15-media-ustar-inventory.md and
 docs/reviews/media-ustar-inventory-review.md. This slice implements only bounded
uncompressed POSIX USTAR scanning over trusted caller-provided byte streams.
TarInfo.frombuf/nts/calc_chksums are the only reused tarfile APIs. No TarFile/open/
fromtarfile/extraction/automatic GNU/PAX/sparse/compression path. Only regular0,
directory5 and inert link1/2 types are accepted. Unsupported formats refuse.
Synthetic real-stdlib header tests are cleared development work, not a qualified
live runtime or source-to-bytecode proof. No acquired .deb/native tool operation.

Tests passed201 scanner cases and2,308 combined Media/context cases;9 in-memory
causal guard removals were detected. Source review and its own full CI/exact-tree
merge remain gates. Shared admission/ownership helpers, app/routes/dependencies,
live settings/flags and UI are unchanged. Preserve exactly one active-status
heading and decisive current facts within2200 characters. Keep roadmap consistent.

Tarfile R1's0644 requirement refused. One separately scoped metadata-only lstat
observed root-owned0755/nlink1/113907bytes. A revised explicit final inert read of
that non-group/world-writable identity passed. Total content observations2/2
across R1/R2 are consumed; do not retry, chmod, repair or overwrite old records.
Retained source SHA256:
664f862558196d80a46663a81bc16cd51061ccec6c1fdda5950a106e97e12512.
The separate retained-file audit passed4 tamper controls. Static observations
never executed/imported the source; synthetic tests use trusted development
stdlib, not independently authenticated installed binary/bytecode correspondence.
Full original notice remains with the inert reference; no vendored implementation.

Next: a separately reviewed isolated parser/owner and format qualification plan.
Current cooperative checks cannot preempt blocked reads/callbacks and are not
OS resource containment. ParsedTar is not OwnerOutcome or a cleanup receipt.
USTAR-only tests do not prove acquired package compatibility. Do not silently
activate GNU/PAX processing or general tar extraction to make a fixture pass.
Native package reader, runtime/import closure, resource/filter and PNG decoder,
Core binding and release remain open. No cgroup/Docker/daemon/system/config/data/
service changes or new native attempt is authorized by this synthetic slice.

Original scopes remain: origin4/4 and old-loader4/4 exhausted, index2/4,
ELF-version2/4, acquisition1/4, dpkg-identity1/1, source corpus16/16 and helper
corpus8/8 plus its single directory observation. All failures/evidence are retained.
PR86 archive proof remains
c7af88e91dc08c92b83f218c5553574f6a4df6ba89b42ce70010990e22771f51.
No resets or inferred signature/Git/source-archive/binary membership/ABI claims.
No new model/Codex job; reviews are direct ChatGPT work, not independent Codex.
Continue v0.15 then v0.16 -> v0.17 -> v0.18; v0.14.0 and parked UI #61 remain.

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
