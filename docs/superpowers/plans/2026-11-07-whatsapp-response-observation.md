# WhatsApp Send-Response Observation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist one privacy preserving response-shape observation for one already claimed WhatsApp reply.

**Architecture:** A private Core service checks the saved attempt and opaque recipient identity in the SQLite writer, classifies one bounded supplied HTTP result, and inserts one append-only row. A pure parser recognizes the minimum successful Meta response shape; no transport is connected.

**Tech Stack:** Python 3.12, SQLAlchemy, Alembic, SQLite, pytest, Ruff.

**Spec:** `docs/superpowers/specs/2026-11-07-whatsapp-response-observation-design.md`

## Global Constraints

- No Meta HTTP, credential, webhook, API route, Worker or default authority.
- `success_shape` is not provider authentication or delivery.
- Keep raw response bytes, recipient ID and wamid out of SQLite/WAL and repr.
- Never permit automatic retry of the spent attempt.
- Migration 0055 preserves existing data and refuses destructive downgrade.

## Review Focus

- Forged or mismatched recipient: no observation row.
- Malformed, duplicated, oversized or contradictory 200 response: durable invalid shape, no outgoing key.
- Concurrent/duplicate observations: one immutable winner.
- Direct SQL replace/update/delete and duplicate outgoing key: refused.
- Populated 0054→0055 upgrade: old attempt and approval remain intact.

---

### Task 1: Response classifier and receipt ledger

**Files:** Create `app/whatsapp_send_response.py`, `migrations/versions/0055_whatsapp_send_response.py`, `tests/test_whatsapp_send_response.py`; modify `app/models.py` and schema-head expectations in affected tests.

**Interfaces:** `WhatsAppSendResponseStore.observe(intent_fingerprint, *, principal, identity_secret, recipient_wa_id, http_status, response_body) -> WhatsAppSendResponseObservation`; a private classifier returns outcome and optional raw wamid, which is immediately keyed and discarded.

- [ ] Write the signed-ingress→Mind→approval→claim→synthetic 200 response test; run it red for the missing service.
- [ ] Implement the minimum parser, model, migration and store; run the test green.
- [ ] Add and verify negative-path and concurrent tests named in Review Focus.
- [ ] Add populated migration and integrity/downgrade tests; update exact schema expectations.
- [ ] Run affected tests, Ruff, format and diff checks.

### Task 2: Documentation and integration

**Files:** Create `docs/v0.16-whatsapp-response-observation.md`; update `docs/zelo-canonical-architecture-roadmap.md`.

- [ ] Document the response-shape boundary and unresolved live gates.
- [ ] Obtain independent review and address material findings.
- [ ] Commit and open PR; require exact-head CI, merge to expected main, then distinct post-merge CI.
- [ ] Record source-backed KNZ checkpoint.
