# Zelo — Canonical Architecture and Roadmap to v1.0

## Status

This document is the repository-facing roadmap summary.

The current canonical product plan is Zelo Canonical Architecture revision 3.3,
as amended by `docs/zelo-workspace-decision.md` and
`docs/zelo-organizational-authority-model.md`. The later explicit decisions win
where they refine older shorthand.

Feature design documents may refine an individual milestone, but they must not
silently redefine Zelo's identity or reorder the roadmap. Material roadmap or
architecture changes require an explicit operator decision.

This repository summary is governed by Canonical Architecture revision 3.3.
Revision 3.3 incorporates the 5 October 2026 persistent-specialist decision:
factory-managed agents are durable digital employees with their own instructions,
memory namespace, skill bindings/learning policy, Space grants, tool grants and
Core authority; execution-capable agents may also own a persistent isolated
computer and corresponding Control Desk Computer Panel. The initial computer
backend may reuse the MIT-licensed CopilotKit/OpenBot infrastructure through a
Zelo-owned adapter, pinned and fail-closed, without surrendering Zelo Core
authority.

Revision 3.2 incorporated the 1 October 2026 organizational authority decision:
the human Managing Director is the ultimate human principal, Zelo is Group CEO,
and factory-managed authority descends through VP, Director, Manager and
Specialist levels. `docs/zelo-organizational-authority-model.md` is canonical for
that hierarchy.

The product model is:

> Zelo is Ahmad's persistent personal AI. Zelo Mind reasons, remembers, discusses,
> plans, delegates, verifies and follows up. Zelo Core is the trusted authority and
> execution boundary. Human authority starts with the Managing Director; Zelo serves
> as Group CEO; factory-managed VPs, Directors, Managers and Specialist Agents operate
> beneath Zelo, with interchangeable workers and tools below the agent layer.

The permanent rule remains:

> Models think. Agents specialize. Workers work. Tools act. Zelo decides.

## Zelo's identity

Zelo is the permanent personal AI and executive identity Ahmad interacts with.
Zelo Mind is the central reasoning and conversational AI. Zelo Core owns trusted
policy, approvals, task state, grants, budgets, audit and execution authority.

Zelo is not a model vendor, coding CLI, swarm framework, gateway, plugin
ecosystem, workspace UI, or third-party repository.

Permanent rule:

> Models think. Agents specialize. Workers work. Tools act. Zelo decides.

Zelo owns identity, goals, projects, routing, permissions, approvals, secrets,
memory boundaries, scheduling, auditing, budgets, validation, learning, and
system-level decisions.

Everything beneath those boundaries is replaceable.

## Core concepts

### Zelo

The overall persistent personal AI and product identity Ahmad interacts with.
Zelo remains the same across model changes, worker replacements, workspaces and
communication channels.

### Zelo Mind

The central reasoning and conversational AI. It understands requests, recalls
permitted context, discusses ideas, plans, delegates and evaluates results. Mind
uses replaceable models through Model Gateway; it does not own execution
authority.

### Zelo Core

The trusted authority boundary. Core owns identity mapping, goals/projects,
tasks, approvals, grants, policy, secrets references, budgets, audit and
authoritative operational state.

### Company runtime

The durable AI organization operating under Core authority.

Canonical reporting hierarchy:

```text
Human Managing Director
  -> Zelo Group CEO
    -> VP
      -> Director
        -> Manager
          -> Specialist Agent
            -> Worker
              -> Tool
```

The Managing Director and Zelo CEO are reserved principals, not ordinary
factory-created agents. VPs, Directors, Managers and Specialists are persistent
factory-managed roles/instances. Divisions, departments and teams are
organizational containers, not authority levels.

The organizational reporting graph is separate from the temporary task
delegation graph. A delegation never silently changes the org chart.

### Model / brain

A replaceable reasoning model such as GPT, Claude, Gemini, Grok, Qwen, DeepSeek,
or another provider/model.

### Agent

A persistent specialist digital employee. Every persistent factory-managed Agent
has a stable identity and organizational position plus an explicit package of:

- trusted instructions / mission / responsibilities;
- scoped memory namespace(s);
- installed/versioned skills and a skill-learning policy;
- Space/project/document/data grants;
- tool and capability grants;
- Core authority, budgets, approval and escalation boundaries; and
- an optional persistent Agent Computer when execution requires one.

Execution-capable Agents should normally receive their own computer rather than
sharing browser identity or mutable workspace with unrelated Agents. An Agent
Computer never grants authority by itself.

Examples include Coding Agent, Research Agent, Business Agent, YouTube Agent,
Trading Agent, Product Agent, and Security Agent.

An agent is not a model or worker.

### Agent Computer

A persistent, isolated execution environment assigned to one AgentInstance when
needed. It may contain a browser/profile, private workspace files and bounded
shell/runtime facilities. Computer state may survive stop/start, but its
credentials, lifecycle and actions remain scoped to the owning AgentInstance and
mediated by Zelo Core.

The initial v0.15 backend may reuse a pinned CopilotKit/OpenBot computer service
and supervisor behind a Zelo-owned adapter. Failure or unavailability must fail
closed: Zelo must never silently fall back to the host shell, host browser or
another Agent's workspace.

Human takeover is an owner control, not Agent authority. After takeover/handback,
stale browser snapshots/actions must not be reused.

### Worker

A replaceable execution harness used for bounded work.

Examples include Codex, Claude Code, OpenCode, Ox, browser workers, computer
workers, Python workers, and API workers.

### Tool

A narrow typed capability owned or mediated by Zelo.

### Skill

A reusable procedure, playbook, verification method, or domain workflow.

### Backend

An optional subsystem that Zelo may delegate to without surrendering control.

### Channel

A communication surface such as web, mobile, voice, WhatsApp, Telegram, Slack,
Discord, email, or phone.

### Memory

Zelo-owned persistent context and experience, separated by identity and project
boundaries.

### Self-improvement

Controlled system evolution using the same Zelo-owned planning, authority,
worker, validation, review, approval, and audit boundaries as other work.

## Canonical architecture

```text
                            AHMAD
                    goals / direction / decisions
                              |
                              v
        +---------------------------------------------+
        |          INTERACTION / PRESENCE             |
        | Talk to Zelo / workspace / voice / channels|
        +----------------------+----------------------+
                               |
                               v
        +---------------------------------------------+
        |                 ZELO MIND                   |
        | understand / recall / reason / plan         |
        | discuss / delegate / evaluate / respond     |
        +-----------+-------------------+-------------+
                    |                   |
                    v                   v
             Model Gateway        Context services
          replaceable models   memory / state / files
                    \                   /
                     \                 /
                      v               v
        +---------------------------------------------+
        |                 ZELO CORE                   |
        | trusted execution authority                |
        | policy / grants / tasks / approvals        |
        | budgets / secrets refs / events / audit    |
        +----------------------+----------------------+
                               |
                               v
        +---------------------------------------------+
        |          COMPANY + AGENT RUNTIME            |
        | Managing Director -> Zelo CEO               |
        | VP -> Director -> Manager -> Specialist     |
        | org units / Agent Factory / Scheduler       |
        +----------------------+----------------------+
                               |
                               v
        +---------------------------------------------+
        |        WORKERS + TOOL GATEWAYS              |
        | Codex / Claude Code / OpenCode / Ox         |
        | browser / research / computer / API / media |
        | optional swarm backends under Supervisor    |
        +----------------------+----------------------+
                               |
                               v
        +---------------------------------------------+
        |          VERIFICATION + RESULTS             |
        | evidence / reviews / artifacts / learning   |
        +----------------------+----------------------+
                               |
                               +----> Zelo Mind / workspace

Shared data services provide authorized StateStore, MemoryStore, ArtifactStore,
event/audit journal, cost records and operational metrics to the layers that
need them. The UI, model and worker layers never become the authority boundary.
```

## Non-negotiable architecture rules

1. Zelo is permanent; models, workers, frameworks, and vendors are replaceable.
2. Agents and workers are different concepts.
3. Models never become the authority boundary.
4. Zelo owns worker selection and capability routing.
5. Zelo owns approval and permission decisions.
6. External repositories must sit behind Zelo-owned interfaces.
7. Worker credentials never imply Zelo authority.
8. Specialist agents receive only scoped capabilities.
9. Promotion, merge, release, money movement, destructive actions, and other
   high-risk external effects retain explicit policy/approval boundaries.
10. Self-improvement uses the same controls as any other change.
11. Hidden reasoning is not persisted as system memory or audit data.
12. Architecture may borrow code aggressively without surrendering control-plane
    ownership.
13. The human Managing Director and Zelo CEO are reserved principals and cannot be
    created, replaced or impersonated by Agent Factory.
14. Organizational reporting and temporary task delegation are separate graphs;
    neither may silently mutate the other.
15. Titles do not grant tool/worker authority. Every action still requires explicit
    Core capabilities, policy, budgets and approvals.
16. Persistent Agent identity includes explicit instructions, memory, skills,
    Spaces, tools and authority; none of those scopes may be inferred from title.
17. Agent Computers are per-AgentInstance execution resources, not a shared
    authority boundary; cross-agent workspace/browser access requires explicit Core grants.
18. A failed/unconfigured Agent Computer never falls back to the Zelo host.
19. Human takeover is owner-only and handback requires fresh browser state before
    autonomous browser actions continue.
20. Learning may propose or improve skills, but learned skills never widen tool,
    capability, Space, budget or external-action authority automatically.
21. Mutation-capable Agent Computers have no direct public egress. Public browser
    traffic must cross a Zelo Core-owned, per-Agent authenticated allowlist proxy;
    Core computer control may use only a validated private address inside the exact
    configured internal computer network.

## Reuse before rebuild

Zelo should not waste time recreating mature open-source systems.

For every new capability:

```text
roadmap need
   |
   v
search existing projects
   |
   v
inspect architecture + code + license + security
   |
   +--> wrap as backend
   +--> vendor focused component
   +--> fork and maintain
   +--> borrow pattern and reimplement
   +--> reject
```

### Rules for code reuse

Before copying or vendoring code:

- identify the exact capability being reused;
- verify the repository license and preserve required notices/attribution;
- review dependency and supply-chain impact;
- review network, filesystem, credential, subprocess, and update behavior;
- prefer a narrow adapter over importing an entire framework;
- pin versions or commits where execution semantics matter;
- keep third-party code distinguishable from Zelo-owned code;
- add regression/security tests around the Zelo boundary;
- never let borrowed code bypass Zelo permissions, approvals, audit, routing,
  secrets, or memory ownership;
- maintain a provenance record when code is copied or materially adapted.

The goal is:

> Own the architecture, not every line of code.

## Placement of reference projects

These projects are references or optional components, not Zelo itself.

| Project / idea | Canonical place |
| --- | --- |
| Mission Control | Strong workspace/control-desk candidate and pattern library; not mandatory |
| Agent Town | Company-world/visual-office reference or adaptation candidate |
| Ruflo | Optional swarm / coordinated execution backend |
| ECC | Engineering skills, hooks, verification and worker procedures |
| Harness | Agent Factory / agent-team design reference |
| CopilotKit/OpenDots | Specialist-agent workspace, Computer Panel, per-agent computer and learning UX/reference; selectively adapt compatible code only |
| CopilotKit/OpenBot | Pinned candidate backend for per-agent persistent browser/files/shell/takeover; always behind Zelo Core/Supervisor authority |
| OpenClaw | Persistence/autonomy reference; possible later channel/device gateway adapter |
| OpenMAIC | Durable sessions, runtime and resumability reference |
| Context Mode | Context Engine / memory handling reference |
| Open Code Review | Coding Agent reviewer capability |
| Archify | Architecture analysis capability |
| God's Eye View | Operational visualization reference for tasks, agents and costs |
| Supabase | Optional future data backend behind Zelo storage interfaces |
| Skales / Omarchy | UX/channel reference / machine-environment reference |
| FreeLLMAPI | Candidate model-provider source subject to provenance/privacy/reliability review |
| HyperFrames | Later media/video capability candidate |
| Ox | Replaceable coding worker |
| Claude Code | Replaceable coding worker |
| OpenCode | Provider-neutral coding worker/harness |
| Codex | Replaceable coding worker |
| Ollama | Local model provider |
| GPT / Claude / Gemini / Grok / Qwen / DeepSeek | Replaceable brains |

A newly discovered repository must first be mapped to a box in the canonical
architecture. Zelo must not be reshaped around whichever repository was most
recently discovered.

## Workspace and conversational direction

The workspace is one product with three connected views:

1. **Talk to Zelo** — always-accessible text and voice conversation for ideas,
   goals, decisions and updates. Linked channels use the same Zelo identity and
   relevant conversation/project context.
2. **Company world** — an optional visual office/town showing departments,
   agents, assignments, progress, evidence and outputs. The visual layer reflects
   real Core state and never invents work.
3. **Control desk** — ordinary project, file, policy, procedure, approval,
   budget and activity management, including a per-agent **Computer Panel** for
   Agents that own persistent computers.

A Computer Panel is a projection over Core/backend state and should expose, as
authorized: Screen, Browser, Files, Terminal, Activity, Permissions/Resources,
Start/Stop, Pause Agent, Take Control and Hand Back. It must not create a second
permission or audit system. The eventual UI may selectively adapt compatible
OpenDots/OpenBot components, but all actions still resolve through Zelo-owned
identity and authority.

Mission Control is a strong candidate and reference, not a mandatory dependency.
Agent Town is a reference or potential adaptation candidate. Build Zelo's own
workspace if that produces the better experience. Reuse compatible components
only after license, provenance, security and integration review.

Every essential action must remain available through conversation or normal
controls. The visual world is optional. All views share canonical Core state and
authority.

Before expanding the visual world, prove one complete flow:

```text
talk to Zelo
  -> delegate bounded work
  -> see real progress
  -> inspect evidence
  -> open the verified result
```

## Canonical roadmap

### v0.10 — Multi-worker foundation — released

Established:

- durable tasks, attempts, approvals and audit;
- Model Gateway;
- Tool Gateway;
- bounded Agent Runtime;
- Developer Runtime;
- Worker Registry and Supervisor;
- enforceable worker authority;
- Codex, Claude Code and OpenCode adapters;
- independent review;
- trusted validation;
- promotion, merge and release boundaries;
- recovery and concurrency controls.

### v0.11 — Self-improvement seed — released

Goal: Zelo originates one bounded improvement to itself.

```text
inspect
  -> propose one improvement
  -> policy check
  -> prepare approval-bound developer work
  -> Supervisor selects worker
  -> implement
  -> validate
  -> independent review
  -> evaluate
  -> stop
```

v0.11 does not autonomously merge, tag, release or deploy itself.

### Early workspace increment — Core proof completed; workspace foundation active

The Core prerequisite is complete: persistent Zelo Mind conversation can answer
directly, request one typed Core action, observe the authoritative task result and
use that result in a later turn.

The selected implementation direction is now a Zelo-owned workspace with
Mission-Control-derived Control Desk UX/components and a later
Agent-Town-derived Company World. The workspace remains a projection over Core,
not a second authority system.

This is a prerequisite slice on the path to v0.12, not a separate release and
not permission to create a parallel task/approval authority system.

### v0.12 — Durable goals and first useful campaign

Add:

- GoalSpec and project/campaign ownership;
- campaign/task dependencies;
- operational StateStore;
- durable events;
- leases and checkpoints;
- shared budget accounting;
- cancellation and recovery;
- limited scheduling and bounded Mind wake cycles;
- workspace project/progress views;
- push-to-talk voice with spoken replies using the same conversation/action
  contracts;
- one fixed Research specialist with a narrow read-only retrieval capability;
- reuse of the existing Coding runtime for approved implementation tasks.

One self-improvement cycle becomes a bounded campaign. The scheduler may repeat
the approved v0.11 pipeline inside a campaign grant, but merge and release remain
separately authorized.

### v0.13 — Memory, context and evidence

Add:

- scoped personal, project and agent memory;
- provenance, expiry, correction and supersession;
- decisions and experiences separated from operational state;
- bounded context assembly;
- baseline evaluation cases;
- worker/model performance and cost evidence;
- storage abstraction for later Postgres/Supabase/vector backends.

### v0.14 — Agent Factory

Generalize the fixed Coding and Research specialists into versioned `AgentSpec`
templates, persisted AgentInstances, durable organization units/assignments and
bounded delegation/execution contracts.

Canonical authority is:

```text
Human Managing Director
  -> Zelo Group CEO
    -> VP
      -> Director
        -> Manager
          -> Specialist Agent
            -> Worker
              -> Tool
```

The Managing Director and Zelo CEO are reserved principals outside ordinary
Agent Factory creation. Factory-managed organizational levels are `vp`,
`director`, `manager` and `specialist`.

Divisions, departments and teams are organizational containers. The durable
reporting graph is separate from the temporary delegation graph.

v0.14 adds:

- trusted/versioned AgentSpec registry and activation checks;
- persisted project-bound AgentInstances and scoped agent/project memory;
- explicit bounded parent-child task delegation contracts;
- exact agent/delegation execution identity on Core tasks;
- organizational role, unit and reporting-assignment contracts;
- hierarchy-aware bounded child-agent creation;
- deterministic activation/evaluation gates before new executive/specialist
  templates become active;
- lifecycle, budgets, depth, cycle and audit enforcement.

Agents bind roles, goals, memory namespaces, capabilities, tools, permissions,
procedures, worker requirements, review requirements, budgets and lifecycle.
Titles never widen tool/worker authority by themselves.

Inspect the parked Agent Factory experiment and reuse only compatible parts; do
not merge it wholesale.

### v0.15 — Capability and skills ecosystem

Core-first checkpoint (8 October 2026): merged PRs #93, #98, #99, #100,
including #102, #103, #104, #105, #106 and #107. PR #107 adds the
new-proposal admission guard. It refuses a brand-new learned procedure
revision at or below the latest promoted version for the same
AgentInstance, source Skill and procedure. Stored proposal retries remain
idempotent, and historical reviews/proof links remain valid.

PR #107 is the merged **promoted-head admission** gate at b3e665ab:
Core refuses a brand-new learned skill proposal at or below the latest
promoted version for the same AgentInstance/Skill/procedure. Historical
identical proposal retries stay idempotent and accepted reviews survive.

PR #110 is the unmerged **local promotion-link recording exclusivity** gate.
It serializes verified head checks before storing new promotion proof;
previously accepted competing heads cannot both acquire local links.
Historical divergent heads still cause the resolver to refuse ambiguity.
This does NOT implement an external GitHub pre-merge publication lock:
a separate trusted learned-skill identity and promotion-time exclusivity
gate are required before merging a competing external PR.

Merged PR #108 starts non-authoritative v0.16 channel contracts; merged PR #109
(bd586aa7) adds operator-selected opaque bindings and unverified replay metadata.
These are not runtime skill activation, provider authentication or new
AgentSpec/worker/tool authority. The four previously consumed native-reader
qualification attempts remain preserved as refusals. UI PR #61 stays parked.

Earlier PR #107 admission/ambiguity tests passed 3 focused and 177
integrated cases. PR #110 local validation passed 108 skill-learning
regressions, an HTTP conflict test, a concurrent writer test and five
context tests before the new main integration. Exact-head CI remains
required for the updated PR #110.

**Latest delivery decision, 2 October 2026:** Core first. The user reports that
the UI needs major rework and has parked UI implementation until the end of the
Core work. Preserve draft PR #61; its earlier early-beta/U2 priority is superseded.
Continue Media Worker completion and the existing Core milestones below.
The v0.16 channels, v0.17 specialist organizations and v0.18 autonomy order stays
unchanged; UI redesign and final integration follow the Core-first work.


Add controlled:

- Browser Worker;
- Computer Worker;
- Python Worker;
- API Worker;
- Shell Worker;
- Research Worker;
- Media Worker;
- Skill Registry;
- Capability Registry;
- selected MCP/plugin adapters;
- optional Ruflo swarm adapter.

For Computer Worker infrastructure, prefer reuse over rebuilding container and
browser lifecycle from zero. The first candidate is the exact OpenBot revision
already consumed by OpenDots, wrapped by a Zelo-owned fail-closed adapter. The
foundation must provide per-AgentInstance identity, persistent browser/workspace
volumes, derived per-agent credentials, start/stop lifecycle and human-takeover
semantics without exposing the Docker socket or master token to Agent computers.
Actual browser/file/shell actions remain separate capability/authority gates.

Computer Worker rollout is explicitly phased:

1. backend foundation and persistent per-AgentInstance identity;
2. Core-owned persisted permissions, lifecycle, audit and human takeover;
3. Agent-only **read-only observation** (`browser_snapshot`, `browser_read`,
   `files_list`, `files_read`) with exact permission/backend execution
   identity and claim-time revocation;
4. Core-owned browser egress and internal-network topology: no direct public
   computer egress, per-Agent proxy credentials, trusted HTTPS endpoint grants,
   private-CIDR Core control and live revocation;
5. first browser mutation: **named-endpoint navigation only**, using a separate
   `computer_navigate` Worker/authority, trusted HTTPS endpoint ID + bounded
   path, exact endpoint/egress/permission/backend binding and claim/runtime
   revalidation;
6. separately qualified interactive browser mutation actions such as
   click/type/key/scroll, each behind explicit policy rather than inherited from
   navigation;
7. separately qualified workspace writes; and
8. shell only through an explicit later authority decision.

Passing an earlier phase never implicitly grants a later phase. Screenshots may
be owner-facing through the Computer Panel without automatically becoming
Agent/model context.

Integrate useful ECC, Open Code Review, Archify and similar patterns behind
Zelo-owned interfaces.

REA is a selected **candidate** MCP/CLI integration for this v0.15
ecosystem, not an authorized execution backend. Its first Core slice **declares**
a draft `reverse_engineering` Specialist Agent, draft capability and
`rea_investigation` skill in a separate operator-visible candidate catalog,
plus a version-pinned, disabled manifest. Existing live Agent, Capability and
Skill registries and their execution-binding fingerprints remain unchanged.
It has no Worker, task, execution tool, persistent computer, external setup,
target/process access, network/credential grant, or skill activation. The
dedicated security/qualification sequence is in
[REA integration](v0.15-rea-integration.md). The v0.15 promotion/publication
gates and prior Core priorities still precede REA runtime activation; neither
v0.16 channels nor v0.17/0.18 milestones are reordered.

### v0.16 — Channels and presence

Merged PR #108 (bc6f50c, 4,965 tests passed and 2 skipped) provides the
contract-only v0.16 foundation: transport-neutral, **non-authoritative**
channel text envelopes,
keyed opaque provider/thread/message identities, and reserved channel turn
idempotency keys. This foundation has no provider credentials or connected
transports. A separately qualified adapter must authenticate provider origin,
bind Ahmad-approved accounts to existing Mind conversations, persist inbound
replay/edits and outbound delivery receipts, and enforce send approval before
any WhatsApp/email/voice action is enabled. Provider text is always untrusted
data, never permission to execute a Core task. Live calling remains a separate
authority and transport qualification.

PR #109 is the separate draft Core binding/observation ledger, based on
merged PR #108: an Ahmad-selected opaque thread may map to an existing
Mind conversation, but that
mapping is not provider authentication. Only message ID, text digest and UTC
metadata are retained with append-only replay checks; no external webhook,
message-to-Mind admission, delivery receipt or send authority is granted.
Integrated migration/channel/Mind/scheduler tests passed 266 cases locally
before the current concurrent-replay regression. Full exact-head CI remains
required. Provider verification and outbound approval are later gates.

Deliver staged conversational presence through the shared Zelo conversation and
authority contracts:

- WhatsApp text and supported voice notes;
- email with thread continuity and delivery state;
- live browser voice with interruption/reconnect;
- a separately qualified call transport;
- additional channels such as Telegram, Slack or Discord when useful.

OpenClaw or another adapter may be evaluated only against the shared Zelo
contract. Messaging support does not imply calling support. If continuous
presence is required while the laptop is offline, choose and verify an always-on
host deliberately.

### v0.17 — Real specialist organizations and agents

Add real domain organizations under the canonical executive hierarchy. Examples
may include Business, Media/YouTube, Trading, Product, Security and Personal
domains.

Each organization can contain the required VP, Director and Manager layers plus
narrow Specialist Agents such as:

- Business / market / sourcing specialists;
- YouTube research / strategy / script / thumbnail / analytics specialists;
- Trading research / strategy / risk / backtesting / reporting specialists;
- Marketing specialists;
- Product specialists;
- Security specialists;
- Personal-assistant specialists.

Executive roles coordinate and govern; Specialist Agents perform domain work.
Every persistent Agent receives a trusted package containing its instructions,
memory namespace, explicit installed skills, Space grants, tool/capability grants,
authority/budget/escalation boundaries and learning policy. Agents that require
interactive execution may receive a persistent per-AgentInstance computer with its
own browser profile and workspace; unrelated Agents do not share those mutable
identities by default.

Every role receives only the tools, memory and authority explicitly required by
its trusted specification and organizational assignment.

### v0.18 — Learning, self-healing and advanced autonomy

Add:

- health monitoring;
- provider/worker failure diagnosis;
- automatic bounded recovery;
- strategy/outcome learning;
- evidence-based model/worker routing;
- failed-strategy avoidance;
- deeper self-improvement;
- skill improvement;
- evidence/outcome-driven skill candidates;
- evaluation before skill promotion;
- operator/policy approval before publication where required;
- versioned skill publication, performance evidence and rollback; and
- skill scopes for agent, organizational unit, project and system use.

Canonical skill-learning flow:

```text
conversation / task evidence / outcomes
  -> detect repeatable successful procedure
  -> candidate skill
  -> bounded evaluation
  -> proposal
  -> approval / policy gate
  -> versioned publication
  -> measured use
  -> improve or roll back
```

Learning never self-grants new tools, Spaces, credentials, budgets or external
write authority. A learned skill is procedure knowledge; capability authority
continues to come only from trusted Core configuration.

### v0.19 — Release candidate / hardening

Focus on reliability rather than large new features:

- long-running operation;
- crash recovery;
- backups;
- memory consistency;
- security review;
- adversarial testing;
- cost/resource accounting;
- runaway-agent protections;
- permission review;
- worker failure recovery;
- channel authentication;
- system observability.

### v1.0 — Minimum functioning autonomous Zelo

v1.0 is not reached because an architecture layer exists.

v1.0 means Zelo can practically:

- receive a broad goal from the operator;
- create and track a project;
- recall relevant context;
- delegate research and specialist work;
- choose brains, agents, workers and tools;
- ask for important decisions;
- build and verify artifacts;
- continue bounded work over time;
- remember outcomes;
- recover from ordinary failures;
- report progress through useful channels;
- preserve one Zelo identity across Mission Control/custom workspace, voice,
  WhatsApp and email;
- expose operator controls, budgets and recovery state;
- let the operator inspect/control persistent Agent computers where configured;
- retain and improve useful Agent skills through governed learning; and
- improve itself through controlled engineering loops.

Example target:

```text
Operator:
"Find a viable UAE business opportunity and start working on it.
Keep me updated and ask before important decisions."

Zelo
 -> creates project
 -> recalls context
 -> Research Agent investigates
 -> Business Agent evaluates
 -> operator approves direction
 -> Coding Agent builds MVP
 -> workers implement
 -> verifier tests
 -> reviewer checks
 -> scheduler continues
 -> memory records outcomes
 -> Zelo reports progress
```

The self-improvement target at v1.0 is similarly practical:

```text
Operator: "Improve yourself."

Zelo
 -> inspects itself
 -> uses past outcome memory
 -> finds a weakness
 -> plans one bounded improvement
 -> Coding Agent coordinates implementation
 -> worker executes
 -> validation and review run
 -> Zelo records the outcome
 -> next cycle occurs only within policy/budget
```

## Live project status

Live implementation status is maintained separately in
`docs/zelo-project-status.md` so architecture and roadmap documents do not
become stale.

Current verified baseline as of 2 October 2026:

- Zelo Core v0.14.0 — Agent Factory is published from
  `3b87bbe4dc9956fbb8b11108faa911c4b23de5c9` with tag `v0.14.0`;
- v0.14 implementation PRs #47-#53 and release-prep PR #54 are merged;
- the post-merge release-commit CI run #36913099766 passed;
- the canonical authority chain is Managing Director -> Zelo CEO -> VP ->
  Director -> Manager -> Specialist -> Worker -> Tool;
- durable organizational reporting and temporary task delegation remain
  separate graphs, and titles do not grant capability authority;
- the active milestone is v0.15 Capability and skills ecosystem;
- Capability Registry is merged through PR #55 at
  `4dcc5bf03dfe761d64df49f848762a163158d8df`;
- Skill Registry is merged through PR #56 at
  `7e0936750967be79ec49ee5858a508935d7ad3e0`;
- Generic Worker Registry is merged through PR #57 at
  `9d2e897a6c8bebf63fc52dec7b6442c24cc9f8aa`;
- Research Worker is merged through PR #58 at
  `c16f21381a2316435091f35faa82b609534cefa3`;
- Python Worker is merged through PR #59 at
  `c8aab211b52cbc576d8457b61d01134fcab645e5`;
- API Worker is merged through PR #60 at
  `8d33d563070c58c487dd06e91fd9af617744dab3`;
- document broker and owned-DNS qualification prerequisites are merged through
  PR #62 at `939cbd7dc49f7e5e74a7659d13a0c2e92b7f5aef`;
- Browser Core binding is merged through PR #67 at `bc8404e`; operator/default
  DNS/TLS qualification is merged through PR #68 at `025d019`, still disabled;
- Shell boundary/owner proofs PR #69/#70 and Core binding #71 are merged at
  `a33dc9c`/`befaefa`/`fd686a9`; operator/application #72 merged at `159caee`,
  still default disabled and without default Agent/Mind routes;
- API execution is GET-only against operator-configured named HTTPS origins with
  public-IP pinning, no redirects, credentials, custom headers or arbitrary URLs;
- Media PNG contract #73 is merged at `aed13e6`; pinned wheel inventory is next,
  without decoder/runtime activation;
  Computer/Media native workers and selected external adapters remain separate
  unimplemented slices; broader Shell process/filesystem authority is not granted;
  Browser receives no authority from API work;
- Capability/Skill metadata and generic registration never grant execution
  authority by themselves;
- parked experiment `feat/v1.0-agent-factory` remains reference material only
  and must not be merged wholesale.

## Decision rule for new ideas

When a new repository, framework, product, or architecture idea appears, ask:

1. Which canonical Zelo capability does it improve?
2. Is that capability in the current milestone?
3. Can we reuse code rather than rebuild it?
4. Can it live behind a Zelo-owned interface?
5. Does it preserve Zelo authority, security, portability and memory ownership?
6. What is the smallest proof before integration?

If those questions do not have clear answers, study the idea but do not change
the active roadmap around it.


## Workspace direction addendum — 24 September 2026

The current workspace decision refines the earlier Mission Control-only direction.

Zelo's user experience has three connected views:

1. **Talk to Zelo** — the primary text/voice conversation with Zelo Mind for ideas,
   goals, decisions, delegation, progress and follow-up.
2. **Company world** — an optional visual office/town representation of real Core
   state, inspired by Agent Town. It visualizes the actual Managing Director/Zelo
   CEO/VP/Director/Manager/Specialist hierarchy, organizational units, assignments,
   work and outputs; animation must never become execution authority.
3. **Control desk** — practical project, file, policy, procedure, approval, budget,
   artifact and activity management.

All three views share one Zelo identity and one canonical Core state.

Mission Control remains a strong candidate and reference for task, agent, activity,
cost and chat surfaces, but it is **not a mandatory dependency**. Agent Town is a
visual-world reference or adaptation candidate. Zelo may use Mission Control,
Agent Town, a hybrid, or its own workspace if that produces the better experience.

The implementation decision must be made through one working proof:

```text
Talk to Zelo
  -> delegate one bounded task
  -> observe real Core progress
  -> make any required approval
  -> open the verified result
```

Use that proof to choose adaptation versus a custom workspace before expanding the
visual world. The workspace must never become a second task, approval, scheduling,
memory or authority system.

### Early workspace increment

Immediately after v0.11 and on the path to v0.12:

- expose Core tasks, self-improvement proposals, approvals, decisions and results;
- add the first persistent Zelo Mind text conversation;
- support direct discussion, plan refinement and a typed Core action request;
- prove one conversation-to-result path;
- keep Mission Control/Agent Town/custom implementation choice reversible until
  the proof identifies the best fit.

This increment does not replace the v0.12 durable-goals/campaign milestone.

## 2 October 2026 — artifact verification checkpoint

PR #63 merged resource-admission/crash-fencing qualification at `2bb48bb`.
The next bounded Core-only slice is `feat/v0.15-browser-artifact-qualification`:
actual browser/controller artifact inventories, policy/role identity and verified
rescans. These establish change detection, not an execution authority or filesystem
freeze. Native-browser sandbox compatibility and the integrated runtime remain gates.
UI #61 stays parked; v0.16/v0.17/v0.18 ordering is unchanged.

## Native browser qualification checkpoint (after PR #64)

Artifact prerequisites merged at `7cdd791`. Native Chromium now runs in the
controlled no-network resource container under a pinned, explicitly reviewed
qualification policy; this is not a production worker activation. See
`docs/v0.15-browser-sandbox-qualification.md`. The next gate is the combined
broker/renderer/owner lifecycle with verified immutable execution inputs.
UI PR #61 remains parked; canonical later milestones are unchanged.

## Post-native-qualification continuation

PR #65 merged at `e16d370` after exact-head full CI and independent review.
Continue with the combined bounded renderer/broker/resource-owner lifecycle in
`docs/v0.15-browser-renderer-integration.md`. All completed qualification work
remains disconnected from production authority until integration/activation gates
pass. UI PR #61 stays parked, and v0.16/v0.17/v0.18 ordering is unchanged.

## Bounded renderer integration checkpoint — 3 October 2026

The active qualification branch combines approved document fetching, exact prepared
image/controller/profile identity, fixed anonymous-pipe rendering and cleanup-gated
results under one durable admission lease. Ten controlled real host cases passed;
Core Browser Worker registration/task authority is not yet enabled. Keep the
qualification proof separate from activation and preserve UI #61 parked. Details:
`docs/v0.15-browser-renderer-integration.md`.


## Renderer merged; Core task binding next — 3 October 2026

PR #66 merged at `f728df7` from exact reviewed head `ba188c3`; tree equality was
confirmed. Exact-head CI passed 1,538 tests with two host-only installed-Codex
skips, and Astra Medium identified no blocking defect in qualification scope.
The updated renderer/protocol/harness gate passed 121 cases. Ten controlled real
browser/container cases passed; browser HTTP/DNS evidence used fixtures.

Continue on `feat/v0.15-browser-core-binding` using the next binding plan. Complete
operator prerequisites and immutable Core task/worker identity before activation;
merging qualification code does not activate Browser Worker. Published release
stays v0.14.0. UI #61 and canonical later milestones remain parked/unchanged.


## Browser Core binding merged — 3 October 2026

PR #67 merged at `bc8404e3257573701683448255311d79f3d27cdf`; its tree equals
reviewed/tested `0486e15153adaf5983d5341a69cdbf01390ec93e`. Exact-commit full CI
passed 1,617 tests with two installed-Codex skips; both passed separately. The
90-case Core/capability gate and eight controlled real Core-to-Chromium cases
passed. HTTP/DNS were fixtures. Independent Astra Medium combined full static
review and exact-commit follow-ups found no remaining blocker in their scope.

Browser jobs now bind immutable Core task/worker/operator/renderer authority,
retry/claim/Runner validation, bounded untrusted results and cleanup fencing.
File scans run off the event loop and before SQLite writer transactions;
commit checks retain action/configuration/cancellation/Agent authority. Deadlines
include initial Runner validation; pending claims and late/overlapping cleanup
are accounted for. See `docs/reviews/browser-core-binding-final-gates.md`.

Continue on `feat/v0.15-browser-operator-qualification`. Qualify actual default
DNS/TLS/public-document fetch and isolated operator assembly before production
acceptance. No Browser Agent/Mind route or production enablement is implied.
Published release remains v0.14.0; UI #61 and later milestone order are unchanged.


## Qualified Shell Core binding merged — 3 October 2026

PR #71 merged at `fd686a9` with matching tested head `484257b`, exact full CI
(1,792 passed, two supplemented skips), independent Astra Medium full/followup
review and real native/compatibility gates. See
`docs/reviews/shell-core-binding-final-gates.md`.

Continue on `feat/v0.15-shell-operator-qualification`: prove actual application,
authenticated ASGI routes, background Runner and shutdown using unmodified default
native factories and private init-only diagnostic state. No live enablement,
default Agent/Mind route, public listener, model request or broader host surface.
The published release, parked UI and canonical later milestone order stay unchanged.

### Qualified Shell operator/application checkpoint — 3 October 2026

PR #72 merged at `159caee5c5fba2bbc25c3b3f75b5365eebef1fed`, with tested/merged
tree equality and all gates recorded in `reviews/shell-operator-final-gates.md`.
Continue on `feat/v0.15-media-png-contract`: fixed qualification-only PNG envelope,
immutable request binding and candidate evidence; no decoding, installation, Core
route or native acceptance. Follow `v0.15-media-png-contract-qualification.md`.

### Media PNG contract checkpoint — 3 October 2026

PR #73 merged at `aed13e6410db099fa0ad6772feb1b37c1f70baff` with all gates and
tree equality; see `reviews/media-png-contract-final-gates.md`. Continue on
`feat/v0.15-media-wheel-artifact-qualification`, following its bounded artifact
plan. Native Media attachment/decoder/resources/ownership and Core binding remain
separate gates; no installation or runtime activation is implied.

### Media wheel inventory checkpoint — 3 October 2026

PR #74 merged at `b17775aea037ced13baf29b5a5b15b3fc2f2113a` with all exact gates
and tree equality; see `reviews/media-wheel-artifact-final-gates.md`. Continue on
`feat/v0.15-media-file-preparation` following its closed operator-only normalized
copy plan. Publisher verification remains a separate prerequisite for combined
preparation; native execution and Core binding remain later gates.

### Media private file preparation checkpoint — 3 October 2026

PR #75 merged at `5f6c678856901638f407cc15883d094aa40b2585` with matching reviewed/
tested tree and all gates; see `reviews/media-file-preparation-final-gates.md`.
Continue `feat/v0.15-media-publisher-qualification` using its phased, private,
mature-tool pinned-root attribution plan. Prepared files remain mutable and
attribution-unverified; no native execution or Core activation is implied.


### Media publisher attribution checkpoint — 3 October 2026

Publisher PR#76 merged at c83af6c with exact matching reviewed/tested tree,2,475 CI
passes/two supplemented skips,658 focused cases,23 actual offline cases,four
standalone supplements and two intended mutation failures. Conditional historical
attribution under the fixed production root is qualified; current TUF freshness,
mutable prepared-tree attribution and native Media execution remain separate.
Continue qualification-only immutable per-file image sealing and trusted guest-copy
proof, without Pillow imports/decoder/Core binding. See
[image qualification scope](v0.15-media-image-qualification.md).


### Media immutable file-image checkpoint — 3 October 2026

PR #77 merged at `3381ada6` with the exact reviewed/tested tree. Full CI passes
2,596 tests with two supplemented skips. The managed per-file sealed image and fixed
read-only scanner prove the complete 140-file normalized closure, denied mutations,
resource controls and ownership cleanup without importing Pillow. This remains file
qualification, not decoder behavior, publisher inheritance or Core authority.
See [final gates](reviews/media-image-final-gates.md). Continue with a pure fixed
Media syscall candidate and concrete installer/resource prerequisites; original
RLIMIT/cgroup/seccomp gates remain ahead of decoder execution. UI remains parked.


### Media pure syscall candidate checkpoint — 3 October 2026

PR #78 merged at `0d3ecec5` with matching reviewed/tested tree, 2,751 CI passes,
155 new policy tests and all standalone/fault supplements. Both PR #77 and #78
post-merge CI are successful. The immutable 40-call artifact grants no enforcement,
native ABI, decoder or runtime authority. See [final gates](reviews/media-syscall-final-gates.md).
Continue with [platform metadata prerequisites](v0.15-media-platform-metadata.md)
before an exact inert platform lock/materializer; original resource/filter gates
remain before decoder qualification. Core first; UI stays parked.


### Media inert ELF tool-source checkpoint — 3 October 2026

PR79 merged ata8159ec3 with matching tree,2,800CI passes and all focused/standalone/
fault gates. The exact55file tool-source copy retains both required notices and grants
no parser execution or platform authority. See [final gates](reviews/media-elf-tool-final-gates.md).
Continue [isolated ELF dependency metadata](v0.15-media-elf-dependency-metadata.md)
with source/launcher review before optional guestcode; original resource/filter/ABI
and decoder gates remain open. Core first; UI remains parked.


### Media isolated loader metadata checkpoint — 4 October 2026 Dubai

PR80 merged f6a8a584 with2,928CI passes and37 isolated cases/seven pinned inputs.
Exact reviewed tree verified; all four bounded attempts retained, final cleanup and
admission release confirmed. See [final gates](reviews/media-elf-metadata-final-gates.md).
Continue [GNU version graph design](v0.15-media-elf-version-metadata.md), with design
and source reviews before new parser execution. Symbols/ABI/platform/resource/filter/
decoder/Core authority remain open. Core first; UI61 stays parked as draft.

### Media GNU version graph checkpoint — 4 October 2026 Dubai

PR81 merged8aeaf6b with exactreviewedtree,3,189matchingCI passes,1,372focusedpasses,
fourstandalones,twocausalfaults and39actualcases/seveninputs. Restrictedversiongraph
structureonly; noABI/platform/decoder/Coreauthority. See
[final gates](reviews/media-elf-version-final-gates.md).
Continue [fixed platform dependency metadata](v0.15-media-platform-dependency-metadata.md):
12fixedinputs/44cases within SAME fourattemptversionquota (oneconsumed), shared
capacityoneadmission, unchangedguest/parser. No automaticretry oroldloadercapreset.
Completeinventory/provenance/materializer/daemon/resources/filter/decoder gatesremain.
UI61parked, flagsdisabled, v0.14.0preserved; v15then v16->v17->v18.

### Media platform dependency metadata checkpoint — 4 October 2026 UTC

PR82 merged at a473f46 with exact reviewed tree, 3194 matching-CI passes,
44 actual cases and 12 fixed input identities. Two version attempts are retained;
four old loader attempts remain exhausted. This is declaration metadata, with no
platform/ABI/native/decoder/Core authority. Continue the fixed Ubuntu origin
evidence design in [platform origin](v0.15-media-platform-origin.md), cleared for
offline implementation only. Native GPG, index and package stages retain distinct
source/execution gates. Original resources/filter gates remain before decoder.
