**BLOCKED — do not execute the 19-case origin attempt at `8a536f0633101809839e846483190053bacbf8ee`.** Two control-validation findings require fixes and exact-source re-review.

1. **The write control can pass on a writable filesystem.**  
   [media_origin_guest.py:169](/home/zelo/zelo/core-v015-browser-renderer/qualification/media_origin_guest.py:169) treats any `OSError` from opening, writing or closing as proof of confinement. With `RLIMIT_FSIZE=0`, creation can succeed and the subsequent buffered write/close fail with `EFBIG`; the control still reports `enforced=True`.

   Require failure of the **creation operation itself**, with the expected `EROFS`, and fail immediately if creation succeeds. Add an offline regression covering successful creation followed by `EFBIG`. The adjacent host-file probe also needs an absence-specific result: arbitrary `OSError`, including `EACCES` or `EIO`, does not prove absence.

2. **The negative-input checks do not establish precise failure causality.**  
   [qualify_media_origin.py:294](/home/zelo/zelo/core-v015-browser-renderer/scripts/qualify_media_origin.py:294) accepts the armor negative with exit `2`, no helper error, and no `GOODSIG`/`VALIDSIG`. Empty stderr—or unrelated GPG failure statuses—satisfies that predicate.

   The pinned [gpgv exit implementation](/home/zelo/zelo/core-v015-browser-renderer/runtime/media-platform-gnupg-reference/g10_gpgv.c:291) uses exit `2` for general logged errors. The [CRC failure implementation](/home/zelo/zelo/core-v015-browser-renderer/runtime/media-platform-gnupg-reference/g10_armor.c:829) does not supply a unique machine-status marker, while this invocation discards diagnostics to `/dev/null`. Therefore the retained evidence cannot distinguish the intended CRC refusal from another failure.

   Freeze a source-supported, bounded diagnostic/status contract that identifies the intended armor failure, reviewing any invocation, FD or ledger changes. For altered text, replace the substring search with complete negative-status validation requiring the pinned key’s `BADSIG` and rejecting unrelated, duplicate or contradictory statuses. Add offline counterexamples for empty exit-2 output, unrelated errors, wrong-key `BADSIG`, and embedded status substrings.

The remaining source-review findings are addressed within the conditional trusted-host scope:

| Area | Assessment |
|---|---|
| Positive protocol | Source-supported ordering, cardinality, signer/primary pins, algorithms and timestamps; plaintext accepted only after successful settled invocation. Synthetic fixtures prove no cryptography. |
| Mount/exec identity | Held-descriptor mounts bind executable, keyring and input; limits precede guest file access; direct fixed exec checks for surviving FDs above 2. |
| Identity/confinement | Explicit host/guest IDs, namespace maps, zero capability sets, fixed environment and host root-owned modes. Trusted ancestors and no concurrent mutator remain assumptions. |
| Historical root | Explicit operator selection and pinned Chisel corroboration accurately replace the unsupported independent-documentation claim. |
| Resources/quota | Ledger arithmetic matches **47 entries / 6,200,154 bytes**. One canonical four-attempt origin scope shares metadata admission; ELF version **2/4** and loader **4/4** remain distinct and untouched. |
| Ownership/release | Observation before creation, cancellation settlement, support retention/close-once and conservative fencing remain. Pending proof precedes release; no proof-file I/O follows successful release. Late `Lease.finish` failure still cannot support universal fencing claims. |

I reviewed the complete new implementation and both test files, relevant reused ownership/bootstrap/process/admission sources, and pinned GNU protocol paths. All ten GNU reference hashes match their manifest. HEAD remained exact and tracked files clean. PR82 merge and postmerge CI success are accepted as operator-verified.

No execution, tests, edits, network, Git mutations or delegation occurred. No quota was consumed. Grammar widening and automatic retry remain prohibited. Standalone/causal/full-CI gates and final source/actual-evidence review remain pending. Ubuntu-patched GPGV remains a conditional operator baseline; platform, materializer, index/package, filter/decoder and Core authority remain excluded.
