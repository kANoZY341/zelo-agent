# Zelo Core v0.14.0 — Agent Factory

Zelo Core is the trusted local authority and execution runtime for Zelo. v0.14
builds on the v0.13 memory/context foundation with trusted versioned AgentSpec
templates, persisted project-bound AgentInstances, bounded parent-child
delegation, exact execution identity, durable organizational hierarchy,
hierarchy-aware child creation, and deterministic activation/evaluation gates.
The canonical company chain is Managing Director -> Zelo CEO -> VP -> Director
-> Manager -> Specialist -> Worker -> Tool. Existing task, tool, worker,
approval, scheduling, budget, validation, promotion, merge and release
boundaries remain in force.

The published release remains v0.14.0. **v0.15 completion is now the
immediate engineering priority** (12 October 2026), with release gated by
[the v0.15 closeout ledger](docs/v0.15-release-closeout.md) and
[issue #166](https://github.com/kANoZY341/zelo-agent/issues/166). Existing
v0.16/v0.17 code does not mean those versions, or v0.15, are released.
The [live project status](docs/zelo-project-status.md) records implementation
and qualification evidence. UI #61 and unrelated later features are parked.

The optional [Computer Specialist startup workflow](docs/v0.15-computer-specialist-startup.md)
connects the normal Core Agent/Task APIs to a persistent Computer and confirmed
Space file operations. It requires an exact operator-reviewed registry selection,
a configured backend and separate per-instance grants; defaults are unchanged.

The owner can also [create a new populated Space through Core](docs/v0.15-space-owner-provisioning.md),
using bounded UTF-8 text and logical IDs instead of manually editing host paths.
Creation never overwrites a resource or grants Agent access.
The same authenticated API can list existing resources with pagination and a
Space filter, so the owner can rediscover them after restarting Core.

The authenticated owner can also rediscover prior Space intents and confirmations,
including while an Agent is paused, and revoke them without saved IDs. See the
[approval recovery workflow](docs/v0.15-space-owner-provisioning.md#recover-and-revoke-prior-approvals).

The [dual-approved delegated Space read candidate](docs/v0.15-delegated-space-file-execution.md)
adds a separately selected, fixed Manager/observer team and a v3 Task binding.
It requires independent parent/child confirmations; native acceptance is not yet
qualified, and existing single-observer defaults remain unchanged.

## Setup

Use Python 3.12 and run commands from the repository root (`~/zelo/core`):

```bash
# Only needed if .venv does not already exist:
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Configuration is optional. Copy `.env.example` to `.env` if you want a local
configuration file and do not already have one. `ZELO_APP_NAME` defaults to
`Zelo Agent`; environment variables override values in `.env`. The version is
defined in `app/__init__.py`.

## Run locally

```bash
# Set ZELO_OPERATOR_TOKEN to a private random token (32–512 ASCII characters).
# Stop any process using this database before applying migrations.
python -m alembic upgrade head
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Stop with `Ctrl+C`. Keep the host set to `127.0.0.1`; this version is intended
only for local access. Interactive API documentation is at
<http://127.0.0.1:8000/docs> while the server is running.

```bash
curl http://127.0.0.1:8000/
# {"name":"Zelo Agent","version":"0.14.0","status":"online"}

curl http://127.0.0.1:8000/health
# {"status":"healthy"}
```

`/health` checks database and runner readiness. Model calls are available only through Ollama on `127.0.0.1`; there is no cloud,
arbitrary URL, download, or fallback provider. `ai_prompt` tasks always require
an explicit approval. Logical profiles select the configured
`local_general`/`local_reasoning` aliases and bind the selection snapshot to the
approval.

## Tests

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

Runtime dependencies include the local HTTP client used by the Ollama adapter.
The development requirements add pytest and Ruff.

## Engineering development procedures

The [engineering development pack](docs/development/engineering-pack.md),
merged in PR #143, provides focused procedures for diagnosing bugs, choosing
meaningful tests, improving diagnostics and simplifying completed changes.
Root `AGENTS.md` selects the relevant procedure for repository work. Its pinned
MIT source references remain under `integrations/development/`; the pack grants
no runtime Skill Registry entry or execution authority. Merge and validation
evidence is recorded in the [project status](docs/zelo-project-status.md).

The [linked-worktree self-inspection fix](docs/v0.15-self-improvement-linked-worktree-qualification.md)
allows bounded read-only RepositoryInspector work on authenticated, registered
Git worktrees without accepting fabricated `.git` pointers or widening
Developer Worker/approval authority.

## Public research with project memory

The opt-in [public research workflow](docs/v0.15-public-research-workflow.md)
completed Browser retrieval of three pinned Agent-Reach source files, complete
workspace evidence staging, Mind delegation, Research execution and cleanup.

The unedited model answer failed semantic and completeness review. A corrected
source-grounded operator summary was separately approved and admitted to normal
project memory. Full scoped recall, exclusion-project isolation and replay
without duplicate memory passed. The guide retains the exact operational report,
semantic review and memory receipt.

This source-file workflow does not establish RSS/Atom ingestion, automatic
video-transcript retrieval or autonomous memory admission.

## Local memory comparison

The opt-in [Hindsight comparison](docs/v0.15-hindsight-memory-comparison.md)
uses a frozen public/synthetic workload, fresh databases, native recall text,
explicit Core lifecycle filtering and separate operator answer grading. The
guide and [project status](docs/zelo-project-status.md) record its retained
outcome, costs and limits. It runs through isolated operator scripts and does
not change Core's memory backend.

Its unmodified reranking model is credited to
[Prithivida's FlashRank model repository](https://huggingface.co/prithivida/flashrank/tree/858a1ac046a05663a35367eac852d7f76feeefdd)
under its retained CC-BY-SA-4.0 model notice; package licenses are recorded
separately in the pinned dependency closure.

## Vendored REA source (inactive)

The complete upstream REA 6.0.0 **tracked source snapshot** is copied into
`third_party/rea/` at pinned tag `rea-agents-6.0.0`. This is our own versioned
source, not a dependency on a live third-party MCP service or runtime npm
package. The immutable source manifest, original MIT and third-party notices,
and five optional external engine gitlink identities are recorded in
`integrations/rea/source-v6.0.0.manifest.json`. The optional engines are
**not** bundled. Verify without installing or executing REA:

    .venv/bin/python -m scripts.verify_rea_source_vendor

This import registers **no Zelo Worker, agent, capability, Tool, API route,
computer, network connection or execution authority**. See
`docs/v0.15-rea-v6-source-import.md` for provenance and later gates.

An optional [offline target admission utility](docs/v0.15-rea-target-admission.md)
can seal operator-selected text inputs from a fixed private inbox into inert
snapshots. It requires explicit opt-in and grants no analyzer or Core authority.
An explicit [offline target preflight](docs/v0.15-rea-target-preflight.md)
rechecks an admitted snapshot against the unchanged pinned REA source and
catalog and returns a content-bound *inert* review fingerprint through
`python -m scripts.preflight_rea_target --allow-offline-rea-preflight`.
There is no live Core API route, analyzer execution, Task, approval or Tool grant.
A separate [Core operator target selection ledger](docs/v0.15-rea-operator-target-selection.md)
records authenticated, operator-asserted metadata and terminal revocations,
not target verification or analyzer execution authority. A separate
[read-only REA analysis plan](docs/v0.15-rea-analysis-plan-preview.md)
shows why the draft Specialist and selected target cannot execute. It never
registers an analyzer Worker, grants a Tool or creates a Task.
A separately gated [offline manual sealed-target static probe](docs/v0.15-rea-manual-static-target-qualification.md)
prepares exact copied bytes under a no-network, read-only Docker policy. It
cannot run without explicit local opt-in and prebuilt reviewed runtime assets;
it does not authorize Zelo Core to analyze selected targets or create Workers.

## Curated community integrations (non-authoritative foundation)

The ordered [Open Code Review / ECC / Harness / Archify / Context Mode concepts /
OpenDots import program](docs/v0.15-curated-community-import-program.md)
retains 19 pinned source-and-license files for five permissively licensed
projects, with a separate no-code-copy Context Mode design. Run the offline
integrity check with:

    .venv/bin/python -m scripts.verify_curated_community_sources

Authenticated operator candidate views are available under
`GET /integration-candidates/community` (sources), `/skills`,
`/teams/{template}`, `/architecture`, `/architecture/mermaid`. Four
[synthetic ECC fixed-case suites](docs/v0.15-ecc-fixed-case-evaluation.md) are
available to authenticated operators at `GET /integration-candidates/community/ecc/{skill_id}/cases`
and `POST /integration-candidates/community/ecc/{skill_id}/evaluate`.
These score submitted decisions on fixed Zelo-authored scenarios only; no model
is run and no Skill, Task, Tool, promotion or runtime authority is granted.
A separate [explicit local Ollama ECC probe](docs/v0.15-ecc-local-model-probe.md)
can observe only three synthetic-case responses from one already installed
local model, using Zelo's existing ModelProvider. It requires an opt-in CLI
flag, never grants permissions, and does not qualify a real Agent or Skill.
An [operator-selected Harness Project team review plan](docs/v0.15-harness-project-review-plan.md)
can bind exact imported blueprint identity, an existing Project and proposed
resource ceilings with collision and inactive-project denials through the
POST `/integration-candidates/community/teams/{template}/project-readiness`.
It creates no Agents, Workers, Tasks, permissions, or spend budget. An
[authenticated OCR evidence preview](docs/v0.15-ocr-operator-evidence-preview.md)
is available through the operator-only POST
`/integration-candidates/community/open-code-review/evidence/{base_sha}/{head_sha}`.
It binds raw untrusted review JSON to exact committed Git changed lines and
returns non-authoritative, content-bounded evidence without running reviewers
or granting Tasks, approvals, GitHub writes or merge authority. A read-only
OpenDots-inspired Computer Panel data projection is available at
`GET /agents/{instance_id}/computer/panel` using Zelo's existing permission
and service boundaries. Deterministic architecture examples are in
`examples/curated/`. The imported upstream documents are reference data,
not active runtime plugins or Agent/Tool grants. The actual UI, reviewer-worker
qualification, skill promotion and actual Space resource access remain future gates. The separately
staged [operator Space-resource registry and confirmation contract](docs/v0.15-space-resource-operator-confirmation.md)
records bounded, pre-provisioned directory identities and revocable,
AgentInstance-bound owner confirmations, but grants no Worker/Tool/file reads.
A separate [task-bound Space file gateway](docs/v0.15-space-file-gateway.md)
proves one controlled read-only use-point with actual running Computer Task
identity, permissions and safe descriptor-relative file reads. It remains
internal and does not turn that proof into an automatically callable Tool.
The follow-on [explicit Space file Task route](docs/v0.15-space-file-task-routing.md)
binds an exact owner confirmation to a Computer Task action and safely returns
bounded read-only content through the existing Core Runner. It cannot bypass
Core permissions or human takeover, and it does not enable general file or
shell access, auto-admit context, or make default Agents Space-authorized.
An authenticated, [read-only Specialist Space access status](docs/v0.15-specialist-space-access-status.md)
is available at `GET /agents/{id}/specialist-profile/space-access` to show
eligible file Tasks and explicit owner/permission/lifecycle/root denials.
This is inspection metadata, never a separate grant or file-access Tool.

## Structure and scope

REA's source-owned v6 Specialist is available to authenticated operators
as a read-only draft candidate at GET /integration-candidates/rea.
Its instructions, capability and skill remain outside live registries.
The candidate references the local pinned REA v6 source and all 139
upstream observed MCP tool names, but grants no execution, tools,
computer, secrets, network or Task/Worker authority. See
docs/v0.15-rea-integration.md and docs/v0.15-rea-v6-source-import.md.

- `app/application.py`: reusable explicitly configured application factory/lifespan.
- `app/main.py`: normal environment-backed ASGI entry point.
- `app/api.py`: typed HTTP responses and routes.
- `app/config.py`: environment-backed settings.
- `tests/`: automated endpoint and configuration tests.
- `AGENTS.md`: permanent development and architecture rules.
- `memory/`, `secrets/`, `workspaces/`: reserved local directories, ignored by Git.

Existing contents of the reserved directories must be preserved. On a fresh
clone, application startup creates the configured workspace root if missing;
create the other reserved directories only when needed. Keep credentials out of
source control.

Provider access is typed and replaceable behind `app.gateway.ModelProvider`.
The authenticated `GET /models` endpoint reports local alias availability.
Prompts and hidden reasoning are not written to audit events; only model result
text and non-secret usage/timing metadata are persisted.

## Early Talk to Zelo

v0.12 includes a persistent Zelo Mind text surface without creating a parallel
execution system. Conversation may answer directly
or request one allowlisted typed Core task. Core still owns validation,
idempotency, approvals, execution, task state, and results.

Authenticated endpoints:

```text
POST /conversations
GET  /conversations
GET  /conversations/{id}
POST /conversations/{id}/turns
```

Every turn submission requires a unique `Idempotency-Key`. Zelo persists the
validated Mind action before any Core task is submitted, so a retry after a
process interruption reuses the same action and the same Core task instead of
asking the model to decide again.

`GET /conversations` returns bounded conversation summaries. Thread reads use
`turn_limit` and `turn_offset` (default 50, maximum 100), while model context
queries only a bounded recent window. Mind model generation also uses a
process-wide admission limit configured with `ZELO_MIND_MAX_CONCURRENCY` and
`ZELO_MIND_ADMISSION_TIMEOUT_SECONDS`.

A linked task is projected back into the conversation from canonical TaskService
state, so a workspace can show pending/running/approval/result state without
duplicating it. The first proof path is documented in
`docs/early-zelo-chat.md`. Durable scoped memory is included in v0.13 and the bounded Agent
Factory is included in v0.14; broad capabilities and external channels remain in
their later canonical milestones.

## Optional local push-to-talk voice

v0.12 voice adds local speech I/O around the existing Talk-to-Zelo conversation
contract. Voice does not create a second Mind or task path.

Install the optional speech environment separately from the Core runtime and choose
exactly one TTS provider:

```bash
python3 -m venv .venv-voice
source .venv-voice/bin/activate

# Trial multilingual/cloneable voice:
python -m pip install -r requirements-voice-chatterbox.txt

# OR lightweight CPU Sherpa/Piper voice:
# python -m pip install -r requirements-voice-sherpa.txt
```

The provider-specific files both include the common `faster-whisper` STT
dependencies through `requirements-voice.txt`; neither TTS runtime requires the
other one to install successfully.

Set a separate random `ZELO_VOICE_TOKEN` in both the voice-service environment
and the Workspace server environment. Keep `ZELO_VOICE_URL` on loopback
(default `http://127.0.0.1:8765`), then run:

```bash
source .venv-voice/bin/activate
python -m scripts.run_voice
```

The initial STT provider is multilingual `faster-whisper`; the initial TTS
provider is Chatterbox Multilingual for English and Arabic. Both are behind the
local adapter and can be replaced later without changing Mind, Core,
conversations, tasks, approvals, or the Workspace turn contract. Raw microphone
audio is handled transiently and is not persisted by this slice.

## v0.13 Memory and bounded retrieval

v0.13 starts from an authenticated, immutable memory record store owned by Core.
Memory scopes are explicit: `personal`, `project`, or `agent`. Project scope
IDs must resolve to an existing Core Project. v0.14 AgentInstances now bind
agent/project memory namespaces through the Agent Factory while retaining the
same immutable record, provenance, expiry and correction/supersession model.

Authenticated endpoints:

```text
POST /memories
POST /memories/retrieve
GET  /memories?scope_kind=personal
GET  /memories/{memory_id}
```

`POST /memories/retrieve` is deterministic and bounded. The request supplies a
query plus explicit scopes. Retrieval considers only active records, uses bounded
lexical matching, returns bounded snippets and preserves scope/provenance. It does
not use embeddings or a vector backend.

Talk-to-Zelo may receive relevant **personal** memory through the existing
`zelo_context` tool envelope. This context is marked `instruction_trust: none`
and is excluded from Core task construction. Talk-to-Zelo automatic injection
remains personal-only; v0.14 AgentInstances assemble explicitly scoped
agent/project memory through the Agent Factory context path.

There are still no automatic memory writes from conversation, model-decided
retention rules, or embeddings/vector search.

### v0.13 Performance and cost evidence

Core exposes a bounded authenticated read-only evidence view at:

```text
GET /performance/evidence?limit=200
```

The evidence is derived from already-persisted conversation turns, model-task
attempts, developer-worker results and independent reviewer results. It includes
model/worker identity, exact source IDs, outcomes, reported token/usage counters
and observed durations, plus per-model and per-worker summaries.

Current local model and coding-worker records do **not** report monetary billing
cost. Zelo therefore returns
`reported_cost.status = "not_reported_by_current_sources"` and does not convert
tokens into guessed currency. Token usage is evidence of resource consumption,
not a price.

The evidence response intentionally excludes prompts, replies, diffs and worker
summaries. This slice does not use the evidence for automatic worker/model
routing; evidence-based routing remains a later autonomy milestone.

v0.13 also includes a deterministic memory evaluation harness in
`app/memory_evaluation.py`. It records exact expected/actual memory IDs,
missing/forbidden/unexpected hits, precision/recall and the retrieval version for
baseline cases. The baseline suite runs without external models or APIs and is
paired with contamination-boundary tests covering scope isolation, untrusted
memory instructions, canonical-state precedence and irrelevant-memory
non-injection.


### v0.13 Storage abstraction

Core services now depend on the Zelo-owned `SessionStore` contract rather than
on SQLite-specific implementation details. Application assembly selects the
backend through `build_storage(settings)`; the current adapter remains the same
local SQLite database and preserves the existing transaction, WAL, schema and
single-runner behavior.

`ZELO_STORAGE_BACKEND` is explicit and fail-closed. In v0.13 the only supported
value is `sqlite`. Postgres/Supabase and vector search are future adapters, not
enabled fallbacks. The current backend descriptor reports relational storage as
available and vector search as unavailable.

This slice does not migrate data, add a cloud database SDK, change memory
retrieval, or alter task/approval/scheduler authority. See
`docs/v0.13-storage-abstraction.md`.


## v0.14 Agent Factory

v0.14 generalizes the fixed Coding and Research specialists into trusted,
versioned AgentSpec templates and durable AgentInstances without creating a
second execution authority. Agent instances are project-bound, carry explicit
lifecycle state and scoped memory, and may act only through Core capabilities,
workers, tools, budgets and approvals declared by their trusted specification.

Authenticated Agent Factory and organization surfaces include:

```text
POST /agents
GET  /agents
GET  /agents/{instance_id}
POST /agents/{instance_id}/transition
POST /agents/{instance_id}/memory-context
POST /agents/{instance_id}/tasks
POST /agents/{parent_instance_id}/delegations
GET  /agents/{parent_instance_id}/delegations
POST /agents/{parent_instance_id}/children
GET  /organization/snapshot
POST /organization/units
GET  /organization/units
POST /organization/assignments
GET  /organization/assignments
GET  /agent-factory/activation
```

Task execution stores the exact AgentSpec and delegation identity used for the
action and binds that identity into idempotency/action hashes. Approval, retry
and claim-time checks revalidate the current lifecycle and delegation state.
Paused, stale or retired agents and revoked delegations fail closed.

Permanent child creation follows the canonical hierarchy and enforces trusted
spec compatibility, organizational placement, depth, budget and direct-report
capacity. Durable organizational reporting is separate from temporary task
delegation; neither graph silently mutates the other. Titles alone never grant
tool or worker authority.

Before an active AgentSpec enters runtime, deterministic activation evaluation
checks its declared task kinds, tools, configured worker requirements, review
contracts and organization authority. The accepted evidence report is bound to
policy, registry and runtime-surface digests and is available only through the
operator-authenticated activation endpoint.

v0.14 does not add the broad Browser/Computer/Python/API/Shell/Media capability
ecosystem; that remains the v0.15 milestone.

## Persistent Agent Computer foundation (v0.15 development)

Zelo now has an **inert, default-unconfigured foundation** for per-AgentInstance
persistent computers. It deliberately reuses the MIT-licensed OpenBot
computer/supervisor infrastructure pattern reviewed in CopilotKit/OpenDots
instead of rebuilding container and browser-profile lifecycle from zero.

The imported deployment is pinned to OpenBot commit
`b6932d31a8d6e7896c15139dfc27a6c6911deb27`. Zelo keeps its own Core authority
boundary, derives a separate HMAC credential for each AgentInstance, requires a
loopback-only supervisor in this first topology, preserves upstream license and
provenance notices, and fails closed if the patched supervisor contract changes.

`AgentSpec` can now describe optional persistent-computer intent plus explicit
instructions, skill IDs, Space grants and skill-learning policy. Those fields are
metadata and upper bounds only; they do not grant execution authority.

This foundation currently exposes only trusted backend configuration and
computer lifecycle identity/list/ensure/stop plumbing. It does **not** register a
Computer Worker, add a task kind, expose browser/file/shell actions to Mind or
Agents, or fall back to host execution. See
`docs/v0.15-agent-computer-openbot-foundation.md`.

Build validation uses:

```bash
ZELO_COMPUTER_SUPERVISOR_TOKEN=<random-24+-char-secret> ZELO_COMPUTER_TOKEN=<different-random-24+-char-secret> docker compose -f compose.agent-computers.yml build   agent-computer-image agent-computer-supervisor
```

The future Control Desk Computer Panel remains parked with the rest of the UI
work until Core qualification reaches that gate.

The stacked Core-control slice adds persisted per-AgentInstance computer grants
and append-only computer activity plus owner-only Core routes:

```text
GET   /agents/{instance_id}/computer
PATCH /agents/{instance_id}/computer/permissions
POST  /agents/{instance_id}/computer/start
POST  /agents/{instance_id}/computer/stop
POST  /agents/{instance_id}/computer/take
POST  /agents/{instance_id}/computer/release
GET   /agents/{instance_id}/computer/events
```

All grants default off and may not exceed the trusted AgentSpec computer profile.
Widening requires an active/current AgentInstance; revocation remains possible
for stale/paused instances. Disabling a live grant asks the backend to stop the
computer but cannot roll the Core revocation back if the backend is unavailable.
Human handback preserves OpenBot's fresh-snapshot requirement. See
`docs/v0.15-agent-computer-control.md`.

The merged first **Agent-executable read-only Computer Worker** keeps
`computer_run` Agent-only and restricted to `browser_snapshot`, `browser_read`,
`files_list`, and `files_read`. Submission binds the exact AgentInstance,
Supervisor selection, worker-authority policy, persisted permission digest, and
OpenBot backend identity. Claim revalidates all of them before creating an
attempt, so permission revocation can cancel queued work before execution.
Human takeover blocks Agent reads.

The next mutation slice is deliberately narrow: `browser_navigate` accepts
only a trusted configured `endpoint_id` plus a bounded canonical path. It uses
a separate `computer_navigate_core` Worker/authority from the read-only worker,
freezes endpoint-registry + egress-policy identity into the task, remains blocked
during human takeover, and rejects a final page outside the frozen HTTPS
authority. Arbitrary URL input is not accepted.

Click/type/key/scroll, workspace writes, screenshots in Agent/model results,
shell, credentials, and host fallback remain disabled. The egress prerequisite
and internal-topology gate ensure Agent computers have no direct public egress;
public browser traffic must cross the per-Agent authenticated Core proxy. See
`docs/v0.15-computer-worker-read.md`,
`docs/v0.15-computer-browser-mutation-egress-gate.md`,
`docs/v0.15-computer-internal-topology.md`, and
`docs/v0.15-computer-browser-navigate.md`.

## Bounded API Worker (v0.15)

`api_run` uses the trusted `api_core` worker with `api_read` capability and skill.
Configure named HTTPS origins in the operator environment (empty by default):

```dotenv
ZELO_API_WORKER_ENDPOINTS='["example=https://api.example.com"]'
```

After running migrations through `0028_api_worker_selection`, submit through the
existing authenticated `POST /tasks` endpoint:

```json
{
  "kind": "api_run",
  "payload": {
    "endpoint_id": "example",
    "path": "/v1/data",
    "query": {"q": "sample"}
  },
  "timeout_seconds": 10
}
```

Replace the example with an operator-reviewed public API origin. Only GET JSON
is allowed; credentials, custom headers, redirects, proxies and workspace access
are unavailable. Paths must be ASCII without percent escapes, dot segments or
repeated slashes. Query values are strings, encoded separately. Both compressed
and decoded responses are capped at 262144 bytes; invalid or empty JSON fails.
GET-only does not guarantee that a remote endpoint has no side effects. No host
credentials are injected; do not put tokens or private data in query strings, which
are sent to the approved origin and stored as task input.

Non-2xx JSON responses produce failed tasks with `api_http_status` and a bounded
result. Core validates every DNS answer and pins the connection to a public IP
while verifying TLS against the configured hostname.

Endpoint, policy and worker identities are persisted and revalidated at claim and
execution. Configuration changes invalidate pending bindings; normal retry binds
the current configuration. No default AgentSpec is added, and Mind routing remains
disabled until trusted endpoint names can be surfaced safely. See
[the API Worker design](docs/v0.15-api-worker.md).

## Bounded Browser Worker Core binding (v0.15 development)

Browser Worker is **disabled by default**. Migration `0029_browser_worker_selection`
adds immutable per-action browser specifications. This development slice does not
activate production, change the published v0.14.0 release, or unpark UI work.

The trusted `browser_core` worker has only `browser_document` capability and
`public-document-render` authority. Its separate origin allowlist never inherits
API endpoints. A trusted operator must first prepare and inspect a reusable image;
runtime cannot build, pull or accept an image, profile, receipt or factory from a
request. See [operator setup and limits](docs/v0.15-browser-core-binding.md#implemented-operator-setup).

Once the separate release/host gates and operator configuration are complete,
the existing authenticated task endpoint accepts this shape:

```json
{"kind":"browser_run","payload":{"endpoint_id":"docs","path":"/guide"},"timeout_seconds":10}
```

Only an endpoint ID, strict origin-relative ASCII path and 0.1–30 second timeout
are browser inputs. Query strings, URLs, credentials, cookies, headers, flags,
profiles, CDP, evaluation, clicks, screenshots and downloads are rejected.
Results are bounded **untrusted DOM text**, with `instruction_trust="none"`, source
origin/path/hash, complete renderer-contract and broker-policy hashes, node count
and truncation. They make no CSS visibility or complete-page claim.

A fresh qualified renderer uses the one configured persistent private admission
directory for every task. Worker selection capacity is advisory. Cancellation,
timeout, retry and restart never clear an uncertain cleanup marker. Successful
results require confirmed cleanup; a terminal Core task does not prove cleanup.
No default AgentSpec, department or Mind route is added.

The separate [opt-in operator qualification](docs/v0.15-browser-operator-qualification.md#implemented-host-command)
runs two real DNS/TLS document tasks in a private diagnostic Core instance and
reinspects the image after cleanup. It never enables the production worker and
is excluded from offline CI.

## Shell boundary qualification (v0.15 development)

Shell remains an [optional native qualification proof](docs/v0.15-shell-boundary-qualification.md#implemented-optional-proof),
with fixed installed artifacts, sealed bytes, no host workspace or network,
read-only guest files and zero process creation. No Shell task, registry entry,
API or Agent/Mind route exists. Host probes require explicit opt-in; offline CI
does not launch them. The shared native process helper now retains the child
identity through group cleanup before reaping, and requires Linux pidfd/waitid
support; unavailable ownership fails closed.

## Fixed Research specialist

v0.12 added a fixed `research_run` specialist on top of the same Core task
lifecycle. That fixed path remains intentionally narrow: it does not itself
grant Agent Factory hierarchy authority or the broader Browser/Research worker
capabilities planned for v0.15.

```text
research_run
  -> fixed ResearchRuntime
  -> local_general model
  -> repo_status / list_workspace_files / read_workspace_file only
  -> Core-generated evidence IDs
  -> cited bounded answer
```

Example:

```json
{
  "kind": "research_run",
  "payload": {
    "query": "Inspect the workspace evidence and summarize what it shows."
  },
  "timeout_seconds": 120
}
```

Research must execute at least one real retrieval tool before it can finish.
Every final answer declares observed Core-generated source IDs and cites them as
`[S1]`, `[S2]`, and so on. Persisted source metadata contains the tool, a safe
locator and a SHA-256 digest of the canonical typed result; raw retrieved file
contents and internal model/tool transcripts are not copied into a new memory
store.

The specialist can be Campaign-owned and can be selected by Zelo Mind. It cannot
use `system_info`, public web/browser/API access, shell/Python execution, write
tools, secrets, task creation, approvals or external channels. Those capabilities
remain in later roadmap milestones. See `docs/v0.12-research-specialist.md`.

## Local Model Gateway

`ai_prompt` tasks use the following path:

```text
Task -> ModelGateway -> ModelRouter/profile policy -> ModelProvider -> Ollama
```

The router is deterministic: `general` selects `local_general`, `mind` selects
`local_mind`, and `reasoning` selects `local_reasoning`. By default Mind falls
back to the general model, so existing installations are unchanged. Set
`ZELO_OLLAMA_MIND_MODEL` to give Zelo conversation a separate model without
changing agent/general work. The general and reasoning models remain configurable
with `ZELO_OLLAMA_GENERAL_MODEL` and `ZELO_OLLAMA_REASONING_MODEL`; the
configuration version is included in the approval binding.

Ollama is the only provider in v0.3 and must remain on localhost. Configure its
HTTP endpoint with `ZELO_OLLAMA_URL` (default
`http://127.0.0.1:11434`). Zelo never accepts provider URLs from tasks, uses
cloud providers, downloads models, or silently falls back. `ai_prompt` calls require
operator approval and return text. Authenticated `agent_run` tasks use the separate
bounded proposal loop described below.

## Native Tool Runtime

v0.4 adds a Zelo-owned, typed runtime for explicitly registered tools:

```text
Task (tool_call) -> ToolGateway -> ToolRegistry -> ToolPolicy -> tool -> typed result
                                                     -> task and audit persistence
```

The initial safe-read registry contains `system_info`, `repo_status`,
`list_workspace_files`, and `read_workspace_file`. Each tool has a stable name,
description, Pydantic input and result model, risk level, approval requirement,
and bounded execution timeout. `GET /tools` is authenticated and exposes only
these safe metadata and input schemas.

The policy supports `safe_read`, `sensitive_read`, `write`, `external`, and
`destructive` risk levels. Only `safe_read` tools execute in v0.4; all other
levels fail closed. Workspace tools accept only relative paths, reject
traversal and symlink escapes, stay under `workspaces/`, do not read contents
while listing, and bound UTF-8 file reads by size. Runtime databases, secrets,
and files outside that root are unavailable.

`ModelGateway` and `ToolGateway` are separate. Models cannot register tools,
call Python functions, choose executables, or create shell commands. Arbitrary
shell, browser, cloud, URL, write, deletion, package-installation, and external
network tools are intentionally unavailable. The v0.5 Agent Runtime validates
model proposals and routes approved capabilities through this same gateway.

## Agent Runtime

The model proposes; Zelo decides; Zelo executes. `ModelGateway` owns provider
access and deterministic model routing. `ToolGateway` owns typed tool execution
and `ToolPolicy`. `AgentRuntime` owns one attempt's in-memory conversation,
structured proposals, budgets, and the handoff between these gateways. The task
engine owns persistence, cancellation, retry, and audit history.

```text
authenticated POST /tasks (agent_run)
  -> AgentRuntime -> ModelGateway -> local general model
  -> validate one action
     final     -> persist final result and complete the attempt
     tool_call -> ToolRegistry -> typed arguments -> ToolPolicy -> ToolGateway
               -> bounded, untrusted tool output -> next model step
```

Use the existing tasks API; no new endpoint is required:

```json
{
  "kind": "agent_run",
  "payload": {
    "prompt": "Check the current Zelo repository status and tell me what branch I am on.",
    "profile": "general"
  },
  "timeout_seconds": 120
}
```

`general` is the only allowed agent profile and is also the default. It selects
`local_general` (`qwen3:4b-instruct` by default). Agent runs never automatically
invoke the reasoning/DeepSeek profile. The selected model is persisted at submission
and checked on each model step; changing its alias mapping fails the attempt.

Every model response must be exactly one Pydantic-validated JSON action:

```json
{"type": "tool_call", "tool": "repo_status", "arguments": {}}
```

or:

```json
{"type": "final", "content": "Your current branch is ..."}
```

Extra fields, unknown action types, duplicate JSON keys, non-finite numbers,
multiple actions, prose, and markdown/code fences are rejected. No command is
extracted from arbitrary text. Tool metadata comes from `ToolRegistry`, filtered
by `ToolPolicy`, and every execution passes through `ToolGateway`. The current
four tools are `system_info`, `repo_status`, `list_workspace_files`, and
`read_workspace_file`. Only safe-read tools can execute without further operator
approval. Other risks and approval-required tools fail closed. A future approval
flow belongs at this policy/gateway boundary; an agent never bypasses it.

| Setting | Default | Bound |
| --- | --- | --- |
| `ZELO_MAX_AGENT_STEPS` | 6 | 1–20 model calls, including parse retries |
| `ZELO_MAX_AGENT_TOOL_CALLS` | 4 | 0–16 executed tool calls |
| `ZELO_AGENT_TIMEOUT_SECONDS` | 120 | 0.01–600 seconds per attempt |
| `ZELO_MODEL_TIMEOUT_SECONDS` | 60 | 0.01–600 seconds per model call |
| `ZELO_MAX_MODEL_PARSE_RETRIES` | 1 | 0–2 retries total per attempt |
| `ZELO_MAX_AGENT_TOOL_RESULT_CHARS` | 8192 | 256–32768 serialized result characters |

The earlier of the task timeout and configured agent timeout ends the run. Tools
use their registered timeout (currently five seconds). The final model step must
produce a final answer; a tool proposal at that step fails without executing it.
Ollama action generation requests JSON output, disables thinking, and caps
generation at 2048 tokens. Actions are additionally limited to 32768 characters;
Zelo validates the complete Pydantic action schema before any execution. Final
answers are limited to 16384 characters. There is no infinite retry or automatic model
download. A timeout stops the loop and discards late results. Existing synchronous
safe-read tools run in a thread; Python cannot forcibly terminate that thread, so
an already-running read may finish after timeout. No further model or tool step
is scheduled for that attempt.

Tool output has a distinct `tool` conversation role and a Zelo-generated envelope:
`type=tool_output`, `untrusted=true`, tool name, bounded `result_json`, and a
`truncated` flag. The serialized result is escaped as data, never interpolated
into the system prompt. A truncated prefix may be incomplete JSON. Envelope and
escaping overhead are outside the content budget but remain bounded. The system
prompt tells the model to treat file contents as evidence rather than instructions.
This does not guarantee that a model ignores every injected instruction; registry,
typed argument, policy, and workspace checks enforce permissions regardless of
what it proposes. There is no arbitrary shell, direct Python callable access,
browser access, cloud fallback, autonomous write, deletion, or package installation.

Results contain `final_response`, `alias`, `actual_model`, `model_steps`,
`tool_calls`, unique `tools_used` in first-use order, per-step token `usage`, and
`completed_at`. Private model thinking fields and internal conversation transcripts
are not persisted. The task stores the original request and final result as before.
Audit events record `agent_started`, `agent_model_step`, `agent_tool_requested`,
`agent_tool_completed`, `agent_parse_failed`, `agent_limit_reached`, and a terminal
`agent_completed` or `agent_failed`, linked to the attempt. They omit full prompts,
model output, tool arguments, file contents, and raw exception details. Completion
and failure events commit atomically with the attempt outcome.

Provider, protocol, tool, policy, and budget failures use existing failed attempts.
`POST /tasks/{id}/retry` starts a new bounded execution with fresh conversation and
counters; it preserves previous attempts and does not resume a hidden reasoning
loop. Process restart marks interrupted runs without replaying them.

Migration `0004_agent_runtime` only extends the task-kind CHECK constraint. It
uses the existing atomic SQLite/Alembic transaction and foreign-key integrity
checks; no conversation table or other persistence schema is added.

Automated tests use fake providers and require no Ollama. To run the real local
smoke test with an already-installed `qwen3:4b-instruct`:

```bash
.venv/bin/python -m scripts.smoke_agent
```

It creates an isolated temporary database, starts Zelo on an ephemeral
`127.0.0.1` port, submits the repository-status request above through HTTP, checks
the persisted result and audit events, then stops the server and cleans up its
temporary database. It never downloads a model or invokes DeepSeek.

## Developer Runtime (v0.6)

`DeveloperRuntime` delegates an explicitly approved coding instruction to a
replaceable `DeveloperWorker`; the initial adapter is `CodexCliWorker`. This is
separate from `AgentRuntime`, `ModelGateway` and `ToolGateway`. Codex proposes
code changes; Zelo owns approval, execution boundaries, validation and results.

Use the authenticated existing tasks API:

```json
{
  "kind": "developer_run",
  "payload": {
    "instruction": "Describe the requested Zelo Core change here.",
    "validation_profile": "zelo_core"
  },
  "timeout_seconds": 900
}
```

The task starts in `awaiting_approval`. Its returned `developer_spec` records the
instruction, configured source identity, exact base SHA, workspace root, worker
implementation/configuration version, detected CLI version/capability profile,
executable digest, authentication-file location (not its contents), validation
environment identity, sandbox/validation profile versions, and effective limits.
The existing approval hash binds this specification and the task request.
Execution rechecks configuration and preflight; changes require a fresh approval.

Before runtime preflight, Zelo snapshots protected primary state, opens the
configured executable once, validates its ownership/type and approved resolved
identity, and copies its bytes into a private Linux `memfd`. Its digest must match
the approval before any code executes. The copy is sealed against writes, growth
and truncation, then independently digest-checked. Capability probes, command
sandbox probes and execution all use this same sealed artifact—not later opens
of the configured binary. Bubblewrap consumes its explicitly passed descriptor,
materializes a named executable in private tmpfs, and remounts that directory
read-only before launch. This avoids anonymous-descriptor path-resolution issues
in Bubblewrap while preserving verified bytes. Replacing the configured binary
after pinning cannot change the artifact. The descriptor closes after the attempt.

At submission there is no prior approval: trusted configured-file ownership and
a sealed content snapshot establish the proposed identity. Capability checks run
in a minimal network-disabled sandbox with no primary checkout or authentication
mount. Unsupported sealing/Bubblewrap behavior fails closed; there is no unsafe
fallback.

Each attempt owns a standalone repository at
`workspaces/developer/<attempt-id>/repo`. Zelo initializes a new Git database,
fetches the approved commit over local file transport, and checks it out detached.
There are no shared object stores, hard links, linked worktrees or configured
remotes. An ownership record lives outside the worker's writable directory.
v0.9 replaces the old global developer-execution lock with approval-bound
per-worker capacity slots. Different trusted workers may execute concurrently
when their descriptors have capacity; each worker's `max_concurrency` remains
a hard scheduler and cross-process filesystem-lock ceiling.

### Codex prerequisites and boundaries

This implementation supports Linux with functioning Bubblewrap user/PID/network
namespaces and the inspected **codex-cli 0.154.0 and 0.155.1**. Unsupported binaries,
capabilities or sandbox probes fail closed. Zelo does not install prerequisites
or log in automatically. The configured executable defaults to
`~/.local/bin/codex`; authentication uses the CLI-managed `~/.codex/auth.json`
through a read-only bind. Zelo does not copy or implement authentication storage.
Authentication refresh that needs to rewrite that file must be performed outside
the worker by the operator.

The outer Bubblewrap view exposes `/workspace`, required system binaries/libraries,
selected public system configuration, the CLI binary, private temporary storage,
and the CLI authentication file. It does **not** mount the primary repository,
runtime database, secrets, other workspaces, host home directories or Docker socket.
Only the isolated source directory is writable on the host; its `.git` is read-only.

The Codex parent retains service networking. The selected worker's approval-bound
authority resolves through Zelo's content-fingerprinted worker-authority policy.
The current production profile, `workspace-write-no-network-v1`, gives generated commands
workspace writes, read-only Git metadata, `network.enabled=false`, and denied
reads of the authentication directory. Unsupported descriptor authority fails at
registry construction instead of silently receiving a default permission set.
Zelo tests that exact profile using `codex sandbox` before invoking the model,
including direct and `/proc` authentication paths and network socket denial.
This installed CLI uses `codex sandbox`, not `codex sandbox linux`.

Execution uses `codex exec --strict-config --ignore-user-config --ignore-rules
--ephemeral --json -C /workspace`, with the prompt on stdin, explicit
`approval_policy="never"`, and the named permission profile. The named profile
provides workspace-write behavior plus authentication read denial; it is not
combined with the legacy `--sandbox` flag. Project `.codex`/`.agents` directories
are rejected; untrusted project configuration and optional hooks, plugins,
MCP installation, browser/computer capabilities and delegation are disabled.
These controls use the installed CLI's
[documented permission profiles](https://learn.chatgpt.com/docs/config-file/config-reference).

Every process receives an explicit environment allowlist. Host credentials,
Python paths, pytest options/plugins and Git override variables are not inherited.
Generated-command environment policy is also explicitly configured.

### Validation and review

Ruff and pytest load from the trusted read-only Zelo venv with Python isolated
mode (`-I`) and a fresh private Python bytecode-cache prefix; excluded existing
bytecode caches cannot change their behavior. A worktree `ruff.py` or `pytest.py`
cannot replace them. Pytest plugin autoloading is disabled. Generated project/tests execute inside a separate
Bubblewrap filesystem/PID/network sandbox with `--unshare-net`, private temp,
and no Codex authentication mount. Validation owns the four fixed Ruff check,
Ruff format check, pytest and Git whitespace-check commands.

The approval-bound validation identity hashes the trusted venv's file contents,
relative paths and modes, including Python, Ruff, pytest, installed package code
and distribution metadata/version records. Symlinked files bind their resolved
targets and contents; external directory aliases are rejected. Python bytecode
caches beneath `__pycache__` are excluded; standalone bytecode files are covered.
The manifest also binds the
trusted source checkout's requirements files, `pyproject.toml`, and presence/content
of `uv.lock` and `poetry.lock`. Hashing is bounded to 50,000 files and 512 MiB.
The manifest is checked before worker execution, before each validation command,
and after validation. This detects changes across those boundaries; it does not
freeze the host environment, hash the entire OS/stdlib/shared-library installation,
or defend against a host administrator changing files during execution. Operators
must keep the trusted environment stable for a run. Untrusted code sees it read-only.

Post-worker Git inspection also runs inside a restricted, network-disabled view.
Worker Git configuration is replaced there by Zelo-owned inspection configuration;
hooks, fsmonitor, credentials, external diff and textconv are disabled. Results
compare against the approved base and include staged/unstaged changes, NUL-delimited
status, correctly decoded filenames, and bounded diffs for untracked files.
An independent bounded filesystem inventory also finds ignored generated files,
including files hidden by worker-edited ignore rules and nested ignored directories.
Only root Git internals are excluded; nested `.git` paths are prohibited artifacts.
`ignored_generated_files` records path, file type
and size; ignored contents are never persisted, even for small text files. These
artifacts—including validator caches—count toward changed-file and review limits.
Symlinks are inventoried without following them; no generated file contents are
read by the inventory process.
Git metadata or HEAD changes are policy failures. Primary source/Git snapshots
are verified on success, failure, timeout and cancellation after subprocess cleanup.

JSONL must be valid UTF-8 JSON with bounded lines/events, an allowlisted Codex
lifecycle, a final completed agent-message summary and a successful `turn.completed`
event. Reasoning and raw command/event text are not persisted. Unknown event types
fail closed; their bounded diagnostic contains only safe event/item type labels.
Only reported usage counters are retained; Zelo invents no costs or model metadata.
Results contain the workspace location, review data, validation outcomes, UTC
lifecycle timestamps and a monotonic overall duration. Terminal audit events are
committed with the task outcome.

An observed protected-state mismatch emits `developer_boundary_violation`.
An inability to complete verification emits `developer_boundary_verification_failed`.
Both prevent success. Results retain `boundary_error` and `original_error` separately
so a later boundary result does not discard an original worker or validation failure.

### Limits and workspace lifecycle

| Limit | Default |
| --- | --- |
| Overall attempt, including preparation/validation/review | 900 seconds |
| Worker | 600 seconds |
| Each validation command | 600 seconds |
| Each Git operation | 15 seconds |
| Worker/Git stdout | 256 KiB |
| Subprocess stderr | 16 KiB |
| Validation stdout | 32 KiB |
| JSONL line / event count | 16 KiB / 1024 |
| Review diff / stat / changed paths | 32 KiB / 8 KiB / 200 |

The earlier task/configured overall deadline applies. Output is limited while
streaming; overflow fails the attempt. Timeout/cancellation sends group SIGTERM,
then SIGKILL, reaps the launcher and checks for surviving group members. Cleanup
resists repeated cancellation. A bounded finalization grace may follow the
deadline to verify protected state; a late attempt cannot succeed.

Successful and diagnostic failed checkouts remain for review. A retry receives
a new attempt directory and requires new approval. The internal `cleanup(id)`
service accepts an execution UUID, verifies ownership/containment, rejects active
or symlinked targets and holds the execution lock. It is not an API deletion tool.
Interrupted ownership records that still say active require operator investigation.

The Developer Runtime itself never commits, pushes, merges, rebases, tags,
opens a PR, deploys, or promotes changes. v0.7 adds a separate trusted
Review & Promotion Runtime for those actions, with its own exact-result bindings
and separate operator approvals. Passing developer validation means the fixed
commands passed, not that the change or worker-modified tests are trustworthy;
human review remains part of the promotion boundary.

### v0.7 real Review & Promotion smoke

Prerequisites are Linux with functioning Bubblewrap namespaces, an installed
trusted `gh` executable, an existing authenticated `gh` configuration, and
`ZELO_PROMOTION_REPOSITORY` set to the GitHub repository to promote into. The
repository's `main` branch must be current, and its required CI check (by
default `checks`) must be configured to run on pull requests. Run exactly one
safe command from `~/zelo/core`:

```bash
.venv/bin/python -m scripts.smoke_promotion
```

The smoke creates a deterministic candidate in an isolated developer workspace,
then uses the real `PromotionReviewer`, approval-bound `PromotionCommitter`,
`GitHubPromotionPublisher`, and `PromotionCIVerifier`. It creates or exactly
reuses one branch under `zelo/promotion/smoke-<base-sha-prefix>` and one open
pull request in the configured repository. Its small persistent state is kept
under `runtime/smoke-promotion/` and `workspaces/smoke-promotion/` so reruns can
reuse the exact prior result. Identify the result from the printed `branch`,
`pr_number`, and `pr_url`, or search GitHub for the `zelo/promotion/smoke-`
branch prefix.

CI verification is intentionally one-shot: the first invocation may successfully
create or reuse the pull request while its required GitHub Actions check is still
queued or running, and can therefore exit with `promotion_ci_not_green`. Wait
for the required pull request check to complete, then rerun the exact same smoke
command. Persistent smoke state and idempotency reuse the exact successful
developer and promotion result rather than creating another branch or pull
request. A genuinely failed CI check still fails closed.

The smoke never invokes Codex, modifies `main`, overwrites a branch, force
updates anything, recreates a closed pull request, merges a pull request, runs
`merge_run` or `release_run`, creates a tag, or bypasses Zelo's promotion
classes. Passing validation means the fixed commands passed; the pull request
still requires normal human review and is intentionally left open.

Full CPU/RAM/PID/disk quotas are future hardening. Current namespace isolation,
deadlines, streamed output caps and process cleanup do not provide those quotas.
The Codex parent is not restricted to an endpoint allowlist; command and validation
networking remain disabled. No real Codex smoke test is part of automated tests.
Linux boundary tests use fake workers, disposable Git repositories and a local
non-model Codex sandbox probe when that CLI is installed.

Migration `0005_developer_agent` adds `developer_run` and the JSON execution
specification through the existing atomic SQLite migration machinery. Released
migrations are unchanged; tests cover upgrade, foreign keys, failure during 0005,
rollback and retry. Apply migrations using the normal reviewed operator workflow.

List and pull models manually with Ollama; Zelo does not do either operation
automatically:

```bash
ollama list
ollama pull qwen3:4b-instruct
ollama pull deepseek-r1:7b
```

The `GET /models` endpoint is authenticated and reports configured aliases and
whether each actual model is installed. On hardware with roughly 8 GiB of
memory and no discrete GPU, local inference may be slow and concurrent model
execution is intentionally bounded to one attempt.

Future cloud providers can implement the same typed `ModelProvider` interface
behind `ModelGateway`; adding one must still pass an explicit product and
security review. v0.3 deliberately ships no cloud provider or API-key path.


## Review & Promotion Runtime (v0.7)

v0.7 turns one successful, validated Developer attempt into a separately
reviewed promotion workflow. Codex and other Developer workers still have no
GitHub credentials and no commit, push, PR, merge, or tag authority. Zelo owns
the promotion boundary and re-verifies every identity before external action.

The staged flow is:

```text
developer_run succeeds
  -> promotion_run review fingerprint + operator approval
  -> trusted deterministic commit
  -> exact GitHub branch + pull request
  -> exact-head CI verification
  -> merge_run with a separate operator approval
  -> squash merge of the approved PR head
  -> release_run with another separate operator approval
  -> annotated semantic-version tag on the exact merged main SHA
```

A `promotion_run` references the exact successful Developer task and latest
successful attempt. Its frozen specification binds the base SHA, worker and
validation identities, candidate manifest, review fingerprint, configured
repository, branch, commit metadata, and PR metadata. Before publication Zelo
re-inventories the retained workspace, reruns the fixed trusted validation
profile, reconstructs the exact Git tree and commit, and confirms the protected
primary checkout did not change.

Only the operator-configured repository is valid. The branch must use the
`zelo/promotion/` prefix. GitHub access uses the operator-owned `gh`
configuration; the worker sandbox never receives that directory. The approved
publisher identity binds the trusted `gh` executable digest, GitHub host,
credential-directory location, repository, and timeout without persisting a
token.

After the PR exists, CI verification is tied to the exact PR head SHA and the
configured required check-run names. A `merge_run` is a new task with a new
approval; it binds the exact promotion task, PR number, base/head identities,
successful CI snapshot, and merge method. The runtime re-checks the open PR and
CI before merging. A changed head/base, closed PR, regressed/missing CI check,
publisher change, or source-task mutation fails closed.

A `release_run` is another separately approved task. It binds the exact
successful merge task, merged `main` SHA, semantic version tag, tag message,
tagger identity, and tagger timestamp. Zelo creates an annotated tag only on
that approved SHA. Existing lightweight tags, tags pointing elsewhere, or
annotated tags with different approved metadata are conflicts.

Promotion, merge, and release recovery is explicit rather than automatic.
Interrupted or failed actions may be retried only through a fresh approval on a
new action version. Previous active approvals are cancelled as superseded.
Retries preserve the frozen external-action specification so Zelo can safely
discover and reuse an already-created exact commit, branch, PR, merge, or
annotated tag instead of duplicating the side effect. Closed promotion PRs are
never silently recreated.

Operator configuration:

```bash
ZELO_PROMOTION_REPOSITORY="kANoZY341/zelo-agent"
ZELO_PROMOTION_GIT_AUTHOR_NAME="Zelo Agent"
ZELO_PROMOTION_GIT_AUTHOR_EMAIL="zelo@localhost.invalid"
ZELO_PROMOTION_GH_EXECUTABLE=/usr/bin/gh
ZELO_PROMOTION_GH_CONFIG_DIR=/home/zelo/.config/gh
ZELO_PROMOTION_GITHUB_TIMEOUT_SECONDS=30
ZELO_PROMOTION_REQUIRED_CHECKS='["checks"]'
# Bind required check-runs to the trusted GitHub Actions app identity.
ZELO_PROMOTION_CI_APP_ID=15368
ZELO_PROMOTION_CI_APP_SLUG="github-actions"
```

The three v0.7 migrations add the frozen promotion, merge, and release
specifications to the existing task table while preserving the established
SQLite migration transaction and foreign-key safety rules:
`0006_promotion_review`, `0007_merge_approval`, and
`0008_release_approval`.

v0.9 adds `0009_worker_concurrency`. It replaces the original global
single-running-attempt index with a per-task running-attempt invariant and
database triggers that permit concurrent developer attempts while keeping
non-developer execution exclusive.


Shell identity/admission qualification is also optional and remains unregistered:

```bash
.venv/bin/python -m scripts.qualify_shell_owner --allow-native-shell-probes
```

It runs fixed harmless boundary and ownership cases with one durable admission
lease. Results require explicit child, pipe/task and sealed-pin cleanup; uncertain
cleanup or owner death preserves a restart fence. It adds no Shell task, Agent
route or production activation. See
[Shell owner qualification](docs/v0.15-shell-owner-qualification.md).

## Qualified Shell Core binding (v0.15 development)

Shell compute is disabled by default. Migration `0030_shell_worker_selection`
adds `shell_run` and immutable, action-bound execution identity. There is no default
Shell agent or Mind route. Explicit enablement requires `ZELO_SHELL_WORKER_ENABLED`
and `ZELO_SHELL_WORKER_ADMISSION`, an existing canonical private 0700 directory
owned by the same nonroot operator. Never choose this directory per request or
reset an unknown admission marker. The operator, host kernel/filesystem and
approved fixed source artifacts remain trusted prerequisites; content hashes
alone do not establish provenance. See `docs/v0.15-shell-core-binding.md`.

Payload is bounded UTF-8 script text only, with a deadline from 0.01 to 2 seconds:

```json
{"kind":"shell_run","payload":{"script":"n=6; printf '%s' \"$((n * 7))\"\n"},"timeout_seconds":2}
```

The lane executes only qualified sealed Dash/prlimit/Bubblewrap with the fixed
nonroot, no-workspace/no-write/no-network/no-credentials/no-fork policy. It offers
no executable, arguments, environment, paths, mounts or stdin-data overrides.
Submission/retry bind worker selection, script digest, policy and runtime identity;
claim and Runner revalidate them. Output is bounded untrusted text and grants no
authority. Successful publication and readmission require explicit native/resource
cleanup; uncertain settlement remains retained and fenced. Production settings
and the published release are unchanged.

An operator may run isolated, fixed harmless native Core fixtures explicitly:

```bash
.venv/bin/python -m scripts.qualify_shell_core_binding --allow-native-shell-probes
```

The proof creates private diagnostic SQLite/admission state under ignored
`runtime/shell-core-binding`, uses init-only settings, and exercises Core task
persistence, success, deadlines, output, cancellation, readmission and lost cleanup
acknowledgement. It leaves the latter diagnostic fence intact. It does not enable
live Core, contact a model or use external network.


## Shell full application operator qualification

After the qualified Core binding gates, an operator may exercise the full default
application/lifespan, authenticated ASGI routes and background Runner in isolated
diagnostic state:

```bash
.venv/bin/python -m scripts.qualify_shell_operator --allow-native-shell-probes
```

This command accepts no arbitrary script, input file, environment, runtime or
policy override. It uses fixed harmless built-in compute, init-only settings,
private database/admission/workspace paths and in-process HTTP without a listener.
Actual acceptance uses default qualified runtime/owner/native factories. It checks
disabled assembly, enabled refusal of an unknown fence, auth/payload rejection,
persisted exact specification/action identity, complete task/attempt/policy audit,
bounded untrusted evidence, single-attempt idempotency, readmission and shutdown.
A rejecting counted provider requires zero model requests. Assertion-enabled
Python is required; optimized mode is refused before proof entry.

Private bounded evidence is saved under ignored `runtime/shell-operator-qualification`.
The deliberately fenced diagnostic directory remains intact. Installed source
hashes are not independent provenance, and host/operator/process visibility/event
loop assumptions remain. See `docs/v0.15-shell-operator-qualification.md`.
This qualifies the diagnostic command; production Shell/Browser flags, the
published release and parked UI remain unchanged.

## Media PNG contract qualification

The v0.15 Media prerequisite in `qualification/media_contracts.py` defines a
bounded RGB/RGBA8 static PNG envelope, immutable thumbnail request and candidate
evidence. It checks framing/CRC/dimensions and exact request/source/policy binding
without decompressing image data. Candidate bytes remain untrusted; they do not
prove valid pixels, a correct transformation, provenance or native cleanup.

This is a qualification-only module, without a Media task/runtime/route or new
dependency. A future isolated Pillow adapter needs separate pinned bundle, decoder,
resource and cleanup qualification. See
`docs/v0.15-media-png-contract-qualification.md`.

The separate `qualification/media_wheel.py` gate inventories one pinned Pillow
12.3.0 Linux/CPython 3.12 release as bounded bytes. Its closed SHA is checked before
ZIP parsing, then file/CRC/metadata/RECORD and notice inventory are checked without
importing, installing or extracting package code. Declared ZIP permissions are
metadata and grant no filesystem authority. A candidate inventory is not native
codec, attestation, license-audit or runtime acceptance; see
`docs/v0.15-media-wheel-artifact-qualification.md`.

`qualification/media_preparation.py` is a separate operator-only diagnostic copy
gate. Strict pinned bytes are inspected before writes to a new private attempt under
this worktree's ignored runtime directory. Every member is streamed and rechecked;
permissions are normalized and a complete independent scanner comparison precedes
a frozen receipt. It imports or executes no package code and creates no Core route.
Four attempts are retained at most; partial failures return no receipt. Read-only
files remain owner-mutable and publisher attribution remains unverified. The combined
preparation gate, immutable native image and runtime qualification are still pending.
See `docs/v0.15-media-file-preparation.md` for filesystem assumptions and bounds.


The optional `scripts/qualify_media_publisher.py` operator proof uses separately
hash-locked private verifier tools and a fixed production root snapshot. It verifies
one exact wheel subject and four fixed signer claims offline; this is historical
attribution under that root, without current TUF freshness. Negative controls require
a successful unchanged baseline and specific refusal categories. The launcher checks
network namespace/interface/route evidence and holds descriptors until cancellation
cleanup settles. No Pillow code or Media runtime/Core route is activated. Tool setup,
resource limits and pending acceptance gates are documented in
[Media publisher qualification](docs/v0.15-media-publisher-qualification.md).


Publisher qualification #76 passed its exact full CI and23 actual offline cases;
see the [final gates](docs/reviews/media-publisher-final-gates.md). The next optional
`qualification/media_image.py` prerequisite seals the closed wheel's complete
normalized file image directly from inspected bytes. Its managed owner separates
stable identity from live descriptor ownership, refuses concurrent consumption,
and retains descriptors on uncertain cleanup. The fixed scanner-copy launcher
imports no copied package code and adds no Core route or dependency. Native copy
acceptance remains pending; see [image scope](docs/v0.15-media-image-qualification.md).


## Offline Media member inventory contract (v0.15 development)

`qualification/media_member_contract.py` checks synthetic/future reader metadata
against caller-owned archive/run/policy bindings and process outcomes. It rejects
unsafe member names, duplicate and non-directory ancestor conflicts, malformed
fields, impossible sizes, output budgets and failed/cancelled cleanup even when
an upstream process reports exit zero. Results are immutable metadata only.

This is **not** an archive parser, extractor, authenticated inventory, activated
Media Worker or runtime permission. No package/native/image code is imported.
The eventual qualified owner must independently establish its observations;
child JSON is never trusted to grant cleanup or execution authority. Link targets
stay inert and unresolved. See [the contract](docs/v0.15-media-member-contract.md).

Run the fake-only checks with:

```bash
.venv/bin/python -m pytest -q tests/test_media_member_contract.py
```

No native setup command, new dependency or enabled setting accompanies this gate.


## Restricted USTAR inventory adapter (v0.15 development)

`qualification/media_tar_inventory.py` inventories an already uncompressed
POSIX USTAR stream using fixed-size stdlib header decoding. It incrementally
hashes regular files and the complete bounded stream; links are inert metadata.
It rejects unsafe names, duplicate/non-directory ancestors, bad checksums,
padding, termination, unsupported extensions and resource/output-limit excess.
It does not extract, decompress or launch a native process. GNU/PAX/legacy/sparse
archives are deliberately unsupported by this initial policy.

`ParsedTar` does not assert archive provenance, native cleanup or runtime authority.
A separately qualified owner and parser runtime are required before live package
use. This is not wired into a Core route or enabled by configuration.
See [design and gates](docs/v0.15-media-ustar-inventory.md).

```sh
.venv/bin/python -m pytest -q tests/test_media_tar_inventory.py
```


## Isolated USTAR synthetic qualification (v0.15 development)

The opt-in `scripts/qualify_media_tar_isolation.py` harness tests18 fixed synthetic
cases in the existing no-network/read-only Python namespace. It makes pinned
source copies, verifies their hashes before imports and requires owned process
cleanup for every accepted result. It is not a general package parser or a Core
route; actual .deb processing, full runtime provenance and image decoding remain
separate. Do not run its optional `--run` without the recorded source clearance.

Offline tests:

```sh
.venv/bin/python -m pytest -q tests/test_media_tar_isolation.py
```

See [design, limits and proof gates](docs/v0.15-media-ustar-isolation.md).


## Reader-bootstrap dependency declarations (v0.15 development)

`qualification/media_reader_bootstrap.py` checks a fixed copied-runtime lock and
cross-file ELF dependency/version declarations. `scripts/qualify_media_reader_elf.py`
reuses the existing qualified metadata guest for32 controls plus8 inert input
files; it shares the original four-attempt version-proof scope. It never executes
dpkg, its loader or a package member. A declared match is not ABI compatibility,
actual dynamic loading or independently authenticated platform provenance.
Only frozen-source clearance permits the opt-in native metadata run.

```sh
.venv/bin/python -m pytest -q tests/test_media_reader_bootstrap.py
```

See [bootstrap design and gates](docs/v0.15-media-reader-bootstrap.md).


## Native Media reader container contract (v0.15 development)

`qualification/media_reader_container.py` is a pure configuration validator for
the **future synthetic** native package-reader gate. It binds the accepted PR #91
ELF proof/lock to a content-addressed image and requires an exact fixed loader
command, fail-closed environment, `runc`, no network, read-only root, non-root
identity, no capabilities, no-new-privileges, 512 MiB memory/no extra swap,
PID limit 8, half CPU and nofile 32. It rejects mounts, devices, ports, DNS,
sysctls, extra groups, tmpfs and other authority widening.

PR #92 merged the offline boundary after 4,505 CI passes/two skips. The
current development branch adds the separate one-shot synthetic operator:
lease-labelled image ownership, private scratch build context, stopped image-export
attestation, pre-stdin cgroup-v2 verification, and four terminal refusal controls.
No acquired archive is eligible.

The operator source is intentionally opt-in and **must not be run** until its own
frozen-source review and matching full CI clear one attempt. Its current fake/offline
suite passes 164 owner/setup/cgroup/operator checks; the broader Media/context
regression passes 2,743 tests. A later accepted synthetic run still will not grant
real-package, image-decoder or Core runtime authority.

```sh
.venv/bin/python -m pytest -q \
  tests/test_media_native_reader_operator.py \
  tests/test_media_reader_setup.py \
  tests/test_media_reader_cgroup.py \
  tests/test_media_reader_container.py \
  tests/test_media_reader_process.py
```

The native proof ledger now retains **four refused synthetic attempts**. All
four authorized attempts are consumed; none produced a native success receipt.
Attempt 1 refused at lease-preflight and attempts 2–4 refused during Docker build.
Do not retry, reset, delete or reclassify those records. The v2 append-only grant
authorized only attempt 4 and is consumed; no fifth proof attempt is authorized.

A separate build-only diagnostic succeeded without touching the proof ledger.
The successful diagnostic and qualifier were confirmed to use the same retained
bundle/Dockerfile bytes and network-none/pull-false build flags. Investigation
found that the diagnostic supplied a fixed PATH/HOME/LANG/DOCKER_CONFIG while
qualification supplied only DOCKER_CONFIG before GOMAXPROCS=2. The current code
uses one exact fail-closed Docker environment for readiness, diagnostic and
qualification. This is a plausible cause of the build discrepancy, not proven
causation, and it does not create a new proof budget.

Current correction validation passes 52 focused environment/diagnostic tests,
412 native-reader/recovery tests and 2,855 Media/context tests. PR #93 still needs
matching full CI and final review and remains a failed qualification record until
then. Real-package, decoder and Core runtime authority remain disabled.

See [the offline contract and next gates](docs/v0.15-media-native-reader-container.md).
