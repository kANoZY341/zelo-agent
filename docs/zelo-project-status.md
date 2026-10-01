# Zelo live project status

Verified: 1 October 2026

This file records current implementation state. Architecture and roadmap intent
live in `docs/zelo-canonical-architecture-roadmap.md`; workspace direction lives
in `docs/zelo-workspace-decision.md`.

## Published release

- Zelo Core v0.13.0 — Memory, Context and Evidence is published.
- Release commit:
  `adc43d856a29fdcacdba80a273566aefaa0b98f0`.
- Release tag: `v0.13.0`.
- GitHub Release published on 30 September 2026 local time.
- Post-merge main CI #1601 passed.

## v0.13 released behavior

v0.13 added the durable context and evidence foundation on top of v0.12:

- scoped personal, project and agent memory records;
- provenance, expiry, correction and supersession;
- deterministic bounded lexical retrieval with explicit scopes;
- bounded personal-memory injection into Zelo Mind with untrusted-instruction handling;
- deterministic memory evaluation and contamination-boundary coverage;
- worker/model performance and reported-cost evidence with no invented pricing;
- Zelo-owned storage contracts and backend selection;
- SQLite retained as the enabled backend with current transaction, migration,
  WAL, privacy and single-runner guarantees preserved.

Merged v0.13 implementation work is PRs #41, #42, #43, #44 and #45.
Release preparation is PR #46.

v0.13 does not add automatic memory writes, embeddings/vector retrieval,
Postgres/Supabase storage, Agent Factory execution, approval bypasses or new
merge/release authority.

## Active increment: v0.14 Release preparation

The complete v0.14 Agent Factory implementation is merged on `main` through
PRs #47-#53 at merge commit
`927f63b55babeb8eca8f6b9a67d59d49241b3088`:

- PR #47 → trusted/versioned AgentSpec registry;
- PR #48 → project-bound AgentInstance lifecycle and scoped memory;
- PR #49 → bounded parent-child delegation;
- PR #50 → generic Coding/Research execution identity;
- PR #51 → durable organizational hierarchy;
- PR #52 → hierarchy-aware permanent child creation;
- PR #53 → deterministic AgentSpec activation/evaluation gates, exact
  implementation head `4e23c8ad49f2132cf27f12a5a11062dfd61f17a0`.

The release-preparation branch is:

`chore/v0.14-release-prep`

v0.14 now provides trusted AgentSpec templates, durable project-bound
AgentInstances, explicit lifecycle transitions, scoped agent/project memory,
bounded delegation, exact task/delegation identity, durable divisions /
departments / teams and reporting assignments, hierarchy-aware child creation,
and deterministic activation evidence.

The canonical authority chain remains:

`Managing Director -> Zelo CEO -> VP -> Director -> Manager -> Specialist -> Worker -> Tool`

Organizational reporting and temporary task delegation remain separate graphs.
Titles never widen tool or worker authority. Stale/paused/retired agents,
revoked delegations, incompatible hierarchy assignments and rejected active
AgentSpecs fail closed.

This release-preparation slice changes version metadata, tests and documentation
only. It does not add new runtime authority, AgentSpecs, migrations,
dependencies, capabilities or v0.15 work.

## v0.14 release gate

Before publishing `v0.14.0`:

1. keep release preparation based on the merged PR #53 `main` baseline;
2. run diff check, Ruff lint and Ruff format;
3. run the full pytest suite from a real repository clone;
4. require independent/adversarial release review;
5. require exact-head GitHub CI;
6. merge the exact reviewed release-prep head;
7. require post-merge `main` CI;
8. tag and publish `v0.14.0` from the resulting `main` commit.

Only after v0.14.0 is published does implementation advance to v0.15 Capability
and skills ecosystem.
