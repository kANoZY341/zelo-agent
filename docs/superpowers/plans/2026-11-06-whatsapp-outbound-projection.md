# WhatsApp Outbound Identity Projection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Produce an ephemeral, operator-only projection of one approved WhatsApp reply after rechecking trusted provider IDs and current Core authority.

**Architecture:** Extend the private reply intent store with a projection method. Reuse its verified-ingress and saved-reply checks; derive account, sender, thread and message keys from trusted raw IDs. Return a frozen, non-authoritative record; add no network call or database row.

**Tech Stack:** Python, SQLAlchemy, Pydantic, SQLite, pytest.

**Spec:** docs/superpowers/specs/2026-11-06-whatsapp-outbound-projection-design.md

## Global Constraints
- Core remains authority; approval does not grant transport authority.
- Raw IDs and secrets remain in memory only and are excluded from repr and durable rows.
- No ASGI route, provider POST, token, Worker task or migration.
- Existing v0.15.0 release and parked UI #61 remain unchanged.

## Review Focus
- Same recipient on a different WABA/receiving phone must fail.
- A correct account with a different recipient or inbound message ID must fail.
- A selected account whose secret identity differs from the current trusted secret must fail.
- Revocation or saved reply changes after approval must fail.
- A duplicate projection must not create durable state or send twice.

---

### Task 1: Private outbound identity projection

**Files:**
- Modify: app/whatsapp_reply_intents.py
- Modify: tests/test_whatsapp_reply_intents.py
- Create: docs/v0.16-whatsapp-outbound-projection.md

**Interfaces:**
- Consumes: WhatsAppReplyIntentStore._current, _prepared, _decision; verified ingress, selection and binding records; derive_opaque_channel_key; whatsapp_app_secret_identity.
- Produces: WhatsAppReplyIntentStore.project_outbound(intent_fingerprint, *, principal, app_secret, identity_secret, waba_id, phone_number_id, recipient_wa_id, inbound_message_id) -> WhatsAppOutboundProjection.

- [ ] Write a signed-ingress, approved-reply positive test asserting exact ephemeral fields and transport_authority false; run it red on the missing method.
- [ ] Add parameterized negative tests for raw ID/secret mismatches, malformed input, missing/rejected approval, revocation, edited reply, non-operator and no database mutation.
- [ ] Implement strict raw ID validation, current secret check, opaque identity rederivation and frozen projection inside one read transaction.
- [ ] Run the focused suite and affected channel/migration suites; run Ruff, format and diff checks.
- [ ] Commit and request independent exact-head review; correct findings with red-to-green tests.
- [ ] Push, open PR, pass exact-head CI; merge only after review and head/base verification; require separate post-merge main CI.
