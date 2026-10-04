# Zelo live project status

Latest checkpoint verified: 5 October 2026

This file records current implementation state. Architecture and roadmap intent
live in `docs/zelo-canonical-architecture-roadmap.md`; workspace direction lives
in `docs/zelo-workspace-decision.md`.

## Active increment: v0.15 Media native-reader offline boundary

Core v0.15 stays active; UI is parked. PR #91 merged at 09b68a5 after 4,347 CI
passes/two skips. Its one cleared 40-case ELF batch is version-proof attempt 3/4;
the eight-node declaration graph closes dpkg-deb over the fixed libraries/loader
but does not prove actual loading, symbols, ABI, provenance or runtime authority.

The current slice is offline only. One canonical container contract binds the
PR #91 proof/lock, inert content-addressed image identity, fixed loader/argv,
PATH/HOME/TMPDIR=/nonexistent, runc, network none, read-only root, uid/gid65534,
cap-drop ALL, no-new-privileges plus the exact vendored Moby default seccomp,
512 MiB/no-extra-swap, PID8, half CPU and nofile32. A deterministic scratch-image
manifest contains only the eight fixed binaries; entrypoint/env/user stay outside
the image and are applied only at container creation.

A separate fixed-fixture module creates four bounded synthetic Debian inputs
(positive, bad version, wrong order, truncated). The Media-specific Docker owner
has fake-only tests for exact inspect validation, output bounds, lost create
acknowledgement, cleanup failure, profile drift and repeated cancellation while
the capacity-one admission remains fenced. **No Docker daemon, loader, dpkg-deb,
compressor or acquired archive has run in this slice.** The consolidated image,
fixture, seccomp, contract and owner gates pass 158 offline tests.

Next: freeze/review this source and merge it, then implement the one-shot operator
that builds/exports/attests the inert scratch image and runs only the four fixed
synthetic cases. Actual cgroup/process-tree evidence and complete cleanup precede
any real authenticated package. USTAR remains narrow (GNU/PAX unsupported), then
Media still needs real member/runtime/import closure, decoder/filter qualification,
Core binding and release. Proof quotas/failures remain preserved; no model/UI/live
system/config/data change occurred and v0.14.0 is unchanged.

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

The active Media native-reader slice remains unchanged. In parallel, an isolated
branch prepares the later v0.15 Computer Worker foundation by reusing the
MIT-licensed OpenBot computer supervisor/service pattern already reviewed by
CopilotKit/OpenDots.

The preparation adds only trusted metadata/configuration, a fail-closed
Zelo-owned lifecycle adapter, exact pinned OpenBot deployment/provenance, and
tests. It does **not** register a Computer Worker, add a task kind, expose
browser/files/shell execution, merge the parked UI, or widen any Agent authority.

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
