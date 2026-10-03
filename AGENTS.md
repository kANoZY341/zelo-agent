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
The active work is v0.15 Media Pillow wheel artifact qualification after Media
contract PR #73 at `aed13e6`. Continue Core; UI rework requires fresh acceptance.
Do not polish, deploy or acceptance-test UI in this phase.

## Current scope: v0.15 Media Pillow wheel artifact qualification

Media PNG contract PR #73 merged at `aed13e6410db099fa0ad6772feb1b37c1f70baff`
from reviewed/tested head `043aa2a8078fb365a5182e9fa7daad4c7717ea94`; trees match.
Full exact-head PR CI passed 2,053 tests. 513 focused/Browser compatibility checks,
four standalone supplements and two negative controls passed. Astra Medium design
and exact full review found no remaining blockers. See the final receipt.

Read `docs/v0.15-media-wheel-artifact-qualification.md` before implementation.
Implement only a closed immutable-bytes-to-candidate-inventory validator for the
independently observed Pillow 12.3.0 CPython 3.12 Linux x86_64 wheel, plus offline
fixtures/tests and credential-free documentation. One exact pinned public release
download into a new private ignored runtime diagnostic file is in scope for actual
artifact inspection. Hash/size-check before standard-library ZIP parsing. No package
import/install/extraction, decoder/native/resource/owner execution, new runtime
factory/registry/capability/task/Core/Agent/Mind route or live settings/database change.

Inspect the exact pinned archive shape without importing before enumerating closed
required members, metadata/tag/license fields. Strict bytes, closed upstream hash,
bounded entries/depth/paths/modes/compression and running actual bytes/CRC/EOF checks;
reject traversal/duplicates/collisions/symlink/special/unsupported entries. Bound
metadata/RECORD, verify canonical complete file hashes/sizes/self-row and preserve
all notice inventory. Reuse standard-library ZIP and ArtifactFile value semantics,
without changing the Browser scanner or building an install/plugin framework.

Return scanner-derived frozen nested tuples and versioned policy-bound candidate
digest/trust none. File presence and hash/RECORD/page publisher claims are not ABI,
codec, source safety, complete license audit or cryptographic attestation verification.
No package import or runtime acceptance follows inspection. Pinned native bundle/
attestation, decoder/transform correctness, resources, one absolute deadline,
durable admission and confirmed cleanup remain separate later gates. Preserve
qualified Browser/Shell contracts and authority/ownership behavior.

Use the dedicated worktree and Astra Medium. Require adversarial offline tests,
actual fixed candidate inspection, independent exact-head static review, full CI and
installed-Codex supplements before merge. No system/global/shared-venv installs,
sudo, services, listeners, model requests, user data or unrelated deletion.
Production flags stay disabled; v0.14.0/UI #61 preserved. Computer/external adapters
remain separate; v0.16 channels -> v0.17 organizations -> v0.18 autonomy stays ordered.

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
