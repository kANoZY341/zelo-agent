# WhatsApp outbound identity projection design

## Intent
Prepare one exact approved reply for a later qualified sender without persisting raw provider IDs or granting delivery authority. The previous PR #347 stores only opaque account, recipient, thread and message keys. A future sender needs a safe way to compare trusted raw IDs with those keys.

## Boundary
A private Core method accepts an Ahmad OperatorPrincipal, one approved reply intent fingerprint, trusted current App Secret and independent Core identity secret, and the raw WABA, receiving phone, recipient and inbound message IDs supplied by trusted operator configuration. It rechecks the verified ingress, bound conversation, current account selection and revocation, strict saved Mind reply, immutable intent and approve decision. It derives all four opaque keys from the supplied raw IDs and checks the current App Secret identity. It returns only an ephemeral frozen projection of the exact saved text and provider IDs, marked transport_authority false. It performs no HTTP request, queues no work, stores no raw IDs or tokens, and never treats an approval row as a send permit.

Malformed IDs, wrong ID combinations, wrong secrets, rejection or missing decision, revocation, and edited saved replies fail closed. The later sender must repeat these checks at dispatch time and add its own one-use claim, provider response/status ledger, retry policy, credential protection and live qualification. A projection may become stale immediately after return.

## Approaches
- **Chosen:** private Core projection using the existing verified ingress and reply-intent checks. Small and reversible; it closes the opaque-to-raw identity gap before adding side effects.
- Direct Meta POST from the approval method: rejected because an external side effect cannot be atomic with the existing decision row and would make uncertain retries unsafe.
- Persist raw provider IDs with the intent: rejected because the current privacy boundary deliberately stores only keyed pseudonyms and the raw data is unnecessary for this gate.

## Compatibility and evidence
No migration, dependency, API route or default setting changes. Revert the commit to remove the projection. Tests use signed ingress, real Mind persistence and isolated databases. They cover correct identity, Unicode text, each mismatched raw dimension, malformed IDs, wrong secrets, missing/rejected approval, changed text, revoked account, non-operator use, exact retry, and absence of transport or raw provider IDs in the isolated SQLite database and WAL files. Ruff, independent review, exact-head CI and separate post-merge main CI are required. Live Meta account ownership and delivery remain NOT RUN.
