# WhatsApp reply intent and operator decision design

## Intent

Give Ahmad a durable, reviewable decision about one reply generated from a verified inbound WhatsApp text turn. This is the next offline Core step after PR #346. It does not send a message or grant transport authority.

## Boundary

A trusted Core service accepts only `OperatorPrincipal` for Ahmad. It reads the verified ingress record, immutable thread binding, current account selection, and saved Mind reply inside one serialized SQLite transaction. It prepares the existing `ChannelDeliveryIntent` from the exact assistant text and opaque account/sender/thread keys. Empty or task-bearing turns, unverified turns, missing bindings, revoked accounts, and identity conflicts fail closed. No raw provider IDs or credentials are persisted.

Preparation inserts one append-only intent row per verified turn. Exact retries return the same row; a changed saved reply or binding conflict cannot replace it. Ahmad may append one immutable `approve` or `reject` decision bound to that intent and its text digest. A decision is evidence for a later separately qualified sender; it does not call Meta, produce a dispatch token, or claim delivery. A later sender must recheck account revocation, exact text and approval at send time. Account revocation after a decision leaves the historical record but blocks any future send.

## Approaches

- **Chosen:** separate immutable Core intent and decision rows. This fits the existing append-only ledger and keeps Mind persistence free of outbound side effects. It adds one migration and a private service.
- **Alternative:** send directly after Mind returns a reply. This has no atomic boundary with the turn or provider call and would make retries and revocation harder to reason about.

## Compatibility and recovery

Migration `0053` adds only tables and guards; it does not alter existing turns or verified ingress. Downgrade is disabled; restore a reviewed backup for recovery. The service is not wired to ASGI or Worker. Existing v0.15 behavior and parked UI remain unchanged.

## Acceptance

Prove exact prepare/decision retries, one record per verified turn, approval and rejection conflicts, revoked-account refusals, unverified/foreign/task/empty turn refusals, database append-only and `INSERT OR REPLACE` refusal, populated `0052` to `0053` upgrade, Ruff, independent review, exact-head CI, and separate post-merge `main` CI. Report this as offline evidence only.
