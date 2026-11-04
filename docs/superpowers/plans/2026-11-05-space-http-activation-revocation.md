# Space HTTP Activation Revocation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a native two-Computer Core HTTP proof that a revoked child Space activation cannot admit a new Task while a distinct valid activation still works.

**Architecture:** Extend the existing disposable qualifier with one extra child intent and activation. Revoke only that activation over the normal loopback Core API, assert a specific 409 and unchanged Task count, then continue the existing successful read/list and delegation-revocation checks. No runtime permission or production service change.

**Tech Stack:** Python, FastAPI/httpx, pytest, existing Core Space and Computer services.

**Spec:** [issue #131](https://github.com/kANoZY341/zelo-agent/issues/131) and `scripts/qualify_two_computer_space.py`.

## Global Constraints

- Keep the qualifier disposable, loopback-only, and default-deny outside its private database and Docker namespace.
- Preserve distinct AgentInstance and activation identities; no Media or Skill authority.
- Run focused tests, native host proof, independent review, exact-head CI, and post-merge main CI before claiming integration.

## Review Focus

- A revocation must be for the child activation used in the rejected request, not the parent activation.
- The refusal must come from persisted Space admission and return `computer_space_activation_invalid`.
- The rejected request must not create a Task.
- A separate still-active child activation must complete both read and list after the refusal.
- Delegation revocation must still reject a later Task for its own reason.

---

### Task 1: Prove HTTP activation revocation in the existing qualifier

**Files:**
- Modify: `scripts/qualify_two_computer_space.py`
- Test: `tests/test_two_computer_http_qualifier.py`

**Interfaces:**
- Extend `qualify_http(...)` with a `revocable_body: dict[str, object]` and `revocable_activation_id: str` from a distinct child activation.
- Keep its return type and all existing arguments unchanged.

- [ ] Write the HTTP helper test with a revocable child activation and assertions for 409, exact refusal, unchanged Task count, and two subsequent successful Tasks.
- [ ] Run `python -m pytest -q tests/test_two_computer_http_qualifier.py -k qualification` and observe the missing-argument/behavior failure.
- [ ] Update `qualify_http` to revoke the supplied activation over Core HTTP and make the negative Task request before normal read/list.
- [ ] Update the real Core TCP test and native qualifier setup to create a separate child intent/activation and pass its request body.
- [ ] Run the focused test file, Ruff check/format, and `git diff --check`; inspect the diff for any authority changes.
- [ ] Commit the bounded qualifier and test change; run the native proof on the authorized host and record hashes and cleanup.
