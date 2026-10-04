# PR86 actual-evidence acceptance — 4 October 2026

Verdict: ACCEPT the one fixed historical archive-byte acquisition at source
023eccba160952f0623f2c1b2d132aa598384ca3, tree
53f8fcd719b51058c99081557dc0bb37453c80e7. No blocking actual-evidence findings.
Normal exact-head/tree merge is cleared once GitHub is rechecked unchanged.
No second acquisition attempt, member parsing or runtime authority is cleared.

## Review provenance

This acceptance review was performed directly in the continuing ChatGPT session,
not by a new Codex execution. The existing Astra Medium frozen-source clearance
in runtime/media-package-acquisition-r2-source-review.md remains the source gate.
The later Codex local-identity design review hit its usage limit and produced no
completed verdict; it is not counted as a passing review. No new model jobs were
launched. The source-cleared acquisition implementation and launcher were read together
with operator receipt/failure tests. No tracked source was changed before or
after the actual run and audit.

## Preconditions and matching verification

- Exact source/head/tree and clean tracked worktree were checked before execution.
- GitHub CI run 37178199742 completed successfully on exactly 023eccb: 3,737 passed,
  two skipped, 11 warnings. Existing four standalone passes cover the omitted paths.
- Retained R2 supplements bind four standalone passes and seven intended causal
  failures to this exact head. Existing source review also read those records.
- A fresh direct run of the three acquisition test files passed 97 tests.
- Private admission marker and lock were empty before execution. The acquisition
  proof scope was absent. Exactly one source-cleared --run was issued, no retries.

## Actual evidence and separate retained-byte audit

The operator saved exit status 0, an empty stderr file and a success receipt.
The audit imports no project code, opens no network connection and never parses
archives. It checks held-FD identity before/after bounded raw hashing, exact
inventory/modes/ownership, all declared sizes/hashes and JSON bindings.

- Eight archives total 8,745,084 bytes, all matching the exact pinned lock.
- Exactly 11 retained regular files total 8,753,701 bytes, below 16 MiB.
- Only one acquisition attempt directory exists; the four-attempt ceiling is unchanged.
- Proof: runtime/media-package-acquisition/acquisition-proof-lobuoic0/cases.json.
- Proof SHA256: c7af88e91dc08c92b83f218c5553574f6a4df6ba89b42ce70010990e22771f51.
- Manifest SHA256: 6970e601ea43120d86f5f5505c32ddea781a9b0b8c709175dd85f44f766e9ba1.
- Policy digest: 54f329d0001bf80b4e113345c6a38d9549c0a216cd08260d93ac8029214738fe.
- Lock SHA256: 0c7b800977288ea8beb88f765718d8df42e0a40729ef5bbc6c3d41c2f4f298d6.
- The retained upstream index proof still hashes to
  199b3b16ee247497b782d78356bb88f3f67ed717401d5cb39ec7a8fc2c0b210d.

The pending on-disk proof correctly retains support_release_confirmed=false.
The separate success receipt binds its SHA and confirms support release=true,
matching the reviewed single-release design; there was no post-release proof
rewrite. Admission files are empty after execution. A marker alone would not
prove cleanup, but the source-reviewed receipt path and fresh failure-path tests
provide the required binding. No universal hidden-library/finalizer guarantee or
hard wall-clock deadline is inferred.

Remote transport caveat: Desktop Commander's completion summary reported exit 1
although the shell reported operator exit 0. The separately retained operator
exit file, empty stderr, proof-bound receipt and successful byte audit establish
the operator result. This review does not claim a clean remote transport exit or
rerun the acquisition to resolve a transport display inconsistency.

## Preserved boundaries

Acceptance is only archive_hash_verified against the accepted historical signed
index chain. Member origin, platform completeness, ABI qualification, freshness,
current signing-key validity, aggregate cgroup qualification and runtime authority
remain false. TLS/CA and installed baseline provenance remain conditional.
No archive extraction, package installation, dpkg execution, image decoding,
Core/route binding, UI changes, enabled flags or system changes were performed.
No old proof scope was reset. The next local identity proposal is a separate
research gate, not authority from this acquisition acceptance.

Retained audit: runtime/media-package-acquisition-actual-audit-20261004.json.
Auditor source: runtime/audit-media-acquisition-20261004.py.
