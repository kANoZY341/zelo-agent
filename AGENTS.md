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
The active work is v0.15 Media package-reader source/provenance research after PR86
merge b1638a0e7f074c8ca8a2bc47326e315d510a0820. Continue Core; UI needs fresh acceptance.
Do not polish, deploy or acceptance-test UI in this phase.

## Current scope: package-reader official-source manifest design

PR86 merged at b1638a0e7f074c8ca8a2bc47326e315d510a0820 with its exact reviewed
tree 53f8fcd719b51058c99081557dc0bb37453c80e7. Matching pre-merge CI passed
3,737 tests, two skips; four retained standalone passes and seven causal controls
are recorded. A fresh direct acquisition suite passed 97 tests. Read
`docs/reviews/media-package-acquisition-r2-source-review.md`,
`docs/reviews/media-package-acquisition-actual-review.md` and
`docs/reviews/media-package-acquisition-final-gates.md`.

Exactly one acquisition batch passed: eight archives / 8,745,084 bytes; all exact
hashes and the retained inventory were audited. Accepted proof SHA256:
c7af88e91dc08c92b83f218c5553574f6a4df6ba89b42ce70010990e22771f51.
No archive members were parsed and no runtime authority was granted.

The three-path R2 local identity observation also completed after design review.
Read `docs/v0.15-media-package-member-identity.md` and its two review records.
Only /usr/bin/dpkg-deb, /usr/share/doc/dpkg/copyright and /var/lib/dpkg/status
were observed as inert bytes. The installed status record reports dpkg
1.22.6ubuntu6.6 amd64 but has NO Source field. Binary-to-source correspondence
and independent package membership are unresolved, not inferred from that record.
No package tool was executed. Local identity research is consumed (1/1).

Next: prepare a separately reviewed closed official-source retrieval/inspection
manifest with exact distribution revision/patch identity, literal source/document/
license paths, finite request/file/per-file/aggregate/retained limits, failed-request
accounting and no retries or recursive discovery. ZERO public requests are cleared
by the completed local identity observation. Do not inspect arbitrary installed
files, rerun the identity observation, invoke dpkg-deb (even --version), read archive
members, decompress/extract/install, or write a reusable member parser/materializer.
A future mature-tool adapter and native sandbox qualification are separate gates.

The prior local-identity Codex review hit its usage limit and is NOT a completed
verdict. The continuing session used direct ChatGPT review and tools; no new Codex
job was launched. Do not retry exhausted Codex jobs or claim a review occurred when
it did not. Preserve explicit source, actual-evidence, matching CI and exact-tree
merge requirements; direct review is not a claim of a separate Codex execution.

Retain all scopes: origin 4/4 exhausted, index 2/4, ELF version 2/4, old loader
4/4 exhausted, acquisition 1/4, and local identity 1/1. No quota reset, proof
repair/overwrite or additional actual attempt. Shared admission/ownership helpers
remain unchanged. Preserve every old proof and all user/runtime data.

No Pillow/image decoder, native member tool, ctypes/filter, Docker/cgroup/daemon/
system/updater changes, live data/config/model calls/listeners, Core/Mind/Agent/
route binding or enabled flags. Resource/filter gates still precede decoding.
Historical archive attribution is not freshness, current-key validity, member
provenance, platform/ABI qualification or runtime authority. Continue v0.15 and
then the canonical roadmap to v0.18; preserve v0.14.0 and parked UI #61.

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
