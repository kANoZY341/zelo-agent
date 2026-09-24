# Zelo — Canonical Architecture and Roadmap to v1.0

## Status

This document is the repository-facing roadmap summary.

The current canonical product plan is Zelo Canonical Architecture revision 3.1,
as amended by `docs/zelo-workspace-decision.md`. The workspace decision is later
and therefore supersedes revision 3.1 wherever the two conflict.

Feature design documents may refine an individual milestone, but they must not
silently redefine Zelo's identity or reorder the roadmap. Material roadmap or
architecture changes require an explicit operator decision.

This repository summary is governed by the 24 September 2026 Canonical
Architecture revision 3.1 plus `docs/zelo-workspace-decision.md`. When they
conflict with older shorthand in this repository, the later explicit decision
wins.

The product model is:

> Zelo is Ahmad's persistent personal AI. Zelo Mind reasons, remembers, discusses,
> plans, delegates, verifies and follows up. Zelo Core is the trusted authority and
> execution boundary. The company of managers, supervisors, producers, specialists
> and interchangeable workers operates underneath those two layers.

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

Managers, supervisors, producers and specialists that coordinate bounded work
under Core authority. These are responsibilities and persistent role records,
not necessarily separate always-running model processes.

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
        | managers / supervisors / producers          |
        | specialists / Goal Manager / Planner        |
        | Scheduler / Verifier / Agent Factory later |
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

### v0.11 — Self-improvement seed — active

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

### Early workspace increment — immediately after v0.11

Adapt or prototype the workspace to show Core tasks, proposals, decisions and
results. Add the first Zelo Mind text conversation that can answer directly,
discuss a plan and request one typed Core action. Prove one
conversation-to-result path before committing to Mission Control, Agent Town, a
hybrid or a custom workspace.

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

Generalize the fixed Coding and Research specialists into versioned
`AgentSpec` templates and `DepartmentSpec` records with manager/producer
assignments, registry, activation tests and lifecycle controls.

Add bounded subagent creation, explicit parent-child contracts and
project-scoped memory. Inspect the parked Agent Factory experiment and reuse only
compatible parts; do not merge it wholesale.

Agents bind roles, goals, memory namespaces, capabilities, tools, permissions,
procedures, worker requirements, review requirements, budgets and lifecycle.

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

### v0.17 — Real specialist agents

Add domain specialists such as:

- Business Agent;
- YouTube Agent;
- Trading Agent;
- Marketing Agent;
- Product Agent;
- Security Agent;
- Personal Agent.

Each gets only the tools, memory and authority required by its role.

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

Current verified baseline as of 24 September 2026:

- v0.10.0 is released from main commit
  `666a0a4d12d280187af02a621e1ab4b57ce604c6`;
- active branch: `feat/v0.11-self-improvement`;
- parked experiment: `feat/v1.0-agent-factory`, deferred to v0.14;
- v0.11 implementation is at PR/review preparation stage.

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
