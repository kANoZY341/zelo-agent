# WhatsApp One-Use Send-Attempt Claim Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Atomically burn one approved WhatsApp reply send attempt before any future external request.

**Architecture:** Factor the existing outbound projection checks so they can run inside the SQLite writer transaction. Add one append-only attempt table keyed by reply intent and a private claim service that returns the ephemeral projection only on first claim.

**Tech Stack:** Python 3.12, SQLAlchemy, Alembic, SQLite, pytest, Ruff.

**Spec:** `docs/superpowers/specs/2026-11-06-whatsapp-send-attempt-design.md`

## Global Constraints

- No Meta HTTP, token, webhook, API route, Worker or default authority.
- Preserve old rows; migration 0054 has no destructive downgrade.
- Raw provider IDs and reply text never enter the attempt table.
- A committed claim permanently refuses another claim, even after an uncertain outcome.

## Review Focus

- Concurrent same-intent claims: exactly one durable winner, second refused.
- Account revocation or edited Mind reply before claim: no attempt row.
- Wrong raw provider ID or secret: no attempt row.
- Direct SQL forged reject/replace/update/delete: database refuses.
- Upgrade with existing 0053 rows: old reply and decision remain, integrity checks pass.

---

### Task 1: Atomic one-use claim and schema

**Files:** Modify `app/whatsapp_reply_intents.py` and `app/models.py`; create `app/whatsapp_send_attempt.py`, `migrations/versions/0054_whatsapp_send_attempt.py`, `tests/test_whatsapp_send_attempt.py`; update schema-head expectations in affected tests.

**Interfaces:** `WhatsAppReplyIntentStore._project_outbound_in_session(session, intent_fingerprint, *, principal, app_secret, identity_secret, waba_id, phone_number_id, recipient_wa_id, inbound_message_id) -> WhatsAppOutboundProjection`; `WhatsAppSendAttemptStore.claim(intent_fingerprint, *, principal, app_secret, identity_secret, waba_id, phone_number_id, recipient_wa_id, inbound_message_id) -> ClaimedWhatsAppSendAttempt`.

- [ ] Write a real signed-ingress test: approve exact saved reply, claim once, assert projection, one unresolved append-only row, no raw IDs on disk; repeat claim must raise and row count stay one.
- [ ] Run that test and observe the missing service failure.
- [ ] Add migration, model, private in-transaction projection helper and minimal claim service; run the test green.
- [ ] Add negative and concurrent tests named in Review Focus, watch them fail for missing guards, then implement guards and rerun.
- [ ] Add populated 0053→0054 and SQL mutation/replace tests; verify downgrade refusal and integrity.
- [ ] Update fixed migration-head and trigger-inventory expectations; run affected suites, Ruff and diff checks.

### Task 2: Documentation and integration gate

**Files:** Create `docs/v0.16-whatsapp-send-attempt.md`; update `docs/zelo-canonical-architecture-roadmap.md`.

- [ ] State exact no-send authority and future transport/status requirements.
- [ ] Independently review the exact diff and resolve material findings.
- [ ] Commit, open PR, require exact-head CI, merge with expected SHA and require distinct post-merge main CI.
- [ ] Record the source-backed KNZ checkpoint and the next live qualification gate.
