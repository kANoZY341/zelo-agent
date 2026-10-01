# Zelo — Canonical Architecture and Roadmap to v1.0

## Status

This document is the repository-facing roadmap summary.

The current canonical product plan is Zelo Canonical Architecture revision 3.2,
as amended by `docs/zelo-workspace-decision.md` and
`docs/zelo-organizational-authority-model.md`. The later explicit decisions win
where they refine older shorthand.

Feature design documents may refine an individual milestone, but they must not
silently redefine Zelo's identity or reorder the roadmap. Material roadmap or
architecture changes require an explicit operator decision.

This repository summary is governed by Canonical Architecture revision 3.2.
Revision 3.2 incorporates the 1 October 2026 organizational authority decision:
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

A persistent specialist with a role, goals, memory namespace, tools, procedures,
permissions, and budgets.

Examples include Coding Agent, Research Agent, Business Agent, YouTube Agent,
Trading Agent, Product Agent, and Security Agent.

An agent is not a model or worker.

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
   budget and activity management.

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

Integrate useful ECC, Open Code Review, Archify and similar patterns behind
Zelo-owned interfaces.

### v0.16 — Channels and presence

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
- skill improvement.

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
- expose operator controls, budgets and recovery state; and
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
- the first bounded v0.15 slice is the Capability Registry foundation on
  `feat/v0.15-capability-registry-foundation`;
- this foundation adds trusted capability identity/validation only and does not
  add new browser, computer, Python, shell, API, media, network or subprocess
  execution authority;
- the next bounded slice after Capability Registry is the trusted Skill Registry;
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
