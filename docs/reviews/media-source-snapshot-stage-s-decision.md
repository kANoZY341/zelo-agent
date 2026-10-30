# Stage S snapshot input decision candidate

Status: **inactive design**, prepared after the offline selector merged in
PR #287 and the candidate date policy in PR #288. This document makes no
request and grants no runtime or release authority. The execution record
must pin the then-current main SHA and tree,
reviewed operator code, host baseline and one-use decision before a request.

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
and match the supplied SHA-256. It names the current clean tracked Git HEAD
and tree, the policy digest, the fixed host root, both exact URLs and body
caps, one additional attempt, no runtime authority, and canonical issued and
expiry times no more than ten minutes apart. The operator retains those exact
bytes in `decision.json` before its first request and binds their digest into
the source manifest. The fixed host proof root then consumes its sole slot.

An operator must independently confirm that the named HEAD is the reviewed
then-current public main commit and that the decision file was created under
the approved one-use scope. A local decision file by itself does not
authenticate a Managing Director identity or grant Media runtime authority.
