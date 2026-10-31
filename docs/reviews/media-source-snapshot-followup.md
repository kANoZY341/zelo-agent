# Stage S2 source snapshot input candidate

Status: **inactive**. This code creates no input authorization, source
provenance, decoder qualification or Media runtime authority. The earlier
Stage S refusal and Stage H observation remain immutable and spent. The Stage
H proof is described in `media-source-header-diagnostic.md`.

## Reason for a separate input

Stage H observed a 200 response with one valid 126,125-byte Content-Length,
no transfer or content encoding and one duplicate header name. It did not
retain the name. Stage S rejected any duplicate before reading a body byte;
this could explain the earlier refusal, but that cause was not captured.

This follow-up uses a new private proof root
`/home/zelo/zelo/media-source-snapshot-stage-s2-proof`, admission root
`/home/zelo/zelo/media-source-snapshot-stage-s2-admission` and mode-0600
decision path `/home/zelo/zelo/media-source-snapshot-stage-s2-decision.json`.
It verifies the exact consumed Stage S and Stage H proofs and empty markers
before admission. It cannot reuse either earlier proof namespace.

## Bounded change

Only the Stage S2 snapshot path accepts repeated unused response metadata
names, after validating **every** field's name and value against the existing
ASCII, token and length bounds. The normal archive acquisition and original
Stage S path stay strict. Content-*, Transfer-*, Proxy-*, Connection, Host,
Keep-Alive, Location, TE, Trailer and Upgrade headers remain singular. The
existing HTTP status, version, non-chunked framing, one canonical
Content-Length, declared length, identity content encoding, body caps, signed
InRelease verification, pinned source index identity and cleanup checks still
apply. If the observed duplicate was a singular field, this policy will
refuse again. It does not infer or retain the duplicate's name.

The fixed URLs are the `20261030T000000Z` noble-updates `InRelease` and its
declared `main/source/Sources.xz` on `snapshot.ubuntu.com`. The operator makes
at most one request per URL, in order, with no redirect, proxy, fallback,
retry or resume. It retains bounded bytes only after framing validation.
The CA, DNS, TLS, socket and cooperative whole-attempt limits remain as in
Stage S. A failed first request consumes the one-use allowance. Accepted
source evidence is conditional and grants no runtime authority.

## Decision and release gate

The isolated operator needs `--run --followup-source --decision-sha256` and
a private decision binding the clean public main SHA/tree, exact policy
digest, two URLs, body caps, one additional attempt, no runtime authority and
issue/expiry timestamps within ten minutes. At validation, at least 120
seconds must remain. A file alone does not authenticate the Managing Director
or establish the public-main identity. Independent code review, synthetic
adversarial cases, exact-head CI, distinct-main CI and a fresh explicit
one-use authorization are required before any live Stage S2 input. Its
outcome cannot be assumed from the Stage H header observation.
