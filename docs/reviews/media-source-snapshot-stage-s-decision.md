# Stage S snapshot input decision candidate

Status: **one Stage S attempt consumed; refused, release HOLD**. This design
was prepared after PRs #287 and #288. PR #290 merged its one-use decision
gate. The execution record below grants no runtime or release authority.

## 30 October UTC execution record

The Managing Director authorized exactly one Stage S source snapshot input at
main `ee6d2120f15448ee493675ec71621447f3d5710b`, tree
`6c84e4cf486e2a62a433833eb780d1a1a61c9dce`. Exact-head PR CI
`41133146986` and distinct main CI `41135282471` passed 6,957 parallel tests,
21 serial tests, Ruff and diff checks. Public main and the clean Zelo host
checkout matched. The keyring, `gpgv`, Python transport files, storage and
unoccupied fixed proof/admission paths passed read-only preflight.

The private decision was issued `2026-10-30T22:38:26Z`, expiring ten minutes
later. Its SHA-256 is
`513454c0026f5aad71301f875de31813f30dd0b67ff00f5bbd65946be1f0d777`;
policy digest is
`cc8368edc7ee7f81b390205abed7ee925a41763376782bba4e2387ddef4c2017`.
The sole invocation allocated
`/home/zelo/zelo/media-source-snapshot-stage-s-proof/snapshot-proof-10cwy2xm`
and returned `media_snapshot_stage_s_refused`. Its `request.json` (SHA-256
`e63fafb182433f97caa901c7b35c94747f0ae76414381059fd87d2bb7afadbbd`)
records one `InRelease` request started and zero body bytes, with a partial
refusal. The zero-byte `InRelease` file and `failure.json` (SHA-256
`0df5f5cca68caad1ebdf2a78eea80aaa7b3df19d4ebe5045521fac1b0b07dcec`)
record `type=Refused`. No `Sources.xz`, manifest, accepted cases or source
stanza exists. The admission marker is empty and the checkout remains clean.

The existing journal records no specific transport refusal code or response
headers, so the precise first-response failure is **unknown**. The request
slot is consumed even if remote receipt cannot be established; do not retry
or infer source provenance from this record. Any new input needs a separate
reviewed scope and decision. A follow-up offline change retains a bounded
transport code in future refusal journals; it cannot repair this attempt.

## Fixed input

| Order | Exact HTTPS URL | Maximum body |
| --- | --- | ---: |
| 1 | `https://snapshot.ubuntu.com/ubuntu/20261030T000000Z/dists/noble-updates/InRelease` | 524,288 bytes |
| 2 | `https://snapshot.ubuntu.com/ubuntu/20261030T000000Z/dists/noble-updates/main/source/Sources.xz` | 4,194,304 bytes |

The second request is allowed only if the first response is a complete,
bounded regular private file, passes `origin.validate_envelope` framing,
and its **same settled** isolated `gpgv` invocation establishes the pinned
Ubuntu Archive signer, signed plaintext,
strict status and zero exit. The signed Release must pass
`candidate_snapshot_release`: Origin/Label Ubuntu, Suite noble-updates,
Codename noble, main component, unique SHA256 index declaration, canonical UTC
Date within one hour before the snapshot instant, and any Valid-Until covering
that instant. The second response length must equal the signed declaration,
which must be positive and at most the cap above. Its SHA256 must match that
declaration before XZ decompression or stanza selection.

The selected `dpkg 1.22.6ubuntu6.6` stanza must be unique and cross-check
`Files`, `Checksums-Sha1`, and `Checksums-Sha256`. It must retain the exact
`.dsc` and tarball filename, length, SHA256 and pool Directory. A mismatch
against the fixed expected 5,548,716-byte tarball SHA256
`7048ee31e30066ae123cdcb891cfa1ad55b2f42a4e04fca2c5db41607ae25196`
refuses. Neither descriptor nor tarball is requested at Stage S.

## Request and retention limits

- Exactly one attempt, at most one GET to each URL, in order. A failed,
  uncertain or interrupted request consumes that URL's allowance. No
  redirect, proxy, alternate host, mirror, retry, resume, range request,
  conditional request, credential, system APT mutation or fallback.
- Require HTTPS, hostname verification, a pinned host and literal path,
  identity content encoding, status 200, HTTP/1.1, no transfer encoding,
  one canonical Content-Length within the applicable cap and an exact-length
  body. Reject duplicate or malformed headers and all other responses.
- Use separate 10-second socket timeouts and a single 120-second monotonic
  whole-attempt deadline, including verification and cleanup. These are
  cooperative bounds; DNS, TLS, buffered I/O and filesystem calls are not
  claimed to have hard deadlines.
- Set a hard cap of 4,718,592 response bytes before the first request, plus
  at most 1,048,576 bytes across eight private journal/proof files. The
  complete retained-attempt budget is 5,767,168 bytes and ten files.
  Use the fixed `/home/zelo/zelo/media-source-snapshot-stage-s-proof` root
  across worktrees, with its own fixed admission directory alongside it;
  a new checkout must not create a new one-use slot.
  Each body is written to
  a new exclusive regular file with no-follow semantics, fsynced, read back
  and checked for size/hash and stable descriptor identity. Retain partial
  failure bytes and a refusal record. No automatic cleanup can erase the
  consumed-attempt evidence. Catchable interrupts make a bounded best-effort
  durable partial/refusal record while retaining uncertain resources; abrupt
  process death or host failure cannot guarantee a final record.
- Hold one admission lease across acquisition, isolated verification,
  parsing and durable receipt. Uncertain process, network, file or lease
  settlement refuses; no receipt may imply a successful release of resources
  when that release is unproven.

## Verifier and trust boundary

Use the previously reviewed Ubuntu Archive keyring
`/usr/share/keyrings/ubuntu-archive-keyring.gpg` (3,607 bytes, SHA256
`80a36b0a6de2f69f49d2df75ef473ccde121e9e190b9ea01d20a4f63778d5c31`)
and `/usr/bin/gpgv` (310,416 bytes, SHA256
`f14d026b9eae172c432e015bce227483293b4966f2f3fdcfa582f71d3dbb2ae8`).
The sole accepted primary and signing fingerprint is
`F6ECB3762474EDA9D21B7022871920D1991BC93C`. Pin root-owned file modes,
hashes and held descriptors. Invoke the fixed argv inside a no-network,
read-only, dropped-capability namespace with a private home/keyring and the
exact retained InRelease bytes mounted read-only. Reject unknown or
contradictory machine status, nonzero exit, output overflow, altered input,
wrong signer or cleanup uncertainty. Reuse the proven origin parser only for
the same tool version and status grammar; any change needs a new source review.

The snapshot ID is a service locator, not a signed publication time. The
independent date window is historical attribution, not current freshness.
The Ubuntu Archive signature proves archive membership only within the pinned
operator trust baseline. It does not verify uploader identity, current key
validity, package build correspondence, installed binary or ABI, decoder
behavior, Core Media authority, or v0.15 release readiness.

## Required before execution

Implement and independently review the bounded acquisition, isolated
verification, one-use ledger and adversarial offline tests at an exact tree.
Verify request URL and transport behavior, tool/keyring host pins, source
snapshot metadata, storage capacity and resource settlement before one
operator decision. Record the actual response and refusal receipts; never
retroactively treat the live 29 October cached Release as this snapshot.

## One-use operator decision gate

The operator remains inert without `--decision-sha256` and a private
`/home/zelo/zelo/media-source-snapshot-stage-s-decision.json`. The exact file
must be regular, owner/group matched, mode 0600, one link, at most 8,192 bytes,
and match the supplied SHA-256. It names the current clean Git HEAD and tree;
the gate hashes every tracked worktree blob against HEAD, including files
hidden by skip-worktree or assume-unchanged index flags. It also binds the
policy digest, the fixed host root, both exact URLs and body
caps, one additional attempt, no runtime authority, and canonical issued and
expiry times no more than ten minutes apart. The operator retains those exact
bytes in `decision.json` before its first request and binds their digest into
the source manifest. The fixed host proof root then consumes its sole slot.

Invoke the script with isolated Python (`-I`) in a fresh
checkout. Before importing repo modules it selects a fresh, nonexistent
bytecode-cache prefix and disables bytecode writes, so ignored cache files in
the checkout cannot replace verified source. Direct module calls cannot use a
decision unless the isolated CLI startup completed.

An operator must independently confirm that the named HEAD is the reviewed
then-current public main commit and that the decision file was created under
the approved one-use scope. A local decision file by itself does not
authenticate a Managing Director identity or grant Media runtime authority.
