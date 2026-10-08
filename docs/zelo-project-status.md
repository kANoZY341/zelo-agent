# Zelo live project status

Latest checkpoint verified: 7 October 2026

This file records current implementation state. Architecture and roadmap intent
live in `docs/zelo-canonical-architecture-roadmap.md`; workspace direction lives
in `docs/zelo-workspace-decision.md`.

## Active increment: v0.15 promotion exclusivity and v0.16 channel ledger

Core-first work remains on Core v0.15. Merged PR #107 established the
governed learned-skill proposal admission guard. Merged PR #108 at
bc6f50c94b7d42c25a32462a8657606534dff457 added bounded inert channel:
text envelopes, HMAC pseudonymous identities and prepared-only replies;
4,965 tests passed, 2 skipped.

PR #109 merged at bd586aa702d2b8b414db8bd22ac2676575c194f4 with
4,985 tests passed, 2 skipped; its merged tree exactly matches the
reviewed source. It adds operator-selected opaque thread bindings and
append-only unverified inbound replay observations. These records store no
original message body and cannot claim provider authentication, account
ownership, Mind admission, execution, delivery, voice or calling authority.

PR #110 is the active unmerged v0.15 local promotion-link exclusivity
gate. Competing previously accepted skill revisions cannot both receive
new local promotion links. Historical reviewed decisions remain valid.
The separate PR #111 still provides the proposed generic GitHub
pre-merge publication refusal; a future identity-bound publication
reservation remains necessary. There is no automatic learned skill
runtime activation or expansion of Agent/Worker/Tool permissions.

Media native-reader qualification remains refused after four authorized
attempts; no fifth attempt is authorized. UI PR #61 remains draft and parked.

The separately proposed v0.15 REA catalog is a **draft-only** reverse-engineering
Specialist/Capability/Skill definition exposed only as an authenticated,
non-authoritative candidate snapshot plus pinned disabled external-tool
manifest. Live registry fingerprints remain unchanged, so active agent
instances do not become stale. This does not authorize REA execution,
install external packages,
change the active v0.15 merge queue, or unblock any worker/sandbox gate.


## Checkpoint history

The sections below preserve prior releases and implementation checkpoints. Their
older active-work descriptions are historical, not instructions to repeat proofs.
The `## Active increment:` heading above is a stable ContextResolver interface;
keep exactly one and update its content rather than renaming it.

## Published release

- Zelo Core v0.14.0 — Agent Factory is published.
- Release commit:
  `3b87bbe4dc9956fbb8b11108faa911c4b23de5c9`.
- Release tag: `v0.14.0`.
- GitHub Release published on 1 October 2026.
- Release preparation is PR #54.
- Independent release clone: 882 passed.
- Post-merge main CI run #36913099766 passed on the release commit.

## v0.14 released behavior

v0.14 added the bounded Agent Factory on top of v0.13 memory/context:
- trusted/versioned AgentSpec templates and deterministic activation evidence;
- durable project-bound AgentInstances with explicit lifecycle;
- scoped agent/project memory assembly;
- bounded parent-child delegation contracts;
- exact AgentSpec/delegation execution identity on Core tasks;
- durable divisions, departments, teams and reporting assignments;
- hierarchy-aware permanent child creation;
- depth, budget, compatibility and direct-report limits;
- fail-closed stale/paused/retired-agent and revoked-delegation handling.

The canonical authority chain is:

`Managing Director -> Zelo CEO -> VP -> Director -> Manager -> Specialist -> Worker -> Tool`

Organizational reporting and temporary task delegation remain separate graphs.
Titles never widen tool or worker authority.

Merged v0.14 implementation work is PRs #47 through #53.
Release preparation is PR #54.

## Parallel v0.15 preparation — persistent Agent computers (5 October 2026)

The active Media native-reader slice remains unchanged. PR #94 is merged at
`46da8794e6b6b4c9fd55fd449bc4e6439acaf710` and provides the persistent-computer
foundation: pinned OpenBot/OpenDots-derived supervisor/computer deployment,
Zelo-owned fail-closed backend adapter, and canonical
AgentSpec/skill/Space/computer metadata.

PR #95 is merged at `905560dbdf45e31fa148f0c0f30bbb00e90503f2`. It adds
persisted per-AgentInstance computer grants, append-only computer activity and
owner-only status/permission/start/stop/take/release routes. The first CI attempt
exposed three stale migration expectations; follow-up commit `667ef18` corrected
them and CI run #2001 passed before merge.

PR #96 is merged at `825d25aed68be425176d706c61aa99ff6213a291` and adds
the first Agent-executable Computer Worker, deliberately **read-only only**:
`browser_snapshot`, `browser_read`, `files_list`, and `files_read`.
`computer_run` is Agent-only and binds exact Agent identity, Supervisor
selection, worker-authority policy, persisted computer permission digest and
OpenBot backend identity. Claim revalidates all of them before an attempt is
created.

Live Zelo-host qualification succeeded against the real pinned OpenBot computer:
an Agent read `qualification/proof.txt` through `computer_run`, then a second
queued read was cancelled with `computer_execution_spec_invalid` and zero
attempts after Files permission was revoked. A claimed browser read was also
refused with `computer_human_control_active` while the owner held control; after
handback, OpenBot reported `resume_snapshot_required=true`. The disposable
container, profile, workspace and supervisor were removed after the proof.

PR #97 is merged at `b67d0d85aa24e2c2a376e1baa08996788fe86b1d` and adds
the **browser mutation egress prerequisite**, not navigation itself:
Agent-scoped canonical HTTPS endpoint grants, a derived per-Agent proxy
credential, Core-owned public-address DNS resolution and a bounded CONNECT proxy.
The proxy revalidates current Agent/permission/endpoint authority on connection
establishment, after DNS and before forwarding tunnel chunks, so revocation
blocks new and already-open traffic. `computer_navigate` is registered as a
capability but no mutation action is exposed yet. Exact-head CI run #2009 passed.
See `docs/v0.15-computer-browser-mutation-egress-gate.md`.

PR #98 is the internal-topology gate and has passed the live Zelo-host proof.
Agent computers join an internal-only Docker network, Core control uses the
validated private computer IP, and public browser traffic can leave only through
the Core proxy bound to the bridge gateway. In the latest disposable proof the
computer was `172.31.250.3:4100`, direct public curl was blocked, real Chromium
reached allowlisted `https://example.com` through the proxy with title
`Example Domain`, then Browser permission revocation caused the next navigation
to fail. The Agent container did not contain either host-only master token. All
qualification resources were removed. See
`docs/v0.15-computer-internal-topology.md`.

PR #99 is the stacked first browser-mutation slice. It adds only
`browser_navigate` using `endpoint_id + bounded path`; arbitrary URL input is
not part of the task contract. Navigation selects a separate
`computer_navigate_core` Worker with `agent-computer-navigate-v1` authority,
freezes the exact endpoint-registry and egress-policy identity, revalidates
permission/backend/endpoint state before and after execution, remains blocked by
human takeover, and rejects a final cross-origin page even if OpenBot reports
success. Click/type/key/scroll remain later gates.

Workspace writes, shell, credentials, screenshots in Agent results, host access,
host fallback and the parked UI remain out of scope. See
`docs/v0.15-agent-computer-control.md` and
`docs/v0.15-computer-worker-read.md`.

Canonical revision 3.3 now records that persistent factory-managed Agents own
explicit instructions, memory, skills, Space/tool grants and authority; Agents
that need execution may later receive a persistent per-AgentInstance computer.
The future Control Desk Computer Panel and governed skill-learning lifecycle are
also canonical. See `docs/v0.15-agent-computer-openbot-foundation.md`.

## v0.15 merged foundations

Capability Registry is merged through PR #55 at:

`4dcc5bf03dfe761d64df49f848762a163158d8df`

Skill Registry is merged through PR #56 at:

`7e0936750967be79ec49ee5858a508935d7ad3e0`

Together they provide trusted capability identity, agent/worker subject checks,
activation policy v2 evidence binding, immutable skill/procedure compatibility,
and operator-authenticated read-only `GET /capabilities` and `GET /skills`.

Capability and skill metadata remain non-authoritative. Supervisor routing,
WorkerAuthority, ToolPolicy, task validation, approvals, budgets and audit remain
authoritative.

Generic Worker Registry is merged through PR #57 at:

`9d2e897a6c8bebf63fc52dec7b6442c24cc9f8aa`

## Research Worker merged

Research Worker is merged through PR #58 at:

`c16f21381a2316435091f35faa82b609534cefa3`

It provides the trusted `research_core` backend, Research AgentSpec v2, capability
v2 worker subject, exact persisted selection, claim/Runner revalidation and unchanged
workspace-read/no-network evidence retrieval.

## Python Worker merged

Python Worker is merged through PR #59 at:

`c8aab211b52cbc576d8457b61d01134fcab645e5`

It provides the trusted `python_core` backend, isolated-compute authority, migration
`0027_python_worker_selection`, exact persisted selection, claim/Runner
revalidation, and bounded no-workspace/no-network Python computation.

## API Worker merged

API Worker PR #60 is merged at:

`8d33d563070c58c487dd06e91fd9af617744dab3`

It adds `api_core`, `api_read`, migration `0028_api_worker_selection`, exact
endpoint/policy/worker identity, claim/Runner revalidation and bounded GET-only
JSON access to operator-configured HTTPS origins. Mind API routing remains disabled.

Reviewed head `eebdb6d996f84bdb60ccb7cd483da9e29cc653b8` passed independent
Astra Medium review and exact-head CI `37033261497`: 1048 passed, 2 skipped.
Both skipped installed-Codex probes passed separately on the host at the same head.
The expanded host-focused suite passed 216 tests. TLS/HTTP diagnostics and the
controlled-clock negative controls are recorded in the PR discussion.

## Latest priority — Core first; UI parked (2 October 2026)

Ahmad reports that the UI still needs major work and directs Core development to
continue now, with UI redesign/integration revisited at the end. This supersedes
the earlier U2-first delivery plan and provisional design acceptance.

Preserve UI PR #61 as a draft and do not merge it automatically, even if CI passes.
Preserve `feat/v0.15-live-conversation-ui` at `95f35cf` and the independent original
design worktree. Do not delete UI code, chats, backups or local beta data.
The active work is v0.15 Media package-reader source/provenance research after
PR #86. Continue the established Core roadmap; later UI rework requires fresh
acceptance. Do not spend this phase polishing, deploying or acceptance-testing UI.

## Browser broker qualification merged

PR #62 is merged at `939cbd7dc49f7e5e74a7659d13a0c2e92b7f5aef`. It adds
standalone qualification-only broker/DNS modules; Core startup, runtime workers,
API Worker, dependencies and UI remain unchanged. Exact reviewed head `c9d387e`
passed CI #1842 with 1114 passed and 2 skipped, and the two installed-Codex
probes passed separately on the host. Independent Astra Medium review and
the 169-case expanded focused gate passed. See the PR for diagnostic evidence.

## Historical increment: v0.15 Browser renderer integration

Branch: `feat/v0.15-browser-renderer-integration`. The broker, fixed anonymous-pipe
renderer and crash-fenced owner are now implemented together in qualification code.
The opt-in host harness verifies actual prepared image payloads and exercised ten
real integration/failure/cleanup cases with controlled HTTP fixtures. No Browser
Worker is active; review/full CI and later Core execution binding remain gates.
See `docs/v0.15-browser-renderer-integration.md` for exact scope and evidence.

UI PR #61 remains parked, draft and unmerged by Ahmad's latest decision.
Do not return to UI integration or spend this phase redesigning it.

## v0.15 continuation

Qualify browser isolation and controlled egress before implementing the next
bounded runtime surface. Preserve explicit capability, skill, worker identity,
filesystem, credential, network and approval boundaries; retain all normal merge gates.

## Historical browser runtime admission/resource implementation checkpoint

The active runtime-qualification branch implements capacity-one cross-process
admission, crash fencing, owned cleanup and frozen resource/execution-policy
contracts. Optional controlled host probes demonstrated 32 MiB/no-swap memory,
12 processes, CPU quota/throttling, 8 MiB tmpfs and full owned-container cleanup.
These are prerequisite checks, not an activated or fully qualified Browser Worker.
The expanded host-focused suite passed 242 tests. Review/full CI precede merging.
UI PR #61 remains parked and its branches/data are untouched.


Follow-up review fixes: the optional PID-1 fixture watchdog now has an explicit
signal handler and a passing real self-exit probe; exceptional image/container
cleanup records unproven outcomes and attempts every owned removal. Final serial
host gate: **248 passed**, three existing warnings; static gates pass. A native
Chromium/default-Docker compatibility probe failed closed (`No usable sandbox`),
so the actual browser runtime remains unqualified and no bypass was enabled.

## Resource/admission prerequisite merged; artifact qualification active

PR #63 merged at `2bb48bb98e99ea02a52002a44b5402c5a9da1ae9` after exact-head
CI #1847: 1193 passed, 2 skipped. Both identified installed-Codex probes passed
separately at the reviewed head. Independent Astra Medium review found no blocker
in its qualification scope. No Browser runtime was activated.

The active artifact slice adds bounded byte-level inventories and rescans against
an independently trusted manifest digest. Host validation: 107 new artifact cases
passed (including actual Unix-socket rejection), 355 tests in the expanded gate.
A real 339-file prepared candidate bundle (291287637 bytes) was captured/rescanned;
the browser binary matched the previously recorded SHA-256. This does not approve
the bundle's provenance or authorize execution. Native browser/container sandbox
compatibility remains unresolved; UI remains parked.

## Artifact prerequisite merged; native compatibility checkpoint

PR #64 merged at `7cdd791c9ed787a9e928e17fb2ad48eb43bd42f3`. Exact-head
CI passed 1300 tests with 2 host-only skips. A fresh real clone passed all
107 artifact tests plus both installed-Codex probes (109 total). Saved independent
Astra Medium review reported no blocker within the documented prerequisite scope.

The active native-sandbox qualification now has a pinned Moby profile adapter
that preserves upstream default denial and appends exactly five x86-64 rules
for Chromium's nested namespace/empty-root confinement. The isolated diagnostic
rendered a fixed trusted document with native sandboxing enabled and no added
container capabilities. Kernel observations: renderer seccomp mode 2/two filters
versus one container filter, no-new-privileges, zero effective renderer capabilities,
nested PID namespace; 512 MiB/no-swap, 128 PID and half-core cgroup limits matched.
All owned diagnostic containers/images were removed. No external page was visited.

This resolves the observed native compatibility failure for the tested candidate,
not the entire Browser Worker. The combined broker/renderer/owner execution path,
policy/executable attachment and task lifecycle still require qualification.
UI remains parked, and production application code is unchanged.

## Native sandbox compatibility prerequisite merged

PR #65 is merged at `e16d370939902d4180237933f5e61d294c651b00`. Exact
reviewed head `6258b426247f9d6833363c1ec9cf443873bfd4e5` passed full CI
#1853 (`37059876485`): 1417 passed, 2 skipped, 11 warnings. The 2 skipped
installed-Codex checks passed in the 119-test supplemental exact-head host run.
Independent Astra Medium static review found no blocker in qualification scope;
expanded qualification tests passed 369 and project-context tests passed 3.

The prior `No usable sandbox` failure is resolved for the tested pinned candidate
without disabling Chromium sandboxing or granting container capabilities. A
separate bounded anonymous-pipe diagnostic completed a fixed document DOM
roundtrip with page script execution disabled and all owned resources cleaned.
This is not production renderer integration or Browser Worker activation.

The next integration work is scoped in `v0.15-browser-renderer-integration.md`.
Use one trusted admission/ownership lifecycle, immutable approved image/policy,
fixed bounded protocol commands, broker-only retrieval and cleanup-confirmed
results. UI remains parked and no later milestone has been reordered.

## Combined renderer host checkpoint — 3 October 2026

The new focused renderer/host harness/protocol regression tests passed 117 cases.
Ten real fixed-content broker-to-browser lifecycle cases passed with no external
network. Artifact bytes were checked inside an exported unstarted image, not only
before building it. Long DOM previews are reported truncated rather than complete.
Lost-create acknowledgement, cancellation and resource-flood paths retained owned
cleanup and admission fencing. Production app and parked UI are unchanged.
