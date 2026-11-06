# WhatsApp one-use send-attempt claim design

## Intent and boundary

An approved offline reply needs a durable pre-network fence. A private Ahmad-only Core method claims at most one possible send for one reply intent. The method accepts trusted current App Secret and independent Core identity secret, plus raw WABA, receiving phone, recipient and inbound message IDs supplied by trusted operator configuration. It rechecks the saved verified ingress, selected and unrevoked account, bound conversation, exact reply-only Mind action, immutable intent, approve decision and all derived opaque identities inside the same SQLite writer transaction that inserts the claim.

The committed append-only row uses the intent fingerprint as its primary key and records only Ahmad, claim UTC time, `send_outcome_unresolved`, `send_may_have_occurred=true`, `automatic_retry_allowed=false` and `transport_authority=false`. It stores no raw provider IDs, reply text, token, secret or HTTP response. The method returns the existing ephemeral outbound projection only for the first claim. Any second claim, including an exact retry after a crash, refuses. Revocation, changed text, wrong secrets or IDs, missing/rejected approval and nonoperator callers refuse before insertion. SQLite foreign keys, constraints and triggers reject a forged unapproved or mutating row, including `INSERT OR REPLACE`.

The claim conservatively burns the one attempt before any possible later network call. This slice does **not** call Meta, provide a sender, enable an API route, store a credential, parse a status webhook or prove delivery. Future transport must call this atomic claim directly before its one POST, preserve ambiguous results, and append independently qualified response/status evidence. It must not retry a claimed intent automatically. A claim may remain unresolved even when no network call happened; that is an intentional at-most-once tradeoff.

## Approaches considered

- **Chosen:** one atomic durable attempt claim plus ephemeral projection. It closes the crash/retry gap without creating a second reservation table.
- Separate no-send reservation followed by an attempt: safe but adds a migration and an extra state with no independent requirement now.
- Send directly from the approve decision: unsafe because database approval and external HTTP cannot commit atomically.

## Evidence and compatibility

No default setting, dependency, webhook, Worker, API route or transport changes. Migration 0054 adds one table without rewriting old rows. Tests use real signed ingress, Mind persistence and disposable SQLite, then exercise first claim, exact retry refusal, concurrent claims, revoked/edited/wrong-identity refusals, direct-SQL guards, populated upgrade and database integrity. The test also scans its isolated SQLite and WAL bytes for raw provider IDs. Ruff, independent review, exact-head CI and separate main CI are required. Live Meta ownership, provider policy, actual sending and delivery are NOT RUN. Published release remains v0.15.0.
