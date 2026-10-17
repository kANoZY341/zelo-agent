# Zelo live project status

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

**16 October full-scope mandate:** The Managing Director explicitly rejects
a reduced-scope v0.15 release. Worker execution, effective per-Agent Space
rights, governed learned-Skill publication, Media, and the selected integrations
must be operationally qualified before v0.15 can ship. No disabled foundation
counts as complete. The four consumed native Media reader proof attempts still
cannot be reset or extended without separate explicit authorization.

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

Latest priority decision: **12 October 2026**. Historical 15 October Core
`main` checkpoint: `a603a136623778abe30f98bb4bf74d932c2441a7`
(merged [PR #183](https://github.com/kANoZY341/zelo-agent/pull/183),
revoked release-SHA retry protection). PR #183's
[exact-head CI 38751369757](https://github.com/kANoZY341/zelo-agent/actions/runs/38751369757)
**PASS** (6,220 passed, 2 skipped; Ruff/diff PASS), after predecessor
[post-#182 main CI 38750520588](https://github.com/kANoZY341/zelo-agent/actions/runs/38750520588)
**PASS**. New
[post-#183 main CI 38761948344](https://github.com/kANoZY341/zelo-agent/actions/runs/38761948344)
is **IN PROGRESS, not a PASS** at this checkpoint.
Historical failed #176 main and initial #179 exact-head receipts remain
failures, with subsequent corrective green runs kept separate. No code/CI
result constitutes v0.15 release qualification.

This file records implementation and qualifications, while the canonical
architecture and roadmap remain in `docs/zelo-canonical-architecture-roadmap.md`.

## Active increment: v0.15 release closeout — HOLD

The Managing Director directed completion of **v0.15 first**. Tracker:
[issue #166](https://github.com/kANoZY341/zelo-agent/issues/166) and
`docs/v0.15-release-closeout.md`. The last published version is still
`v0.14.0`; v0.15.0 is **not released or ready to tag**.

Priority release blockers include complete Space/Core permission scope and
negative path tests; publication-time exclusive learned-skill approval/claim;
exact-head qualification of all included Workers and third-party adapters;
Media native-reader's four exhausted refused proof attempts; independent
full-repo review; and an explicit operator release-scope decision where
a required runtime remains disabled. No fifth native proof is authorized.

New v0.16, v0.17, v0.18 and UI feature promotion is paused except for necessary
security/regression repairs. Existing code and branches are preserved:
PR #61 stays parked, the local v0.17 atomic-hire branch stays unmerged,
and merged ahead-of-version company/channel code does not imply release.
Never classify imported REA source, OCR/ECC candidates, read-only Space
status projections or speculative tool permissions as activated runtime.

## Historical checkpoint: 10 October — v0.15 publication refusal and inert v0.16 channels

Current checkpoint (10 October 2026): repairs #142 and #141, including #140
ancestry, are complete. PR #143 merged the engineering pack with exact-head
and post-merge CI green. The public Browser → Mind → Research path passed
operational checks; its raw answer failed semantic review. A corrected
operator-reviewed summary was separately admitted, recalled in full, isolated
from the exclusion project and replayed without duplication.

Hindsight comparison on the fixed public/synthetic workload: Core and
Core-filtered Hindsight each passed 8/10 grounded-correct cases; raw Hindsight
passed 6/10. This bounded run does not justify replacing Core memory.
This evaluation does not replace Core memory or its scope and lifecycle authority.

The engineering focus remains v0.15; the published release remains v0.14.0.
UI PR #61 stays parked, REA stays inactive, and no fifth Media native-reader
qualification is authorized.

Core-first work remains on Core v0.15. Merged PR #107 established learned-skill
proposal admission. Merged PR #108 at
bc6f50c94b7d42c25a32462a8657606534dff457 added channel: text
envelopes and HMAC pseudonymous identities (4,965 tests passed, 2 skipped).
PR #109 merged at bd586aa702d2b8b414db8bd22ac2676575c194f4 with
4,985 tests passed, 2 skipped: operator-selected opaque thread bindings
and append-only unverified observations never admit Mind turns or delivery.

PR #110 merged at b8a963462a926e24517c884c7ceb0af454f9fa10:
4,988 tests passed, 2 skipped, with an identical reviewed and merged
tree. Competing previously accepted skill revisions cannot gain a second
local promotion link. Exact historical proof retries remain idempotent.

Media native-reader qualification remains refused after four authorized
attempts; no fifth attempt is authorized. UI PR #61 remains draft and parked.

PR #111 has merged the generic pre-merge publication refusal for protected
learned-skill files. PR #117 also merged an inert identity-bound publication
intent contract. These gates do not authorize runtime skill activation,
external GitHub publication or new Tool/Worker permissions.

The v0.16 channel security stack is now merged through PRs #112, #113,
#114 and #115: offline exact raw-body WhatsApp HMAC verification, strict
single text-event extraction, unverified pseudonymous ingress, and an
append-only operator-selected/revocable WABA-plus-receiving-phone identity
ledger. All default permissions for Mind admission, provider authenticity
and outbound delivery remain false. No real Meta account/credential,
network webhook listener, Mind turn, sending, voice or calling is enabled.

Merged PR #119 (main af6c209) delivered the complete, pinned REA 6.0.0
source snapshot (2,082 upstream files and five optional pinned gitlinks),
the inert Specialist candidate and the fixed-fixture source-built MCP proof.
Its exact-head full CI and post-merge main CI both passed. PR #116's
implementation was merged through #119; PR #118 was closed only after its
implementation and tests were verified on main. The source manifest and
verifier remain non-authoritative: no REA Worker, Tool, Agent activation,
native engine, user-target execution or network access is authorized.

Merged #121 tightened the fixed static-fixture identity; #122 adds the
operator-only, 139-tool name inventory; and #123 (main 36937ef) delivers
explicitly selected, bounded, sealed text-only target snapshots under a fixed
private inbox. Neither target admission nor the preflight review proposal
approves or runs that target. PR #125, merged at `3d1c8bad4dedffa4289e177112f53ccdc1797076`,
fixes two independent test-only Core CI scheduling/procfs observation races.
Its exact-head full CI and post-merge `main` CI both passed.
Merged #126 (main 492f6c8) delivers an explicitly opted-in offline local
target preflight CLI that rechecks sealed content, source and catalog hashes
and emits an inert preview checksum. No Core API imports admitted files.
Merged #127 (main 2b0610e) adds a Core-owned, append-only, authenticated
operator selection and irreversible revocation ledger for **asserted**, not
Core-verified, REA target identities. Its exact-head full CI and post-merge
`main` CI both passed. All execution, Worker, Tool, Task and approval
permissions remain false.

Merged PR #128 (main `4a8d4c2ebb3c10e3c6851194d8ead2b5b882bdcf`)
delivers the read-only, denied analysis-plan projection for the selected
asserted target, draft REA Specialist and current WorkerAuthority identity.
It emits all five fixed refusal reasons; it never reads target bytes, creates
a Task, invokes an analyzer or grants execution authority. Its exact-head
full CI passed; post-merge `main` CI remains a separate pending gate.

A subsequent strictly manual **offline sealed-target static qualification**
codepath is prepared behind an explicit local CLI switch and its own pinned
no-network sandbox policy. It verifies copied target contents against the
independently retained receipt before allowing exactly one source-owned static
REA MCP operation in an isolated Docker guest. No general-target native proof
has yet been run or claimed successful. An initial read-only runtime diagnostic
used the unresolved `runtime-v6` symlink path and misleadingly reported
`rea_runtime_symlink_escape`. The normal runtime qualification uses the
canonical resolved physical path; an independent check with that same path
**passed** all pinned REA 6.0.0 source, module, and dist hashes, and confirmed
no network or Core Worker authority. The symlink escape guard remains intact
for any links escaping that canonical root. No selected-target Docker proof was
run. The offline operator qualifier now also enforces an exclusive local lock;
unreconciled private stage directories block subsequent probes. This is not
Core execution, approval, Worker registration, permission propagation or
Evidence admission; those remain future separately reviewed gates.


## Community reuse / engineering integration program

The Managing Director selected a six-stage reuse order for Open Code Review,
ECC, Harness, Archify, Context Mode *concepts*, and unfinished OpenDots
Computer Panel/Spaces/learning. The first isolated Core slice snapshots 19
reviewed upstream source/license files at exact commits (and **no** Context
Mode source), builds a read-only OCR finding-to-Zelo-review adapter grounded in
Core-approved changed lines, six draft skill candidates, two draft four-tier
specialist teams, an Archify-compatible static architecture projection,
an independently written bounded tool-output index, and a read-only Core
Computer Panel data projection. See `v0.15-curated-community-import-program.md`.
No borrowed instructions execute automatically; these preview features grant
no Worker, Task, Agent, tool, Space, publication or CI merge permission.
The actual React Computer Panel, executable code-review Worker, skill promotion,
full Archify renderer and Space/skill-learning enforcement remain separate gates.
The follow-up OCR trusted-Git-scope candidate independently derives reviewable
added-line anchors from pinned Git commits using read-only bounded commands.
It rejects deleted files, deletion-only hunks, custom diff drivers, binary or
special files, mutable attribute overrides, stale revisions and unsupported Git.
The resulting checksum-bound reviewer evidence grants no new authority; no
Coding Specialist or CI integration is activated. See
`v0.15-curated-ocr-trusted-git-review.md`.
Four reviewed ECC manual playbooks now describe operator procedures for security
review, agent evaluation, AI regression and architecture decisions. They remain
draft, checksum-bound references with no Worker, Tool, Task or Skill Registry
activation. See v0.15-ecc-manual-engineering-playbooks.md.
Harness now also offers operator-only YouTube content and Trading research draft
teams, each with four VP-to-Specialist levels. Neither template registers live
Agents or grants publishing, trading, Tool, Worker, Task or Space access. See
v0.15-harness-specialist-team-drafts.md.
Archify now has a second operator-only view grounded in the trusted Agent Factory
registry: exact AgentSpec/DepartmentSpec templates, version fingerprint,
deterministic JSON/Mermaid and digest. It never claims that templates are running
Agents or that edges grant authority. See v0.15-archify-agent-registry-projection.md.
The separate OpenDots Specialist profile candidate exposes only trust-bound,
operator-visible AgentSpec instructions, memory namespace, eligible skill
metadata, declared Spaces and tools; no new authority (see
v0.15-opendots-specialist-profile.md and issue #131).
UI PR #61 remains parked until the Core-first development priority changes.

Context Mode concept continuation: a separate operator-only, private SQLite
archive now supports digest-bound explicit windows and manual purge, with
project/operator scope isolation and no automatic ContextResolver or model
prompt admission. This code is Zelo-authored, not copied Elastic-2.0 source.
See v0.15-context-mode-explicit-archive.md.

OpenDots Space intent bookkeeping is merged in PR #138: short-lived
operator-reviewed, AgentInstance/spec/project-bound grant intents are append-only
and revocable. They cannot grant effective Worker, Tool, Computer or model
context access. See v0.15-opendots-space-grant-intent-foundation.md.

Computer Worker hardening additionally rechecks the exact permission/backend/
endpoint binding immediately before dispatch, after the control-state await
and started-event callback. It prevents the reproduced post-revocation access
window; it does not activate Space grants or make remote access atomic.
See v0.15-computer-dispatch-revalidation.md.

OpenDots governed learning-history inspection is now a separate operator-only
Specialist view over existing Skill Learning records. It reports bounded
per-AgentInstance proposal status, revision digest and review/promotion-link
evidence but never instructions, automatic learning or executable Skills.
See v0.15-opendots-specialist-learning-history.md.

The repair and merge queue is complete. REA repair #142 merged at
`dfba93fb47bbb0b02c8455e15170ecb337d687af`, and combined Computer dispatch
repair #141 merged at `84bebc6558ffc20911e057bead66ac42fe4dc097` with the
complete #140 Learning History head in its Git ancestry. Exact-head CI
[38012355408](https://github.com/kANoZY341/zelo-agent/actions/runs/38012355408)
and actual post-merge main CI
[38015425568](https://github.com/kANoZY341/zelo-agent/actions/runs/38015425568)
each passed 5,550 tests with two skips. The merged tree equals the independently
reviewed and tested tree `895ebe41b816ef9712214f0765e199f31fbdafca`.

## Selected engineering and public research continuation

The Managing Director selected the sequence: the focused Addy Osmani and
Matt Pocock engineering pack, a complete public research workflow using current
Zelo memory, then a bounded Hindsight comparison on the same work.

The [engineering development pack](development/engineering-pack.md) merged in
PR #143 at `b1e122aaa673a054606932d9b40e9913ab7e1d97`.
[Exact-head CI 38031905463](https://github.com/kANoZY341/zelo-agent/actions/runs/38031905463)
and [post-merge main CI 38033369042](https://github.com/kANoZY341/zelo-agent/actions/runs/38033369042)
passed. Post-merge main recorded 5,550 passed, two skipped and 42 warnings in
1,232.30 seconds. These are the engineering-pack merge gates. Four adapted
procedures are connected through root `AGENTS.md`; exact MIT source files and
supporting references remain under `integrations/development/` and grant no
runtime Skill Registry or execution authority.

The [public research workflow](v0.15-public-research-workflow.md#observed-result)
completed its operational and separately reviewed project-memory path in run
`2ff6423a809a4ddbb242f471f2a56b32`. Browser retrieval, Mind delegation, Research
execution, citations and cleanup passed. The unedited answer failed semantic
and completeness review. A corrected source-grounded operator summary was
separately approved and admitted; full scoped recall, exclusion-project
isolation and idempotent replay passed. The original answer, operational
report, semantic review and memory receipt remain unchanged.

The [bounded Hindsight comparison](v0.15-hindsight-memory-comparison.md) evaluates
native recall on a frozen public/synthetic workload with Core scope and
lifecycle filtering and separate operator grading. Its retained outcome and
operator assessment are recorded in that guide and the active checkpoint
above. The experiment grants no production memory promotion or backend switch.

RSS/Atom ingestion and automatic public-video transcript retrieval are not
established by the source-file workflow. HyperFrames and VoiceStudio remain
deferred to their corresponding production tasks. The engineering focus remains
v0.15, with the existing later milestone order and Core authority boundaries.

## REA source-owned candidate preparation

Main now contains the REA v6 source and its observed 139-tool catalog,
the draft Specialist/Capability/Skill and operator-only candidate view,
and a source-built fixed-fixture MCP static reader proven in an isolated
no-network Docker guest. The fixture digest is independently pinned at the
analyzer boundary before Docker launch, so the CLI's fixture preparation
is not the only defense against substituted files. This is not a generalized
REA Worker. No runtime host, user-target, network or secret access is granted;
Media refusal history and UI PR #61 remain preserved.

## REA manual-profile CI correction (10 October 2026)

The post-merge main check at 6eab7b9 failed one REA profile-read check with
`rea_target_profile_changed`; 5,491 tests passed and two were skipped. An
isolated correction compares stable file identity and nanosecond mutation
metadata rather than access time, preserving exact profile SHA-256 and all
consent/authority gates. Deterministic atime-only cases fail with the original
reader and pass with the fix; protected metadata changes still refuse.
The correction merged in #142 after independent review and exact-head CI
[38008763900](https://github.com/kANoZY341/zelo-agent/actions/runs/38008763900).
Its post-merge main CI
[38011976160](https://github.com/kANoZY341/zelo-agent/actions/runs/38011976160)
also passed. This changes no native qualification permission. See
v0.15-rea-profile-stable-identity.md.

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
