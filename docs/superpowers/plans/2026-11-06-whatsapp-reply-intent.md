# WhatsApp Reply Intent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist an operator-reviewed reply intent for a verified WhatsApp Mind turn without sending it.

**Architecture:** Private Core service rechecks verified ingress, selected account, binding and saved reply in serialized SQLite transactions. Two append-only tables record exact prepared intent and one operator decision. No route or transport uses them.

**Tech Stack:** Python, SQLAlchemy, Alembic, SQLite, pytest.

**Spec:** `docs/superpowers/specs/2026-11-06-whatsapp-reply-intent-design.md`

## Global Constraints

- No webhook, Meta credential, outbound call, Core Task or Worker authority.
- Persist only opaque keys, turn ID, reply SHA-256, operator ID and timestamps.
- One prepared intent per verified turn; one immutable decision per intent.
- Revocation is checked in the same transaction as prepare and decision.

## Review Focus

- Duplicate prepare with changed saved text refuses rather than silently reusing an intent.
- An unverified or task-bearing turn cannot be prepared.
- Approval after account revocation refuses; old decisions remain historical.
- `INSERT OR REPLACE` cannot overwrite intent or decision.
- A prepared or approved row cannot be interpreted as delivery authority.

---

### Task 1: Durable schema and migration

**Files:** `app/models.py`, `migrations/versions/0053_whatsapp_reply_intents.py`, `tests/test_whatsapp_reply_intents.py`.

**Interfaces:** `WhatsAppReplyIntentRecord` keyed by delivery fingerprint and `WhatsAppReplyDecisionRecord` keyed by intent fingerprint.

- [ ] Write tests for append-only guards, duplicate insert and populated 0052 upgrade.
- [ ] Run those tests red.
- [ ] Add models and migration with foreign keys, constraints, WITHOUT ROWID and insert/update/delete guards.
- [ ] Run tests green and commit.

### Task 2: Operator-only prepare and decision

**Files:** `app/whatsapp_reply_intents.py`, `tests/test_whatsapp_reply_intents.py`.

**Interfaces:** `WhatsAppReplyIntentStore.prepare(turn_id, principal)` and `.decide(intent_fingerprint, decision, principal)` return immutable snapshots and a created flag.

- [ ] Write tests for verified success, exact retries, conflicting decision, revocation and invalid turn/binding/text.
- [ ] Run them red.
- [ ] Implement the private service with transaction-time rechecks and no transport calls.
- [ ] Run tests green and commit.

### Task 3: Integration gate

**Files:** current-schema migration inventory tests, `docs/v0.16-whatsapp-reply-intent.md`.

- [ ] Update only current-head/schema/trigger inventories that 0053 changes.
- [ ] Run affected channel/Mind/migration tests, Ruff, format and diff checks.
- [ ] Request independent review; fix findings with regressions.
- [ ] Open PR, require exact-head full CI, merge, then require separate `main` CI.
