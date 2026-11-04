# Two Computer Space Acceptance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Produce an exact-source, disposable native receipt for two distinct AgentInstance computers and independently approved delegated Space file use.

**Architecture:** A standalone operator command creates its own temporary Core database, Space root, credentials, Docker namespace, internal networks, and loopback supervisor. It uses the real pinned OpenBot supervisor and child images with current Core services, then removes only resources labeled with its unique run ID. It does not change application authority or default configuration.

**Tech Stack:** Python, current Zelo Core services, SQLite, Docker/OpenBot.

**Spec:** `docs/v0.15-release-closeout.md` R1 and `tests/test_delegated_space_files.py` dual approval contract.

## Global Constraints

- Media remains deferred and unavailable; no Reader request.
- Use two different AgentInstance UUIDs and two running native child containers under one backend identity.
- Require separate parent and child Space intents and activations, a live one-hop delegation and Campaign budget.
- A revoked grant or delegation must refuse a new Task before physical read.
- Preserve production data, dirty UI checkout, existing Docker resources, and all secrets.
- Keep the supervisor bound to `127.0.0.1`; child containers have no host port.
- Exact-source review and a real host receipt are separate gates; neither implies v0.15 release.

## Review Focus

- Existing Docker name or network collision must refuse before mutation.
- If a child operation fails, cleanup must remove only this run's resources.
- A parent activation cannot substitute for the child's activation.
- A human-held Computer and stale approval remain covered by the existing offline boundary suite; this bounded host run does not claim those races.

---

### Task 1: Disposable harness and preflight

**Files:** Create `scripts/qualify_two_computer_space.py`.

**Interfaces:** `source_sha() -> str`, `namespace_containers(tag: str) -> list[str]`, `namespace_volumes(tag: str) -> list[str]`, `namespace_networks(tag: str) -> list[str]`, `main() -> None`.

- [x] Implement read-only preflight with exact image digests and a clean source SHA.
- [x] Implement unique resource names and verify label, image, network and exact expected names before cleanup, including failure before either UUID is returned.
- [x] Run Python syntax, Ruff, and `git diff --check`.

### Task 2: Two native Computer and Space journey

**Files:** Modify `scripts/qualify_two_computer_space.py`.

**Interfaces:** `async qualify(settings: IsolationSettings) -> tuple[str, str, str]`; prints only bounded PASS phases and exact source identity.

- [x] Create two distinct AgentInstances from the trusted opt-in team registry and provision one exact private Space resource in a disposable database.
- [x] Start both real Computers and require two distinct running containers on the internal-only network.
- [x] Create separate owner-confirmed parent and child activations, Campaign budget and one-hop delegation.
- [x] Submit, claim and run real `files_read` and `files_list` Tasks; assert exact fixture content and listing.
- [x] Refuse parent approval substitution and new submission after delegation revocation.
- [x] Stop services, clean exact owned resources, and verify no run resources remain.

### Task 3: Review and exact-head host receipt — completed through PR #320

**Files:** Update `docs/v0.15-release-closeout.md` and issue #166 only with actual results.

- [x] Run independent source review on the candidate and fix actionable findings.
- [x] Run Ruff and diff check; open a draft PR and require exact-head CI.
- [x] On an isolated Zelo host checkout at the reviewed head, run the native qualification and record exact SHA, host backend identity, two UUIDs, result, failure phases, log digest and cleanup state. The earlier failed attempt remains part of the receipt.
- [x] Merge after the narrow proof gates and verify separate main CI. PR #320 merged as `a97338fb3ec78f9a0c051edd2d4633331857d7c0`; see [the merged receipt](https://github.com/kANoZY341/zelo-agent/issues/166#issuecomment-6618098605).

This plan's bounded R1 proof is complete. It does not close broad Computer/Space authority in [#131](https://github.com/kANoZY341/zelo-agent/issues/131). The smaller [v0.15.0 Core release](https://github.com/kANoZY341/zelo-agent/releases/tag/v0.15.0) was later published with optional Computer/Space authority disabled.
