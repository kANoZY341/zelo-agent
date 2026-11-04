# v0.15.0 Core Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish an auditable v0.15.0 Core release under the Managing Director's 4 November smaller-scope decision.

**Architecture:** Keep the existing default-deny configuration and Core release interlock. Record the supported profile, advance the application version, then qualify one exact merged `main` candidate before any tag or release.

**Tech Stack:** Python 3.12, FastAPI, SQLite/Alembic, pytest, GitHub Actions.

**Spec:** `docs/v0.15-core-release-scope.md`

## Global Constraints

- Media remains deferred and unavailable; no additional Reader or source attempt.
- No unqualified Skill, Computer, Browser, Shell, OCR or REA authority is activated.
- Keep the parked UI checkout and PR #61 untouched.
- A tag requires final independent audit, exact-SHA operator decision and the separate Release Task approval.

## Review Focus

- A stale deployment flag must not be described as part of the supported release profile.
- A default Agent must not gain a Space or Computer through the version change.
- Backup restoration must preserve the old Task and audit before reupgrade.
- An audit finding or changed main SHA must invalidate the final release candidate.
- A GitHub tag must bind to the audited exact main SHA and be annotated.

---

### Task 1: Scope and version candidate

**Files:** `docs/v0.15-core-release-scope.md`, `docs/v0.15-release-closeout.md`, `docs/v0.15-release-tag-interlock.md`, `docs/zelo-project-status.md`, `docs/zelo-canonical-architecture-roadmap.md`, `README.md`, `app/__init__.py`, `tests/test_api.py`.

- [ ] Record the exact owner scope decision and current disabled lanes.
- [ ] Set application version and health expectation to `0.15.0`.
- [ ] Run default-authority, release-interlock and health tests on the isolated host.
- [ ] Review the bounded diff, run exact-head CI, merge, and verify separate main CI.

### Task 2: Final exact-main qualification

**Files:** release evidence in issue #166 and the existing closeout ledger; no runtime permission change.

- [ ] Verify the exact merged SHA and default inventory, then inspect effective
      release-host `Settings` (environment and `.env`) and conditional
      registrations. Fail closed on any excluded lane's active opt-in; publish
      only a redacted inventory. Run a local Core smoke on that host.
- [ ] Prove disposable v0.14 upgrade, backup restore and reupgrade with preserved Task/audit.
- [ ] Obtain independent full-repository audit on that exact candidate and resolve release-blocking findings.
- [ ] Present the exact SHA and evidence for the final operator release decision.
- [ ] After approval, use the release interlock and separate Release Task; verify annotated tag, published release and post-release Core health.
