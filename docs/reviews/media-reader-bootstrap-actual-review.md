# Reader-bootstrap ELF actual-evidence acceptance — 4 October 2026

Verdict: ACCEPT the single source-cleared 40-case data-inspection batch at
head d51074661f15071a7c93d0030a3c55f224678300, tree
4b39b2f253108e7bbf1461b4cccaffac321d270e. This is version-proof attempt 3/4.
No target executable, loader, compressor, package member or Pillow code ran.

## Matching source and CI

- PR #91 exact-head CI run 37204122789 succeeded: 4,347 passed, 2 skipped,
  11 warnings.
- Frozen source/tree remained exact and tracked worktree clean before the run.
- Exactly two prior version-proof directories existed and admission marker/lock
  were empty before the one-shot wrapper allowed execution.
- The run was issued once. There was no retry or proof-scope reset.

## Actual result and independent audit

- Operator exit: 0; stderr: empty.
- 40 records accepted: 32 fixed control/malformed cases plus 8 fixed inert
  reader-bootstrap inputs.
- Proof: runtime/media-elf-version-proofs/version-proof-vsvprzca/cases.json.
- Proof SHA256: cc5c463a62ccd73534d88c37b2dbc9ad4b0343161066998343877e469a6601e5.
- Receipt SHA256: d500babd8c820c5d5559546616015fc8997993cf0282dd69e2b64b8b5574bd0b.
- Policy digest: 00188020e9559f3dd95b27bf69c4a2288f0044d8336629779fefd706c92e54ae.
- Retained attempt: 78 top-level entries, 2 FIFOs, 76 regular files including
  the nested fixed bad-tool anchor; 439,696 regular bytes.
- Every record reports passed=true and cleanup_confirmed=true.
- Support release is false in the immutable pending proof and true only in the
  separately bound post-cleanup receipt.

The independent auditor used trusted stdlib only: no project imports, network,
installed-input reads, native execution or proof mutation. Its first invocation
found an auditor-only path comparison error because the receipt stores an absolute
proof path and the command supplied a relative proof directory. Only the ignored
auditor was corrected to compare resolved paths. The qualification was not rerun,
and its proof/receipt bytes were unchanged. The corrected audit passed.

## Dependency declarations accepted

The fixed graph has eight nodes. dpkg-deb declares only:
libz.so.1, liblzma.so.5, libzstd.so.1, libbz2.so.1.0, libmd.so.0 and libc.so.6.
libc.so.6 declares the fixed ld-linux-x86-64.so.2 provider. The remaining
libraries depend only on libc within this graph. Every declared required version
name exists in the named provider. The reader interpreter is exactly
/lib64/ld-linux-x86-64.so.2. No accepted RPATH/RUNPATH/AUDIT/FILTER/auxiliary or
text-relocation requirement entered the graph.

These are declaration-consistency observations, not actual dynamic loading,
function/symbol resolution, ABI qualification or independent binary provenance.

## Preserved boundaries

Version-proof scope is now 3/4 consumed; one attempt remains. Origin/old-loader,
index, acquisition, local-identity, source/helper and USTAR scopes are unchanged.
No old proof was deleted, repaired or overwritten. Runtime authority, ABI,
platform completeness, actual_dynamic_load_verified and independent provenance
remain false. No Core route, enabled flag, UI/system/config/live-data change.

Normal exact-head merge is cleared after one final GitHub head/tree recheck.
Next gate is a separately reviewed native-reader container/synthetic-fixture
qualification with aggregate cgroup limits; it must not inherit runtime authority
from this metadata result.
