# Zelo workspace direction

Confirmed by Ahmad on 24 September 2026.
Implementation choice selected after the live Core proof on 26 September 2026.

Zelo is the persistent personal AI Ahmad talks to: he reasons, remembers,
discusses ideas, plans, delegates, verifies results and follows up. His expandable
company includes managers, supervisors, producers, specialists and interchangeable
workers. The workspace presents this system; it does not define or replace Zelo's
intelligence.

## One workspace, three connected views

1. **Talk to Zelo:** always-accessible text and later voice conversation for ideas,
   goals, decisions and updates. Linked WhatsApp, email and call channels connect
   to the same identity and relevant conversation/project context.
2. **Company World:** an optional visual office or town, inspired by Agent Town.
   Departments appear as rooms and agents as characters. Selecting a character
   opens real assignments, progress, evidence and outputs.
3. **Control Desk:** practical project, file, policy, procedure, approval, budget
   and activity management, drawing strongly on Mission Control's useful patterns.

## Selected implementation direction

The reversible proof phase is complete. The selected direction is a
**Zelo-owned workspace with Mission-Control-derived control-desk UX and a later
Agent-Town-derived Company World**.

This is deliberately not a wholesale Mission Control or Agent Town runtime
adoption.

Mission Control is MIT-licensed and is the primary reference/reuse source for
navigation, monitoring, task/activity panels and operational dashboard patterns.
Agent Town is MIT-licensed and is the reference/reuse source for the later Phaser
visual office/world. Any copied or materially adapted code must keep required
license notices and be recorded in provenance.

Zelo owns:

- the workspace product identity and navigation;
- the browser/server adapter to Zelo Core;
- conversation UX;
- project and company views over canonical Core contracts;
- security decisions about what the browser can see;
- the integration contract between Talk, Company World and Control Desk.

Zelo Core continues to own:

- task identity and state;
- approvals and policy;
- worker selection and authority;
- audit;
- scheduling and budgets when those milestones arrive;
- operational state and later memory boundaries.

## First workspace foundation

The first slice is intentionally smaller than Mission Control:

```text
browser
  -> local Zelo Workspace server
  -> authenticated server-side Core adapter
  -> Zelo conversation contract
  -> optional canonical Core task
  -> live task projection/result
  -> browser
```

Requirements:

- bind workspace development to `127.0.0.1`;
- never expose `ZELO_OPERATOR_TOKEN` to browser JavaScript;
- keep Core URL configured server-side and restricted to loopback;
- use the existing conversation and task projection contracts;
- poll or stream canonical Core state rather than inventing UI state;
- keep Company World and Control Desk navigation visible but do not fabricate
  functionality before their Core contracts exist.

## Product rules

- The visual world reflects actual Core execution. Animations never delay tasks
  or imply unobserved progress.
- Every essential action is available through conversation or ordinary controls;
  walking around is optional.
- All views share canonical Core state and authority. UI choice does not change
  Zelo's identity, memory or company contracts.
- The workspace may cache presentation data later, but a cache never becomes the
  source of truth for task/approval authority.
- New third-party code must pass license, provenance, dependency and security
  review before it is vendored or adapted.

## Proof that unlocked this decision

On 26 September 2026 the Early Zelo Chat path was proven live from green
`main` commit `f920036d9c1631b8ddf999554b3c10437602cbf5`:

```text
Talk to Zelo
  -> Mind requested agent_run
  -> Core executed repo_status
  -> real task reached succeeded
  -> conversation projected the completed result
  -> follow-up Mind turn answered from that authoritative result
```

That satisfies the earlier requirement to prove a complete conversation-to-result
flow before choosing the workspace implementation.

## References

- Mission Control: https://github.com/smolboon/mission-control
- Agent Town: https://github.com/geezerrrr/agent-town
