# Zelo Workspace Foundation

## Decision

The Early Zelo Chat proof gate is complete. Zelo will use a Zelo-owned workspace
with a Mission-Control-derived Control Desk and a later Agent-Town-derived
Company World.

Mission Control and Agent Town were inspected before this decision. Both use the
MIT license. The first slice borrows architecture and UX patterns only; it does
not copy third-party source code. If source code is copied or materially adapted
later, preserve the required notice and record provenance.

## Why Zelo owns the workspace

Mission Control already has useful dashboard patterns, but it also owns its own
database, scheduler, task model, auth roles, gateway/session state and workflow
runtime. Importing that whole runtime would create a second control plane beside
Zelo Core.

Agent Town currently centers its execution contract around an OpenClaw gateway.
Using it wholesale would similarly make the visual layer responsible for runtime
concepts Zelo already owns.

Zelo therefore keeps the useful UX ideas while preserving one authority boundary:

```text
Browser
  |
  v
Zelo Workspace (presentation + interaction)
  |
  | server-side authenticated adapter
  v
Zelo Core (authority)
  |
  +--> Zelo Mind
  +--> tasks / approvals / audit
  +--> agents / workers / tools
  +--> results
```

## First implementation slice

The first workspace slice proves Talk to Zelo in a browser using the same
contracts already proven live through curl.

It includes:

- local Next.js/React workspace bound to `127.0.0.1`;
- server-side Core adapter;
- no Core operator token in browser JavaScript;
- persistent conversation creation and text turns;
- canonical Core task projection beside the conversation;
- polling while a linked task is pending, awaiting approval or running;
- result/error rendering from the Core projection;
- visible placeholders for Company World and Control Desk.

It deliberately excludes:

- a second database;
- client-owned task state;
- task/approval mutation outside existing Core contracts;
- Company World animation;
- durable goals/campaigns;
- memory;
- voice and external channels.

## Security boundary

The workspace server reads `ZELO_OPERATOR_TOKEN` from its server environment and
adds it only when calling Core. Browser requests use a local workspace header and
never receive the token.

`ZELO_CORE_URL` is server-configured and restricted to loopback HTTP. Core and
workspace development servers stay on `127.0.0.1`.

This is not intended as internet-facing authentication. Remote/network deployment
belongs to a later hardened presence/channel design.

## Third-party reuse plan

### Mission Control

Primary source of patterns for:

- navigation rail and workspace shell;
- task/activity monitoring;
- operational status surfaces;
- approvals/review presentation;
- cost/budget visualization when Core exposes those contracts;
- real-time update UX.

Do not import its SQLite database, scheduler, task authority, OpenClaw gateway
ownership or RBAC model as Zelo authority.

### Agent Town

Primary source of patterns for the later Company World:

- Phaser scene organization;
- sprites/office navigation;
- task-state visualization;
- selecting an agent/worker to inspect real work;
- typed UI event flows.

Do not let movement, animation or an OpenClaw connector become execution
authority. Company World must render actual Zelo Core/company state.

## Next proof

The first browser smoke test must reproduce this already-proven path:

```text
Talk to Zelo
  -> Mind answers directly
  -> operator requests current repository state
  -> Mind submits agent_run
  -> Core executes repo_status
  -> workspace shows running task
  -> workspace shows succeeded result
  -> follow-up turn answers from persisted Core result
```

Only after that proof should the Control Desk expand beyond the execution
inspector.
