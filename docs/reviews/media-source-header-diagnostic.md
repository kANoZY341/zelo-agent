# Stage H — first-response header diagnostic candidate

Status: **inactive**. This candidate grants no request, source provenance,
Media runtime authority, or v0.15 release. The Stage S input authorized on
31 October 2026 (Dubai time) remains terminally refused. Its sole proof is
`/home/zelo/zelo/media-source-snapshot-stage-s-proof/snapshot-proof-10cwy2xm`.
It recorded one `InRelease` request started, zero retained body bytes and no
`Sources.xz` request. The exact response condition was not retained.

## Fixed question and scope

This diagnostic asks only which bounded first-response condition the fixed
snapshot endpoint presents to the reviewed Zelo transport. It may issue **one
GET** to
`https://snapshot.ubuntu.com/ubuntu/20261030T000000Z/dists/noble-updates/InRelease`.
It follows no redirect, uses no proxy, credentials, alternate host, mirror,
fallback, retry, resume or second request. It does not read or retain a
response body. The HTTP library may buffer bytes while parsing headers; zero
application body reads is the bounded claim, not a claim about bytes on the
wire. A failed or interrupted request consumes the allowance.

Use the existing pinned Python transport files, CA bundle, verified TLS
hostname, IPv4 DNS records, HTTP/1.1 request, 10-second socket timeout,
65-line and 65,536-byte aggregate header limits, and a cooperative 120-second
whole-attempt deadline. The same limitations for DNS, TLS, buffered I/O and
filesystem hard deadlines apply. The diagnostic response parser accepts a
non-200 status for *observation* and never promotes it to source evidence.

Retain only numeric and boolean response facts: status, HTTP version, header
bytes/lines/count and defect count; Content-Length count and a bounded
decimal value if unambiguous; transfer/content encoding counts, chunked and
identity flags, and Location count. No header name/value, Location URL,
cookie, body or peer diagnostic string is retained. If parsing fails before
those facts are available, retain only a fixed bounded refusal code or an
unclassified failure. No observation authorizes an altered acquisition
policy; a separate review must decide any transport change.

## One-use evidence and decision

Use the fixed, separate host directories
`/home/zelo/zelo/media-source-snapshot-stage-h-proof` and
`/home/zelo/zelo/media-source-snapshot-stage-h-admission`. An occupied proof
root or uncertain lease refuses, without clearing or reusing the Stage S
record. Before a request, verify the exact earlier Stage S proof files and
their pinned SHA-256 hashes, its zero-byte `InRelease`, sole proof directory,
and empty admission marker. The parent, proof, files and marker retain their
private owner/mode checks. No previous proof is edited or moved.

The operator is inert without an isolated `python -I` entrypoint,
`--run --headers-diagnostic --decision-sha256` and a private mode-0600
`/home/zelo/zelo/media-source-snapshot-stage-h-decision.json`. The file must
match the supplied SHA-256 and bind the then-current clean public main SHA and
tree, exact policy digest and URL, one additional attempt, zero body bytes,
no runtime authority and issue/expiry times at most ten minutes apart. At
validation, at least 120 seconds must remain, covering the cooperative
whole-attempt deadline from its earlier start. Network and filesystem calls
are not hard bounded, so this does not guarantee settlement by expiry. The
checked-out bytes are hashed against the Git tree, including index-hidden
files, before admission. A local file alone does not authenticate the
Managing Director or establish the public-main identity; both require an
independent operator check and a **new explicit one-use decision**.

Retain policy, exact decision, a pre-request start journal, a bounded
observation or failure, and a cases receipt in private exclusive files under
the existing 1,048,576-byte journal cap. No body file is allocated. Hold the
admission lease through request, header observation, cleanup and durable
receipt. An uncertain socket or release cannot produce an accepted
observation. Even an accepted observation has
`source_provenance_verified=false` and `runtime_authority=false`.

## Before any live invocation

Independently review the code and synthetic adversarial cases at an exact
tree, pass exact-head and distinct-main CI, verify the public main SHA/tree,
host pins, empty Stage H namespace and prior Stage S record, then obtain the
separate one-use decision. Do not run a diagnostic under the spent Stage S
authorization. Its result can inform a later separately reviewed source
acquisition scope; it cannot be retroactively treated as one.
