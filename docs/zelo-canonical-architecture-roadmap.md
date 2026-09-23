# Zelo — Canonical Architecture and Roadmap to v1.0

## Status

This document is the project north star.

Feature design documents may refine an individual milestone, but they must not
silently redefine Zelo's identity or reorder the roadmap. Material roadmap or
architecture changes require an explicit operator decision.

## Zelo's identity

Zelo is the permanent personal AI control plane and chief-of-staff system.

Zelo is not a model, coding CLI, swarm framework, gateway, plugin ecosystem, or
third-party repository.

Permanent rule:

> Models think. Agents specialize. Workers work. Tools act. Zelo decides.

Zelo owns identity, goals, projects, routing, permissions, approvals, secrets,
memory boundaries, scheduling, auditing, budgets, validation, learning, and
system-level decisions.

Everything beneath those boundaries is replaceable.

## Core concepts

### Zelo

The permanent authority and orchestration layer.

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
                         OPERATOR
                            |
                            v
                 Channels / Interfaces
                            |
                            v
+---------------------------------------------------------------+
|                         ZELO CORE                             |
|                                                               |
| Identity / Goals / Projects / Tasks                           |
| Permissions / Approvals / Secrets / Trust                     |
| Audit / Security / Budgets / Resource limits                  |
| Memory ownership / Events / Sessions                          |
|                                                               |
|                 ZELO IS THE AUTHORITY                         |
+-----------------------------+---------------------------------+
                              |
                              v
+---------------------------------------------------------------+
|                    ZELO ORCHESTRATOR                          |
|                                                               |
| Planner / Supervisor / Agent Factory                          |
| Capability Router / Brain Router / Context Engine             |
| Scheduler / Verifier / Learning Engine                        |
| Self-Improvement Engine / Recovery / Self-Healing             |
| Budget Manager                                                |
+-----------------------------+---------------------------------+
                              |
             +----------------+----------------+
             |                                 |
             v                                 v
      Specialist Agents                  Core Runtimes
 Coding / Research / Business     Agent / Developer / Review /
 YouTube / Trading / future       Promotion / future campaign
             |                                 |
             +----------------+----------------+
                              |
                              v
+---------------------------------------------------------------+
|                 CAPABILITY / WORKER LAYER                     |
|                                                               |
| Codex / Claude Code / OpenCode / Ox / future workers          |
| Browser / Computer / Shell / Python / API / Research / Media  |
| Optional swarm backend such as Ruflo                          |
| Skills and engineering procedures                             |
+-----------------------------+---------------------------------+
                              |
                  +-----------+-----------+
                  |                       |
                  v                       v
                Tools                   Models
                  |                       |
                  +-----------+-----------+
                              |
                              v
+---------------------------------------------------------------+
|                       DATA PLANE                              |
|                                                               |
| Personal / Project / Agent / Episodic memory                  |
| Decisions / Outcomes / Skills / Knowledge                     |
| Files / Artifacts / Metrics / Costs / Audit history           |
|                                                               |
| Storage interface owned by Zelo                               |
| SQLite/files now -> Postgres/Supabase/vector stores later     |
+---------------------------------------------------------------+
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
| Ruflo | Optional swarm / coordinated execution backend |
| ECC | Engineering skills, hooks, verification and worker procedures |
| Harness | Agent Factory / agent-team design reference |
| OpenClaw | Channel/device/gateway reference or adapter |
| OpenMAIC | Durable sessions, runtime and resumability reference |
| Context Mode | Context Engine / memory handling reference |
| Open Code Review | Coding Agent reviewer capability |
| Archify | Architecture analysis capability |
| God's Eye View | Observability / discovery / visualization capability |
| Supabase | Optional future data backend behind Zelo storage interfaces |
| Ox | Replaceable coding worker |
| Claude Code | Replaceable coding worker |
| OpenCode | Provider-neutral coding worker/harness |
| Codex | Replaceable coding worker |
| Ollama | Local model provider |
| GPT / Claude / Gemini / Grok / Qwen / DeepSeek | Replaceable brains |

A newly discovered repository must first be mapped to a box in the canonical
architecture. Zelo must not be reshaped around whichever repository was most
recently discovered.

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

### v0.12 — Goals and autonomous campaigns

Add:

- Goal Manager;
- planner;
- campaign state;
- milestone decomposition;
- budgets;
- stop conditions;
- bounded retries;
- priorities;
- scheduler;
- event triggers;
- resumable long-running execution.

One self-improvement cycle becomes a bounded campaign.

### v0.13 — Memory, context and learning

Add:

- personal memory;
- project memory;
- decisions and outcomes;
- experience/failure history;
- worker/model performance history;
- Context Engine;
- bounded learning from outcomes;
- storage abstraction for later Postgres/Supabase/vector backends.

### v0.14 — Agent Factory

Add persistent specialist-agent identities.

First production specialists:

- Coding Agent;
- Research Agent.

Agents bind roles, goals, memory namespaces, capabilities, tools, permissions,
procedures, worker requirements and budgets.

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

Add communication surfaces:

- web/mobile;
- voice/phone;
- WhatsApp;
- Telegram;
- Discord;
- Slack;
- email;
- future channels.

OpenClaw may be used as a gateway/channel/device backend where useful. It does
not replace Zelo Core.

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
- report progress through useful channels; and
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

## Active branch policy

At the time this roadmap was established:

- released base: Zelo Core v0.10.0;
- active milestone: v0.11 Self-Improvement Seed;
- experimental Agent Factory work is parked until the v0.14 milestone.

Do not promote parked future work merely because code already exists.

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
