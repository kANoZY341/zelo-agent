# WhatsApp send-response observation design

## Intent and boundary

The one-use send claim needs a durable way to correlate a later Meta response without recording raw provider IDs or promoting an untrusted response into delivery authority. A private Core method records at most one bounded HTTP response observation for one claimed reply intent. This remains an offline shape check: there is no HTTP client, token, webhook, route, Worker, live Meta account proof or provider authentication.

The caller supplies Ahmad's principal, the Core identity secret, and a trusted raw recipient WhatsApp ID. Core derives its opaque recipient key and compares it to the saved reply intent inside the serialized SQLite writer. The caller may report no response, a non-200 final HTTP status, or a 200 response body. For 200, the bounded strict JSON parser recognizes exactly one WhatsApp contact with the expected wa_id and one syntactically valid wamid. It rejects duplicate keys, malformed UTF-8/JSON, oversized bodies, missing/ambiguous lists, mismatched recipient, and contradictory error fields. Unknown extra response fields are ignored, never treated as authority.

The append-only row is keyed by the attempt's intent fingerprint. It stores only HTTP status, one of `success_shape`, `invalid_success_shape`, `http_non_success`, or `no_response`, an optional keyed opaque outgoing message ID, observation time, and fixed false delivery/transport flags. `success_shape` means only that supplied bytes match the recognized shape. A non-200 response or missing response does not prove no send; the original attempt remains permanently spent with no automatic retry. A later independently authenticated status path must link the opaque outgoing ID and prove delivery.

## Approaches considered

- **Chosen:** one first-observation row with conservative categories and keyed message ID. It preserves uncertainty and gives a future status path a private correlation key.
- Store the raw Meta JSON: easier debugging, but leaks recipient and provider message IDs and may contain arbitrary data.
- Treat 200 as delivered and retry non-200 automatically: violates the existing at-most-once claim and provider status boundary.

## Evidence and compatibility

Migration 0055 adds one `WITHOUT ROWID` table, constraints, a unique outgoing message key, and update/delete/replace guards; it does not rewrite old rows and refuses destructive downgrade. Tests use signed ingress through Mind approval and the real 0054 attempt claim, synthetic response bytes, isolated SQLite and WAL inspection, concurrent/duplicate observation, recipient mismatch, malformed response, revocation-after-claim, direct SQL guards, and a populated 0054→0055 upgrade. Ruff, affected tests, independent review, exact-head CI and distinct post-merge CI remain required. Live Meta transport, source provenance and delivery are NOT RUN.
