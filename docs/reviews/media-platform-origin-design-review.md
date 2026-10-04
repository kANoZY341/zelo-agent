**Clear for offline implementation, with mandatory source-review gates before native execution.** I found no architectural blocker to writing the fixed origin module, launcher and pure adversarial tests. This is **not approval to execute GPG, decompress indexes, inspect packages or grant platform authority**.

Reviewed proposal SHA256: `f8a2851fb5fba78ec4ae78d520f43258c80d38584cb44a1eaecdf29fe58388fc`.

I read the requested documents and manifests, applicable repository instructions, sandbox/process/admission ownership sources, existing qualifier lifecycle and bootstrap, and local GPG manual and Chisel references. I accept PR82 merge `a473f46` and matching CI as operator-verified. No new origin implementation exists to assess.

The following findings must be closed in the implementation and its source review.

1. **The GPG acceptance protocol is not yet executable specification.**

   [Proposal lines 58–72](/home/zelo/zelo/core-v015-browser-renderer/runtime/media-platform-origin-proposal.md:58) correctly require strict statuses and defer exact grammar, but “bounded documented auxiliary statuses” leaves acceptance unresolved. The local manual supports the proposed options; it does not establish the complete status sequence or authenticated-output semantics.

   Before native execution, review pinned GnuPG 2.4.4 `DETAILS` and relevant source, accounting for the installed Ubuntu revision. Freeze status ordering, cardinality, field lengths, escaping, signature-version/reserved fields, signer versus primary fingerprint, and failure handling. `GOODSIG` identity text must not become a trust decision.

   Establish how this invocation handles unsigned prefixes/suffixes, concatenated messages, additional signatures, malformed armor and plaintext emitted before verification finishes. Accept plaintext only after the **same invocation** exits successfully, its complete status stream validates, and ownership settles. Offline fixtures must distinguish this sequencing from merely finding `VALIDSIG` somewhere in stderr. Do not widen the grammar opportunistically after a failed attempt.

2. **The proposed sandbox needs an explicit origin-specific mount and exec design.**

   The [existing compute plan](/home/zelo/zelo/core-v015-browser-renderer/app/developer_sandbox.py:323) mounts selected Python/library paths, not all `/usr`; it does **not** expose `/usr/bin/gpgv`. It also remounts `/tmp` and the root filesystem read-only. The [existing guest audit hook](/home/zelo/zelo/core-v015-browser-renderer/qualification/media_elf_metadata_guest.py:188) prohibits `os.exec`.

   Specify a separately reviewed bootstrap that authenticates its sources, sets limits and directly execs the fixed GPG executable. Bind the held, verified executable descriptor at its fixed guest path; holding a descriptor while independently executing a pathname is insufficient identity binding. Attach input and keyring similarly. Define creation of the private homedir before read-only remounting, exact loader/library mounts, working directory and environment.

   Freeze the descriptor map across host → bubblewrap → bootstrap → GPG. GPG should receive only the intended standard streams and any explicitly justified descriptors; it must not inherit admission, proof, host-directory or readiness authority. Preserve shared helpers unchanged.

3. **Root-owned files and process identity must be specified separately.**

   [Lines 39–49 and 74–88](/home/zelo/zelo/core-v015-browser-renderer/runtime/media-platform-origin-proposal.md:39) establish root-owned input/tool expectations but leave the executing UID/GID and namespace mapping unstated. The Release candidate manifest supplies hashes and sizes but no fixed modes.

   Freeze expected file modes and host-side ownership checks before native execution. Explicitly retain trusted ancestors/mounts and no concurrent mutator assumptions: final-component `O_NOFOLLOW` and retained descriptors do not establish immutable bytes against an authorized writer.

   Also define operator and guest real/effective IDs, mappings and capability state. Treat `NPROC=0` as supplementary; do not infer process containment solely from it, especially without resolving root/capability behavior. Review and later demonstrate the direct exec transition under these limits. Python audit restrictions do not survive as enforcement inside GPG.

   The conditional trust in host Python, bubblewrap, loader and libraries is acceptable for this qualification tool. It must remain visibly distinct from authenticated platform provenance.

4. **The independent trust-root claim exceeds the retained primary notes.**

   [Proposal lines 51–56](/home/zelo/zelo/core-v015-browser-renderer/runtime/media-platform-origin-proposal.md:51) say the fingerprint was cross-checked against Canonical security documentation and pinned Chisel YAML. The YAML contains the stated full fingerprint, and its bytes match the manifest. However, the [primary notes](/home/zelo/zelo/core-v015-browser-renderer/runtime/media-platform-origin-primary-notes.md:3) report a failed full-page retrieval and do not preserve that fingerprint cross-check.

   Before native acceptance, retain the exact supporting source evidence or accurately describe the root as explicitly operator-selected with the available Chisel corroboration. Bind the full fingerprint in policy independently of whatever key GPG reports; the keyring hash alone is not that selection.

   The historical-only trust statement is sound. The local manual confirms GPGV trusts supplied keys without checking expired/revoked keys. No freshness, present key validity, security-support or package-safety claim follows.

5. **Admission is correctly shared; quota identity and the resource ledger still need freezing.**

   [Lines 91–99](/home/zelo/zelo/core-v015-browser-renderer/runtime/media-platform-origin-proposal.md:91) preserve the correct capacity-one admission and release ordering. The existing helpers support observation before creation, repeated-cancellation settlement and conservative fencing, provided the new caller follows their ownership contract.

   Freeze one canonical origin-proof directory/prefix, shared across all origin policy revisions, and count every retained attempt—including failed and partial attempts. Acquire the existing admission before proof allocation, source reservations or process creation. Reject malformed quota state without deletion or automatic recovery.

   Preserve **two consumed version attempts out of four**, and **all four exhausted loader attempts**. The distinct origin scope must never become a route to rerun or reset either.

   The proposed limits are reasonable starting bounds, but not yet a demonstrated ledger. Freeze the native case count and worst-case entries/bytes, including simultaneous input, plaintext, status, serialization, negative fixtures and failure evidence. Explain what the 16 MiB support reservation covers. Per-process address-space/CPU limits do not establish aggregate host limits; retain the explicit absence of a cgroup claim.

   Preserve pending proof before release, final stdout after successful release, and no subsequent proof-file I/O. The late `Lease.finish` caveat is accurate: a failure can occur after marker truncation, so refuse the receipt without claiming universal fencing.

**Stage boundaries are appropriate, with these clarifications:**

| Stage | Review decision and permitted claim |
|---|---|
| Offline implementation | Clear for fixed parsing/validation, launcher construction, synthetic tests and ownership fault tests. Synthetic statuses prove no cryptography. |
| First native Release gate | Blocked pending the source/invocation findings above and a frozen case matrix. Eventual success establishes only authentication of the three fixed historical Release snapshots and selected signed index declarations. |
| Index retrieval/decompression | Separate gate. Specify fixed sources, transfer/redirect policy, decompressor ownership, compressed and expanded bounds, and exact signed size/hash bindings. Check compressed bytes before decompression and expanded bytes against the selected uncompressed record before package parsing. |
| Package-member attribution | Separate gate for exact `.deb` identity, inert streaming, descendants, archive/member bounds, link semantics and notice/source/relink obligations. Release authentication alone does not authenticate installed files. |
| Full platform authority | Remains false: import/frozen/builtin closure, actual loaded identity, search/dlopen/ABI, immutable materialization, daemon settlement, resources/startup/installer/filter and decoder gates remain outstanding. |

For the offline index validator, make continuation rules, numeric limits, canonical paths and identical-versus-conflicting cross-pocket records explicit. Retain the fixed package/version set and refusal on missing versions; historical retrieval requires its own review.

The reuse decision is sound: GPGV supplies cryptography, later `dpkg-deb` supplies package streaming, and Zelo retains policy and ownership. All six retained Chisel reference-file hashes match their manifest. Its license text is AGPLv3; the missing release-definition license supplies no vendoring permission. Research-only use is appropriately separated from incorporation. Tool/component redistribution and notice obligations remain later gates.

One evidence bookkeeping discrepancy should be corrected: [the final-gates record](/home/zelo/zelo/core-v015-browser-renderer/docs/reviews/media-platform-dependency-final-gates.md:1) records final-review SHA256 `ee5364…b43`, while the current final-review file hashes to `86bbc42f8b6f436a6631b51c45c92639b66f62290daa1448b23dd74a31b7e238`. Identify the intended review artifact or correct the reference. This does not override the supplied operator verification of PR82.

This review used static text reads and SHA256 only. No edits, tests, imports, native qualification, network, mutations or delegation were performed.
