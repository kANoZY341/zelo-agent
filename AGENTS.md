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
The active work is v0.15 Media PNG contract qualification after Shell operator
PR #72 at `159caee`. Continue the established Core roadmap; UI rework requires fresh
acceptance. Do not polish, deploy or acceptance-test UI in this phase.

## Current scope: v0.15 Media PNG contract qualification

Shell operator/application PR #72 merged at `159caee5c5fba2bbc25c3b3f75b5365eebef1fed`
from reviewed/tested head `1d9fd54fc37cb977ec58f04f7c295f1bfc307835`; trees match.
Full CI passed 1,817 tests. 163 focused and 302 other assembly compatibility checks,
four standalone supplements, six real normal/invalid-ambient cases each and two
negative controls passed. Exact Astra Medium full review found no blockers. Both
model counters stayed zero; Runner/runtime/DB shutdown ownership was confirmed.
See `docs/reviews/shell-operator-final-gates.md`.

Read `docs/v0.15-media-png-contract-qualification.md` before implementation.
Implement only qualification-specific immutable bounded PNG envelopes, thumbnail
requests and explicitly untrusted candidate outputs, plus offline fixtures/tests
and documentation. No decoder execution, package installation, process/runtime/
owner factories, admission state, registry/capability/task/Agent/Mind route, live
settings, database, service or release change in this slice. No caller host paths,
URLs, arbitrary filters, code, metadata, plugins or executable/env/runtime overrides.

Inspect only bounded PNG framing: signature, exact IHDR/IDAT/IEND ordering/count,
CRC, lengths, positive dimensions, RGB/RGBA8 noninterlaced subset and exact framing
end. Never decompress or assert valid pixels. Reject unknown/ancillary/animation/
palette chunks and trailing data. Strict bytes only; reject mutable/coercible types.
Bind source bytes and exact box to a versioned canonical request/policy digest.
Candidate-specific types must derive hashes/dimensions from exact retained bytes,
compare declared hashes to the validated request, retain instruction trust none and
exclude runtime-success, cleanup/provenance or transformation-correctness claims.

Reuse evaluation favors a future isolated Pillow PNG adapter; reviewed upstream
MIT-CMU license and security/API documentation are linked in the plan. It is not
installed or approved. Exact pinned/native bundle provenance, license notices,
decoder success, transformation correctness, resource enforcement, one absolute
deadline, durable admission and owned cleanup need separate native qualification.
Preserve existing Browser script-disabled contract and Shell identity/owner gates.

Use the dedicated worktree and Astra Medium. Require guarded offline regressions,
independent exact-head static review, full CI and installed-Codex supplements before
merge. No system installs, sudo, services, public listeners, model requests, user
data or unrelated deletion. Production Browser/Shell stay disabled, published
v0.14.0 and draft UI #61 unchanged. Computer and external adapters remain separate;
v0.16 channels -> v0.17 organizations -> v0.18 autonomy stays ordered.

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
