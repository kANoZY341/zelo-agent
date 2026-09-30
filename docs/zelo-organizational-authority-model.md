# Zelo organizational authority model

## Status

Canonical organizational authority requirement for Agent Factory.

This model refines the earlier flat department manager/producer concept. It does
not replace Zelo Core authority, task approvals, budgets, audit, worker
supervision or the existing bounded delegation contracts.

## Authority chain

The canonical company hierarchy is:

```text
Human Managing Director
        |
        v
Zelo — Group CEO
        |
        v
Vice Presidents
        |
        v
Directors
        |
        v
Managers
        |
        v
Specialist Agents
        |
        v
Workers
        |
        v
Tools
```

### Managing Director

The Managing Director is the human principal and ultimate human authority over
the Zelo organization.

The Managing Director:

- is not an AgentSpec;
- is not created by Agent Factory;
- is not a worker;
- is not represented by a model-generated identity;
- owns the final human authority boundary for major policy and organizational
  decisions.

Core must never allow an agent to impersonate, replace or create the Managing
Director identity.

### Zelo — Group CEO

Zelo is the permanent system identity and Group CEO under the Managing Director.

Zelo:

- is not an ordinary factory-created agent;
- owns company-level orchestration;
- proposes and manages organizational structure within policy;
- may appoint or activate trusted VP-level agent instances only through Core;
- remains subject to Core approvals, budgets, audit and policy;
- cannot override the Managing Director or Core safety boundaries.

There is exactly one active Zelo CEO identity for one Zelo deployment.

### Vice President

A VP is the highest factory-managed executive level.

A VP:

- reports to Zelo CEO;
- owns one or more divisions or major business domains;
- may supervise Directors only within explicit organizational authority;
- coordinates strategy, budgets and cross-department goals;
- does not gain unrestricted worker/tool authority merely because of title.

### Director

A Director:

- reports to a VP;
- owns a function, department or major domain inside the VP's scope;
- supervises Managers;
- converts strategy into bounded programs/campaigns;
- cannot create or supervise a VP.

### Manager

A Manager:

- reports to a Director;
- owns a team or workstream;
- supervises Specialist Agents;
- delegates concrete bounded work;
- is the normal organizational parent for specialist execution.

### Specialist Agent

A Specialist Agent:

- performs a narrow domain role such as Coding, Research, Marketing, Analytics,
  Security or Finance;
- normally has no permanent organizational reports;
- may use workers only through Zelo/Core-controlled task and worker selection;
- may receive explicit permission for bounded temporary helper delegation, but
  that does not make the helper a permanent organizational report or change the
  specialist's executive level.

## Organizational containers

Divisions, departments and teams are containers, not authority levels.

Examples:

```text
Division: Engineering
  VP Engineering

Department: Software Engineering
  Director of Software Engineering

Team: Backend Engineering
  Backend Engineering Manager
  Coding Agent
  Testing Agent
  Review Agent
```

The organizational container and the authority role are separate records. This
allows reorganization without changing agent identity or historical task
evidence.

## Two graphs, never one

Zelo must maintain two distinct relationship graphs.

### 1. Organizational reporting graph

This is the durable company structure:

```text
Zelo CEO
  -> VP
    -> Director
      -> Manager
        -> Specialist
```

It answers:

- who reports to whom;
- which organizational unit an agent belongs to;
- which executive scope governs the agent;
- which roles may create or appoint which lower roles.

The reporting graph must be acyclic and level-valid.

### 2. Task delegation graph

This is temporary execution authority:

```text
parent agent
  -> bounded delegation contract
    -> child agent
      -> Core task
```

It answers:

- what objective was delegated;
- which Core task kinds are allowed;
- which capabilities are required;
- what Campaign and Project own the work;
- what budget is reserved;
- what maximum delegation depth applies.

A task delegation never changes the organizational reporting graph.

## Role ordering

Factory-managed organizational levels are ordered:

```text
vp > director > manager > specialist
```

Default creation/supervision rules:

```text
Zelo CEO   -> VP
VP         -> Director
Director   -> Manager
Manager    -> Specialist
Specialist -> no permanent organizational child
```

Any exception must be explicit trusted configuration and must still preserve
Core policy. Model output alone cannot widen these rules.

## Planned Core representation

The preferred bounded implementation is:

### AgentSpec

AgentSpec remains the versioned role/capability blueprint and gains a trusted
organizational-role declaration for factory-managed agents:

- `vp`
- `director`
- `manager`
- `specialist`

The organizational level is an authority ceiling, not a permission bundle.

### OrganizationUnit

A durable organization-unit record represents a division, department or team.

It contains:

- stable unit ID;
- unit type;
- owning Project or organization scope;
- name and objective;
- parent unit;
- lifecycle state.

### OrganizationAssignment

A separate assignment record binds an AgentInstance to:

- organization unit;
- organizational role;
- reporting parent;
- effective lifecycle period.

Assignments are separate from AgentInstance identity so reorganizations do not
rewrite historical agent identity.

### Reserved roots

The reporting root is not another AgentInstance.

Core treats:

- human Managing Director; and
- Zelo CEO

as reserved authority principals. VP assignments report to the Zelo CEO root.

## Authority chain evidence

When an agent creates work, Core should be able to preserve the organizational
authority chain alongside the existing execution binding.

Example:

```text
Managing Director
  -> Zelo CEO
    -> VP Engineering
      -> Director Software Engineering
        -> Backend Manager
          -> Coding Agent
            -> developer_run
              -> selected worker
```

Historical task evidence must continue to identify the exact agent, AgentSpec,
organization assignment/delegation contract and selected worker that were in
force when the work was authorized.

## Factory behavior

A future request such as:

> Create a YouTube organization.

should result in a proposal, not uncontrolled spawning.

Example proposed structure:

```text
Zelo CEO
  -> VP Media
    -> Director YouTube
      -> YouTube Manager
        -> Research Agent
        -> Content Strategy Agent
        -> Script Agent
        -> Thumbnail Agent
        -> Analytics Agent
```

Core then validates:

- every AgentSpec is trusted/versioned;
- every reporting edge is level-valid;
- every unit exists and is in scope;
- instance and child limits are respected;
- budgets and approvals are present;
- no reporting or delegation cycle exists;
- no model-created role can impersonate reserved principals.

## v0.14 implementation order

The updated remaining Agent Factory order is:

1. finish generic Coding/Research execution identity;
2. add organizational role/unit/assignment contracts;
3. migrate the transitional flat DepartmentSpec manager/producer concept to the
   organizational container model without breaking historical registry data;
4. add hierarchy-aware bounded child-agent creation;
5. add activation/evaluation gates for new executive and specialist templates;
6. prepare v0.14 release.

This ordering ensures automatic agent creation is not enabled before the company
authority hierarchy exists.

## Non-goals

This model does not:

- grant executives unrestricted shell/browser/computer access;
- bypass Core tasks or approvals;
- make workers employees or organizational agents;
- allow models to create Managing Director or Zelo CEO identities;
- allow titles to widen tool permissions;
- merge organizational reporting with temporary delegation;
- permit an agent to change its own organizational level.

The governing rule remains:

> The Managing Director owns human authority. Zelo leads the AI organization.
> Executives govern. Managers coordinate. Specialists perform domain work.
> Workers execute. Tools act. Core enforces.
