# Zelo — Canonical Architecture and Roadmap to v1.0

## Status

**17 October current mainline:** PR #193 bulk Space withdrawal merged at `c603b5300475a507a23e6333b01baa4a67a00083`. Its distinct post-merge main CI `39058416557` **passed**: 6,335 tests passed, 2 skipped, 42 warnings.

**17 October delegated Space read candidate (draft PR #194):** the candidate binds separate parent/child owner confirmations to one resource and read/list operation under one-hop delegation. It remains unmerged; native acceptance was not run, and exact reconciled-head CI/review are pending. Full v0.15 remains HOLD.

**17 October integration checkpoint:** approval-history PR #191 merged
at `0471c29e43386202fbb3535aa6df3e4d0191a645` after exact-content full CI
passed **6,311 tests, 2 skipped**; its distinct post-merge main run
**39033985522** subsequently passed. Owner-takeover status PR #192 merged
at `077f6a5e6fd7dc3f259765cb284d9cd9e1f5efb5` after exact-head CI
passed **6,318 tests, 2 skipped**, native proof and independent review.
Its separate post-merge main run **39043578016** is queued at this
checkpoint. The full operational v0.15 release remains HOLD.

**17 October owner-control status correction (merged #192):** Specialist
Space eligibility now reflects the existing durable owner-takeover barrier.
Three normal-Core regressions first reproduced false-positive eligibility
while Task admission already denied access. No execution check or permission
was weakened. See [Space status](v0.15-specialist-space-access-status.md#owner-takeover-and-handback).
The broader eight-module regression passed **106 tests, 3 warnings**.
Independent exact-source review, native qualification and full CI passed
for #192; the full v0.15 scope and HOLD remain unchanged.

**17 October bulk Space withdrawal candidate (not merged):** a confirmed
owner POST can withdraw an Agent's recorded intents and confirmations in one
Core database transaction, without deleting resources/history or affecting
other Agents. It remains usable for inactive/stale bindings and does not
silently ban future explicit owner approvals. Two missing-route regressions
failed first; the focused implementation group then passed **17 tests,
3 warnings** including rollback, concurrent requests, pending/claimed reads,
and revocation during a suspended backend check. A stale-fixture attempt
correctly hit the immutable-identity trigger; only the fixture was corrected
to use a changed registry. The separate nine-module regression passed
**125 tests, 3 warnings**, and a pre-commit real loopback HTTP/native Computer
journey passed paused bulk withdrawal, other-Agent isolation, restart,
retained file/results and later explicit owner re-approval. Read-only review
found no actionable findings. Final exact-commit review/native acceptance
and full CI remain separate gates. See the
[owner workflow](v0.15-space-owner-provisioning.md#revoke-all-existing-approvals-for-one-agent).
No release, Media budget, production permissions or schedule was changed.

**Historical 17 October approval-history candidate:** owner-only paginated
intent/confirmation discovery reuses the existing immutable records and receipt
checks. History remains available after an Agent/Project stop or registry change
so old approvals can be located and explicitly revoked; visibility grants no
execution authority. The first regression group passed 36 tests; the final
nine-module group passed **142 tests, 3 warnings**, as a separate run. A normal
Core loopback HTTP/native Computer proof exercised restart, rediscovered
approval execution, paused revocation and denied post-resume access. Read-only
review found no actionable defects. Exact committed-source review/native checks
and full CI remain separate integration gates. Resource
discovery PR #190 and all full v0.15 release requirements remain tracked without
scope reduction. See the [operator workflow](v0.15-space-owner-provisioning.md#recover-and-revoke-prior-approvals).

**17 October live integration / Space discovery continuation:** PR #189
is merged at `5266eed20b7b91be028065b09a91c2a37d97ea41`; its independent
post-merge main CI [38988550329](https://github.com/kANoZY341/zelo-agent/actions/runs/38988550329)
is verified **PASS**. Owner-created text Spaces are integrated, not a draft.

The current local candidate adds authenticated, paginated discovery of existing
resource receipts within the selected Agent's Project and trusted Spaces.
Retirement and root-attestation state remain visible; contents and effective
grants are not returned. Three baseline tests reproduced the missing GET route
before implementation. See [owner Space workflow](v0.15-space-owner-provisioning.md).
Candidate review, full CI and exact-source operational acceptance are separate
gates, not satisfied by this status entry. All earlier dated checkpoints below
are historical. Full operational v0.15 remains HOLD; no scope reduction,
production activation, schedule restart or Media proof-budget change is made.

**17 October resumed Space owner-provisioning candidate:** main is now
`1ad0e819d9988b44bb52083f42ae76db93b43d6e` after merged #188, and its distinct
post-merge CI **38926255055 is PASS**. Zelo remote access is restored. The
interrupted six-module local result was recovered: **107 passed, 3 warnings**.
The ten saved edited files were preserved byte-for-byte while integrating main.

The authenticated owner can create a new private, bounded text resource through
the normal Space API, then use separate confirmation and ordinary read/list
Tasks. See [Space owner provisioning](v0.15-space-owner-provisioning.md).
An initial real loopback HTTP/native Computer proof passed this workflow, but
it preceded the review corrections below and is not final-source acceptance.

Independent review found a published-directory replacement race and incomplete
rechecking of earlier seed files. Four controlled failing regressions reproduced
them. The correction prepares privately, retains an exclusive retry reservation,
checks the complete bounded seed set and uses atomic no-replace publication.
The 30-case corrected suite and separate three-case publication/DB-recovery
suite passed. A fresh nine-module regression then passed **148 tests, 3 warnings**,
with no failures; these are separate runs, not additive release totals.
Independent follow-up and exact functional-commit review found no actionable
defects. The expanded native restart proof first failed because it omitted the
required post-restart browser snapshot. A separate run on unchanged production
code observed that resume flag, verified content remained blocked, performed an
explicit snapshot Task and reread the preserved Space content successfully.
The original failed proof stays failed; no permission check was weakened.
Final candidate checks and full CI remain separate gates.
No automatic Agent grant, runtime activation, additional Media attempt or
release scope reduction occurred; full v0.15 remains HOLD.

**16 October current integration:** PR #187 merged at
`ba0fc1dab59fac6a9eff5a9cd597d6141a6b377c` after exact-head CI
[38889367738](https://github.com/kANoZY341/zelo-agent/actions/runs/38889367738)
**PASS: 6,240 passed, 2 skipped** and independent patch review. The distinct
post-merge main CI
[38893887444](https://github.com/kANoZY341/zelo-agent/actions/runs/38893887444)
is **IN PROGRESS, not a PASS** at this checkpoint.

The new normal-Core Computer startup candidate has **182 passed, 3 warnings**
in its final 12-module Computer/Space/activation suite, and a separate
**12/12** new startup suite. Real loopback HTTP and native OpenBot/Chromium
qualified all four read actions, explicit Computer/Space revocation, human
control, container-recreation persistence and full Core restart without any
registry/service/Runner injection. Independent exact-source review found no
actionable defects. Candidate integration with merged #187 preserved its
source tree exactly. Its own full CI and final merge remain separate gates.

A separate real normal-Core API Worker proof also passed on exact candidate
source `5fe8251b0d6a56351ab7f5a446efea2326d7a103`: credential-free HTTPS GET
of the fixed public GitHub `python/cpython` repository metadata through actual
DNS/IP pinning/TLS, persisted Task/audit, unauthenticated/write-method
refusal and removal of the endpoint on restart. All temporary Core services
and data were cleaned. This qualifies that one public GET route, not arbitrary
APIs, credentialed providers or production deployment. The historical blocked
API attempt stays **NOT RUN**; it is not reclassified by this fresh success.

The real private GitHub branch-policy read still returns **403** (provider
plan/visibility requirement). No purchase, visibility change, Skill-policy
bypass, fifth Media proof or release tag was performed. Full v0.15 stays HOLD.

**16 October normal-Core Computer candidate (not merged):** the fixed,
operator-selected `computer_observer` Specialist now has a normal startup
path, reusing the existing registry, permissions and Runner rather than
injecting a test Agent. A real loopback HTTP / full-lifespan / native
OpenBot-Computer proof passed Task execution, Space read/list/revocation,
owner control, workspace persistence across container replacement, and a
second Core startup preserving Agent, Task result and revocation state.
Temporary proof-owned resources were cleaned. This is a local functional
candidate, not production activation or full v0.15 qualification. See
[v0.15 Computer Specialist startup](v0.15-computer-specialist-startup.md).

**16 October verified integration checkpoint:** `main` is
`f2924f15652cf51a0876fa2dd62d8cafa89c200d` after merged PR #186. Its exact-head
PR CI 38825281940 and distinct [post-merge main CI 38872713784](https://github.com/kANoZY341/zelo-agent/actions/runs/38872713784)
are **PASS**. PR #184 cross-Space isolation and PR #185 isolated Python
HTTP qualification are also merged. The bounded owner-confirmed Space
`files_list` route is implemented on main; generalized Space rights are not.

**Current local Computer correction (not merged):**
`fix/v0.15-computer-human-admission` now checks the durable owner takeover
barrier before generic Computer Task submission, retry and claim, not only
for Space-bound file Tasks. A retry keeps its new action version and rebound
Computer identity in one database flush; the existing immutable-identity
trigger is unchanged. The seven-module Computer/Space suite passed
**144 tests, 3 warnings**, with no failures. A separate real pinned
OpenBot/Chromium, disposable-database proof passed Core snapshot/file-list
Tasks, authenticated operator control, early takeover denial, workspace
persistence across container recreation and owned-resource cleanup. This is
an isolated host proof, not a production deployment, generalized Computer
permission, or complete R1/R3 qualification. Independent review, this
candidate's full exact-head CI and post-merge main CI remain required.

The full operational v0.15 scope remains **HOLD**; the four exhausted Media
proof attempts, private-provider Skill publication blocker, remaining Worker
and integration qualifications, final audit and operator release decision
are not waived.

**16 October scope affirmation:** v0.15 must fulfill the full originally
approved operational capability ecosystem; the reduced Core-only foundation
release is **not approved**. Inert third-party code and unqualified Workers
remain blockers, not completed deliverables. Merged PR #186 extends
owner-confirmed Space file resources from a single bounded read to
independently granted, Task-bound, bounded directory listing with execution
and audit. This does not complete general Space permissions or the v0.15
release. No new native Media reader proof attempt is authorized.


**Active engineering priority (12 October 2026): complete and qualify
v0.15 before promoting additional v0.16/v0.17/v0.18 or UI features.**
See `docs/v0.15-release-closeout.md` and [issue #166](https://github.com/kANoZY341/zelo-agent/issues/166)
for the ordered release gates and known blockers. The most recent published
release is still `v0.14.0`; merging ahead-of-version code does **not**
complete the earlier version. Existing later-version code and worktrees stay
preserved but new nonessential feature promotions are paused. Final v0.15
scope or exclusions cannot change without an explicit operator decision.

**Historical 15 October evidence checkpoint:** `main` reached
`a603a136623778abe30f98bb4bf74d932c2441a7` after #183's
release-retry SHA revocation fix. Its
[exact-head CI 38751369757](https://github.com/kANoZY341/zelo-agent/actions/runs/38751369757)
**PASS** (6,220 passed, 2 skipped) and predecessor
[post-#182 main CI 38750520588](https://github.com/kANoZY341/zelo-agent/actions/runs/38750520588)
**PASS**, while distinct
[post-#183 main CI 38761948344](https://github.com/kANoZY341/zelo-agent/actions/runs/38761948344)
is **IN PROGRESS, not a PASS**. R1 cross-Space negative Runner tests are
local-only, not general Space permission admission. The four terminal
Media native-reader refusals, private-repo GitHub protection API 403,
missing full live Worker qualifications, final independent release audit
and Managing Director scope decision remain open. Earlier failed CI
receipts stay historical failures; later green CI cannot retroactively
reclassify them. v0.15 remains release HOLD.

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
| Addy Osmani / Matt Pocock skills | Selected repository development procedures, adapted to Zelo rules with pinned MIT source notices |
| Agent-Reach | Public-source access choices and diagnostics, behind Zelo-owned retrieval and evidence boundaries |
| Hindsight | Evaluation-only candidate for richer retrieval behind Core memory scope and lifecycle controls |
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

**Curated integration program (9 October 2026):** The Managing Director
selected Open Code Review → ECC → Harness → Archify → Context Mode **concepts**
→ the remaining OpenDots Computer Panel, Space permission and skill-learning
integration. See [the pinned reuse and exact authority gates](v0.15-curated-community-import-program.md).
OpenDots Space authority follows as a separate default-deny stage:
operator-only, per-AgentInstance scoped and version-bound grant *intents*
are recorded with revocation but are never usable for Worker, Tool, File,
Browser or Shell actions until the independent enforcement gates pass.
See `v0.15-opendots-space-grant-intent-foundation.md`.
A separately staged Core-owned file-resource registry binds canonical Space IDs
to pre-provisioned, attested roots, and an operator-only confirmation binds
a current intent to one exact resource. Neither the registry nor a reviewed
confirmation grants Worker/Tool/file-opening authority; dispatch/use-point
enforcement remains a future independently qualified slice. See
`v0.15-space-resource-operator-confirmation.md`.
A separately gated task-bound read gateway adds an internal, bounded
per-Task/Attempt file use-point with the exact Space confirmation and
Core Computer permission rechecked under a serialized transaction; no
Worker dispatch, model Tool or HTTP file-reading endpoint is activated.
See `v0.15-space-file-gateway.md`.
A subsequent explicitly scoped Computer Task route binds the exact operator
Space confirmation into immutable Task and Worker execution identities,
revalidating it on submit/retry/claim and at read dispatch. Generic Computer
backends reject Space-bound requests; the Core Runner fails closed without its
Space file gateway. This remains file-read-only, not general OpenDots authority
or an automatic Agent Tool. See `v0.15-space-file-task-routing.md`.
An additional authenticated, read-only Specialist Space-status projection
shows whether a currently confirmed, scoped file Task appears eligible, or
which exact owner/lifecycle/registry/resource/Computer prerequisite denies
it. The projection never grants access or exposes source paths/content; every
use-point revalidates its own authority. See
`v0.15-specialist-space-access-status.md`.
Existing Computer Worker execution is also revalidated after the control-state
await and started-event callback, immediately before backend dispatch. This
closes a reproduced revocation gap without granting Space authority; see
`v0.15-computer-dispatch-revalidation.md`.

The OpenDots Specialist profile separately exposes a bounded, operator-only
governed Skill Learning history view, displaying historical proposal, review
and promotion-link metadata without instructions or activation authority.
See `v0.15-opendots-specialist-learning-history.md`.
The next Context Mode concept slice stores explicitly submitted, untrusted
text in a bounded project/operator-scoped archive with SHA-256-anchored narrow
windows; it never automatically admits retrieved text to Zelo Mind. See
`v0.15-context-mode-explicit-archive.md`. This is not a Space/Agent grant.
The first Core slice imports narrow source assets with full notices, exposes
operator-only draft/evidence/projection contracts, and preserves original
Worker, Skill, Agent, Task and Space authority. Source snapshots are not a
runtime plugin installation. Finish independent evaluation and promotion
before activating each capability. UI PR #61 remains parked; the imported
OpenDots React panel is a reference, not production UI. The separate
read-only OCR trusted-Git-diff scope strengthens evidence anchoring to
immutable Git commits; it does not register or authorize a reviewer Worker
or grant any CI/merge permission (see
`v0.15-curated-ocr-trusted-git-review.md`). An authenticated, manually
selected OCR operator preview now binds strict raw reviewer JSON to that same
trusted Git scope, returning non-authoritative ReviewerReport/evidence without
changing Task, Tool or independent reviewer authority. See
`v0.15-ocr-operator-evidence-preview.md`. An additional operator-only OCR
fixed-case calibration scores external, explicitly supplied review JSON on four
source-pinned synthetic examples, recording matched findings, false alarms,
misses and invalid reviews without a model/Worker/Task/Tool grant. These small
fixtures are not real-Git reviewer qualification or a substitute for the
independent Coding reviewer. See `v0.15-open-code-review-fixed-calibration.md`.
An additional explicitly opted-in local Ollama probe reuses Zelo's existing
pinned, metadata-attested loopback ModelProvider and those four synthetic
fixtures, recording the model's actual outputs as bounded, digest-bound
scores without creating Tasks, granting reviewer authority, or claiming
real Git semantic accuracy. See `v0.15-ocr-local-model-probe.md`.
Four ECC manual playbook
candidates expose Zelo-authored reviewed procedures without activating
Skills, Workers or Tools (see v0.15-ecc-manual-engineering-playbooks.md).
An operator-only, deterministic fixed-case evaluator binds four reviewed ECC
draft candidates to SHA256-frozen synthetic engineering decisions, measuring
answer agreement, unsafe misses, false alarms and unsupported overclaims
without executing agents, storing Task evidence, activating Skills or granting
permissions (see v0.15-ecc-fixed-case-evaluation.md). This is a fixture grader,
not real independent Agent evaluation or promotion qualification.
A separately opted-in local-only Ollama probe reuses the existing trusted
ModelProvider to measure three live model responses against that fixed suite,
with exact source/model identity, bounded time/output, measured token usage and
no Task/Tool/Agent/Skill grants (see v0.15-ecc-local-model-probe.md). A passing
sample is still not real Agent Task/Attempt, independent security evaluation or
permission/Skill promotion authority.
Harness proposal templates additionally cover permissionless YouTube content
and Trading research hierarchies under Managing Director → Zelo CEO (see
v0.15-harness-specialist-team-drafts.md).
A new operator-selected Project-bound Harness team review plan checks pinned
blueprint, current Project status, Agent Registry version, globally reserved
role identities and explicitly proposed four-role planning ceilings. Its SHA
is a checksum, never an activation ticket; no AgentInstances, Workers, Tasks,
Skill promotion, tools, Spaces or execution budgets are granted. See
v0.15-harness-project-review-plan.md.
A separate immutable, operator-only Harness design-review ledger records the
exact current Project/Registry/blueprint/budget plan and human design decision
under a serialized transaction. Its SHA-bound historical receipt is never a
team-activation token; no AgentInstance, Task, org unit, runtime budget or
Tool/Space grant is created. See `v0.15-harness-durable-design-review.md`.
The separate Archify projection of the trusted Agent Factory registry makes
registered template hierarchy inspectable without live instance or grant claims
(see v0.15-archify-agent-registry-projection.md).
A further operator-only Archify view renders the **current** Project-bound
Harness design-review plan as a strict static diagram of the four proposed,
never-deployed roles, with exact plan fingerprint, Project/Registry binding,
denial reason and no instance or permission grant. See
`v0.15-archify-harness-project-design.md`. Live team activation still requires
separate durable Managing Director approval and runtime qualifications.

**Selected continuation (10 October 2026):** The repair queue is complete and the
[Addy + Matt engineering pack](development/engineering-pack.md) merged in PR #143.
The [public research workflow](v0.15-public-research-workflow.md#observed-result)
completed its operational checks and separately reviewed memory admission,
scoped recall and replay. Its unedited answer failed semantic review; the
admitted summary was separately corrected and approved.

The [bounded Hindsight comparison](v0.15-hindsight-memory-comparison.md) evaluates
the same public-source work with a fixed synthetic lifecycle and isolation
workload. Its retained result and operator assessment are recorded in the
comparison guide and [live project status](zelo-project-status.md). Public-source
text remains untrusted evidence. Core retains scope, expiry, correction,
supersession, provenance and context-admission authority; the experiment grants
no production backend switch or autonomous retention authority.

HyperFrames and VoiceStudio follow their corresponding production tasks.
The engineering focus remains v0.15; the published release remains v0.14.0.
UI PR #61 stays parked, REA runtime activation remains inactive, and the
v0.16 → v0.17 → v0.18 milestone order is unchanged. The four consumed Media
native-reader attempts remain preserved refusals; no fifth attempt is authorized.

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
A later v0.15 maintenance fix qualifies real registered linked Git
worktrees for bounded, clean, read-only self-inspection with reciprocal
Git metadata identity checks; it does not relax Developer execution or
approval authority. See `v0.15-self-improvement-linked-worktree-qualification.md`.

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

The four previously consumed Media native-reader qualification attempts
remain refused; no fifth attempt is authorized by a skills merge.

Core-first checkpoint (8 October 2026): merged PRs #93, #98, #99, #100,
including #102, #103, #104, #105, #106 and #107. PR #107 adds the
new-proposal admission guard. It refuses a brand-new learned procedure
revision at or below the latest promoted version for the same
AgentInstance, source Skill and procedure. Stored proposal retries remain
idempotent, and historical reviews/proof links remain valid.

10 October 2026: the repair queue and PR #143 engineering pack are complete.
Public Research passed operational checks; the raw answer failed review and a
corrected summary was separately admitted and replayed. See live status for the
bounded Hindsight result.

Merged PR #110 at b8a9634 (4,988 passed, 2 skipped) adds
**local promotion-link recording exclusivity**: under a serialized SQLite
writer, previously accepted competing skill revision heads cannot gain a
second local promotion link. Historical divergent heads remain fail-closed.
Merged PR #111 implements **generic pre-merge publication refusal**:
until trusted skill identity/exclusive publication reservations exist, the
generic GitHub merger must refuse protected learned-procedure files.
Neither gate adds runtime skill activation, and this refusal is not global
GitHub branch protection or proof that a separate GitHub client is blocked.
A trusted, skill-specific promotion-time exclusivity reservation is still
required for publication; this is not runtime skill activation.

Merged PR #108 starts non-authoritative v0.16 channel contracts; merged PR #109
(bd586aa7) adds operator-selected opaque bindings and unverified replay metadata.
These are not runtime skill activation, provider authentication or new
AgentSpec/worker/tool authority. The four previously consumed native-reader
qualification attempts remain preserved as refusals. UI PR #61 stays parked.

The exact-head #110 CI passed 4,988 tests and 2 skips, Ruff and formatting,
and its reviewed tree matched merged main. PR #111's merged refusal does
not grant publication or worker/tool permission.

A separate, **inert** next-step publication-intent contract can freeze one
currently accepted proposal, operator review, exact content-addressed artifact
and verified promoted predecessor into a deterministic SHA-256-bound preview.
It has no durable exclusive claim, GitHub merge authority, Task/Tool grant or
runtime activation. The generic PR #111 learned-file refusal remains in force.
See `docs/v0.15-skill-publication-intent-contract.md`.

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

REA full-source reuse decision (8 October 2026): copy the complete upstream
REA 6.0.0 Git-tracked source into Zelo under `third_party/rea/`, pinned to
commit `6fee42689ae6e95a0182e0d4d0f55644e73e72be`, rather than
depending on an external MCP installation. This is an inert
source-owned integration, recorded in `docs/v0.15-rea-v6-source-import.md` and
merged PR #119 (main af6c209). The optional five independent engine Git
submodules remain pinned by commit but not included as copied source. The
REA Specialist candidate from #116 and the source-built static MCP proof
from #118 are already delivered in #119, not alternate activation routes.
The fixed manual fixture does not admit user targets. A separate
[operator-only offline admission utility](v0.15-rea-target-admission.md) now seals
text-only target snapshots from a fixed private inbox. An additional
[explicit offline target preflight](v0.15-rea-target-preflight.md) rechecks a
snapshot's independently retained digest, complete vendored source and 139-tool
catalog from an operator-only local CLI, and emits only an inert review fingerprint.
The live Core API still cannot import or consume admitted target snapshots. Neither step
creates Core approval, a durable grant, a Worker, a Task, a runtime lease, a
Docker process or a model/agent action. The tool remains qualified for the
fixed benign fixture ONLY, not for user-selected targets. Next add durable
Core operator selection, revocation, approval and claim-time snapshot/source/
WorkerAuthority/Task/Agent/Space identity binding before a carefully isolated
real target analysis and untrusted Evidence admission. No preflight fingerprint
may be used as execution authority. A separate
[Core operator-target ledger](v0.15-rea-operator-target-selection.md), merged
through PR #127, stores one authenticated, operator-asserted inert review per
snapshot ID and terminal revocation, but cannot independently verify target
bytes, grant an analyzer Worker, create a Task/lease or approve execution.
A distinct [read-only analysis-plan projection](v0.15-rea-analysis-plan-preview.md)
binds the selected target, draft Specialist, pinned source manifest and worker
authority policy fingerprints with fixed refusal reasons. It must never
create a Task, approval, Worker registration, target file read or execution grant.
An isolated, [separately opt-in source-built manual static-target proof](v0.15-rea-manual-static-target-qualification.md)
may qualify a benign operator-selected sealed snapshot *only after* independently
pinned source/runtime/target verification and exact container cleanup. It is
not an active Core Worker, Tool grant, approval or agent capability; a passing
mock/test suite is not a successful live native qualification. Do not infer
per-tool Core execution authority from a local probe receipt.
Importing MIT source does not grant host/browser/credential access and
preserves original legal notices. Complete current Core and v0.16 gates
before REA execution integration.

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

The REA v6 full-source import is kept in-tree. Its separate, operator-visible
draft reverse_engineering Specialist Agent, reverse_engineering Capability,
and rea_investigation Skill are pinned to this imported source. The source
advertises 139 observed tools but none is granted to Core or to an Agent.
The existing active Agent/Capability/Skill registry versions stay unchanged.
No Worker, task, computer, host/target/browser/network/credential permission
is enabled by the candidate. The plan in
[REA integration](v0.15-rea-integration.md) retains Core's original
approval/claim-time authority and source qualification. Runtime activation
must separately pass review; earlier v0.15, v0.16 and later milestones
retain their existing priority.

### v0.16 — Channels and presence

Merged PR #108 (bc6f50c, 4,965 tests passed and 2 skipped) provides the
contract-only v0.16 foundation: transport-neutral, **non-authoritative**
channel text envelopes, keyed opaque provider/thread/message identities
and reserved channel-turn idempotency keys.

The WhatsApp signature verifier checks Meta's X-Hub-Signature-256 against
the exact raw webhook bytes using a trusted server-owned App Secret.
The signed-text parser extracts exactly one bounded inbound text event,
refusing mixed, malformed, media or batched payloads; status-only events
produce no text turn. The inert projection derives keyed pseudonymous
WABA/phone/sender/thread/message identities under an independent Core
identity secret, producing only an unverified ChannelTextIngress.
These offline functions grant no provider account ownership, freshness,
replay, Mind admission or sending authority. See the signature, parser
and ingress documentation:
docs/v0.16-whatsapp-signature-foundation.md,
docs/v0.16-whatsapp-signed-text-shape.md and
docs/v0.16-whatsapp-opaque-ingress.md.

This foundation has no connected providers, live credentials or transport.
A separately qualified adapter must authenticate provider origin,
bind Ahmad-approved accounts to existing Mind conversations, persist inbound
replay/edits and outbound delivery receipts, and enforce send approval before
any WhatsApp/email/voice action is enabled. Provider text is always untrusted
data, never permission to execute a Core task. Live calling remains a separate
authority and transport qualification.

Merged PR #109 (bd586aa, 4,985 passed and 2 skipped) provides the
separate Core binding/observation ledger: an Ahmad-selected opaque thread
may map to an existing Mind conversation, but that mapping is not provider
authentication. Only message ID, text digest and UTC metadata are retained
with append-only replay checks; no external webhook, message-to-Mind admission,
delivery receipt or send authority is granted.

Merged PR #110 (b8a9634, 4,988 passed and 2 skipped) closes local
learned-skill promotion-link exclusivity. Neither the thread ledger nor a
valid provider HMAC authenticates a WhatsApp account.

The distinct operator-selected WhatsApp account ledger is a later, **inert**
Core gate. It records a chosen opaque WABA+receiving-phone identity bound to
an opaque trusted Meta App Secret identity, supports exact idempotent selection
and append-only irreversible revocation, but permanently denies claiming
provider authenticity, Mind admission or delivery authority. No secrets,
plaintext account IDs, ingress adapter, worker or API route are connected.
See `docs/v0.16-channel-operator-account-selection.md`. A future separately
qualified provider adapter must compare the signed, parsed provider facts
against a still-current operator choice and live trusted secret/registry
binding before any replay or Mind admission. Replay freshness and outbound
approval remain independent later gates.

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

An early **operator-opt-in, single Specialist pilot** is being qualified at
`docs/v0.17-youtube-research-specialist-pilot.md`. It binds a trusted
YouTube Research AgentSpec to Core's existing Research Worker, an explicit
read-only workspace Tool/Skill allowlist, an exact SHA-256 operator startup
approval and a fail-closed guard against invalidating existing live
AgentInstances. It is default-disabled, does not create an instance or
hierarchy automatically, and grants no YouTube publishing/credentials or
Campaign budget. This pilot does **not** complete the wider v0.17 team work.
A separately staged Core TaskService guard adds an eight-new-Research-Tasks
per rolling 24h per-Project quota for the explicitly enabled YouTube Research
Specialist. It uses the existing serialized Task INSERT and immutable agent
execution snapshot, counts terminal/retired instance history, denies competing
ninth submissions, and exposes a read-only operator quota view; it is **not**
a token, financial, attempt-level or Campaign budget. See
`docs/v0.17-youtube-research-admission-quota.md`.
A separate *claim-time* 12-Research-Worker-attempt/rolling-24h per-Project
limit closes the retry/replacement bypass against this new-Task quota. It
counts persisted TaskAttempt rows, denies a 13th claim before any Worker
dispatch and never claims token/money budget or new grants. See
`docs/v0.17-youtube-research-attempt-quota.md`.

The first separately operator-approved **real** four-role YouTube company is
now staged at `docs/v0.17-youtube-company-operational-hierarchy.md`. Distinct
from the earlier single-specialist pilot and inert Harness draft, it registers
reviewed Active VP/Director/Manager/Specialist AgentSpecs under a whole-registry
SHA-256 startup approval, an Organization coordination Skill and the existing
read-only Research Worker. A real database-backed test manually creates a
Division→Department→Team, the VP and three atomic next-level child
AgentInstances/assignments with three separate Campaign budget reservations
and active Delegations. It then claims real bound Research Tasks and verifies
revocation prevents a subsequent Worker claim. No company Team is automatically
created by startup or model text; a durable *operator-only bootstrap approval*,
measured Campaign spend and autonomous team coordination remain separate gates.
A separate 24-Research-Worker-attempt rolling 24-hour **shared Project/team**
limit is staged at `docs/v0.17-youtube-company-attempt-budget.md`. It
enforces before TaskAttempt insert under the existing serialized TaskService
writer, counting all four VP/Director/Manager/Specialist roles, failures and
retries across replaced AgentInstances. It does **not** claim currency/token
spending evidence, new tool/Space grants or autonomous Campaign decisions.
A separate **read-only, provider-reported token observation** at
`docs/v0.17-youtube-company-token-observation.md` aggregates only complete
ResearchRuntime usage metadata from successful, persisted TaskAttempts. Failed
attempts and incomplete/invalid provider reports remain visibly **unmetered**
rather than silently treated as zero. The metrics are unverified historical
evidence, explicitly NOT attested billing, currency authorization or a
provider-token hard ceiling.
An operator-only `GET /projects/{id}/youtube-company-status` live-read
surface is staged at `docs/v0.17-youtube-company-operational-status.md`.
It reads real project/role instances, Org assignments, the entire bounded
reporting/Delegation/Campaign hierarchy, current reservation expiry and
per-Role Research Task pulse, distinguishing partial/stale/paused/revoked
state rather than relying on Harness preview diagrams. It grants no
approval, agent creation, model/Tool access, or spending authority.
The next step remains an independently authorized, idempotent real
Company bootstrapping workflow and truthful provider cost enforcement.

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
