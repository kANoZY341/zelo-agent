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
The active work is v0.15 isolated USTAR qualification after PR89 merge
69dd06818bd8ba37ac7669dd58b5667dc1dc8209. Continue Core; UI needs fresh acceptance.
Do not polish, deploy or acceptance-test UI in this phase.

## Current scope: isolated synthetic USTAR launcher source review

PR89 merged at69dd06818bd8ba37ac7669dd58b5667dc1dc8209 with reviewed tree
7419f3a46183afa7fb035fef4dafa73b112f2819. CI37196275201 passed4,122 tests,
two skips. The scanner has no extraction or GNU/PAX/legacy/sparse fallback.

Read docs/v0.15-media-ustar-isolation.md and its source review. The new fixed
18-case qualifier and fake tests are implemented; native --run is allowed only
after a frozen-source review explicitly clears one full matrix. No actual
synthetic proof is yet claimed at this source checkpoint. Never invoke dpkg,
read retained .deb members, import Pillow or activate a Media route from this gate.

Reuse unchanged Python namespace, NativeExecutionObservation/run_process and
existing readiness/repeated-cancel helpers, with three pinned read-only source
copies. No project mount or inherited credentials/config. All source/ready FDs
stay held until actual owned process cleanup; no result on unknown cleanup.
Other interpreter/stdlib/loader/library/bwrap bytes are a conditional trusted
host baseline, not complete package/binary/source/ABI authentication. Effective
rlimits and allocation refusal are not an aggregate cgroup proof.

The new runtime/media-ustar-isolation/ustar-proof-* scope allows at most TWO
retained attempts across revisions. Any real mismatch stops the matrix without
retry; preserve all failed bytes. Exact0700 directories, <=8 entries/5MiB, fixed
JSON names/ceilings and at most two0600 named FIFOs. Pending proof before single
support release; receipt binds its hash after confirmed cleanup with no further
proof-file I/O. A shared lease close can clear marker before failing: refuse
receipt, do not claim a universal retained fence. No source/descriptor close retry.

135 fake/synthetic cases,515 focused cases before the last two mode checks and
four causal controls passed. The combined Media/context suite had2,441 passes
before those extra checks. Repeat final-source regressions and full CI. Source
review, actual evidence review, matching full CI and exact-tree merge all remain.
Current-status heading stays unique and under2200 characters; roadmap consistent.

Original scopes unchanged: origin4/4 and old-loader4/4 exhausted, index2/4,
ELF-version2/4, acquisition1/4, dpkg identity1/1, source corpus16/16, helpers8/8
plus one directory read. Tarfile observationR1 refused on0644 expectation; one
metadata diagnostic foundroot0755, and explicitly scoped finalR2 inert read
passed. Total content observations2/2 consumed, with original refusal preserved;
no chmod, repair, installed source reread, quota reset or new authenticity claim.
Retained tarfile hash664f862558196d80a46663a81bc16cd51061ccec6c1fdda5950a106e97e12512.
Original archive proof c7af88e91dc08c92b83f218c5553574f6a4df6ba89b42ce70010990e22771f51.

No new Codex/model job; direct ChatGPT reviews only. No shared authority helper,
app route/dependency/flag/UI, daemon/system/updater/config/live data modification.
Resource/filter still precedes image decoding. Continue v0.15 then v0.16->v0.17
->v0.18, preserving v0.14.0 and parked UI #61 and its worktrees.

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
