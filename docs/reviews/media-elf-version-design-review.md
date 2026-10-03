**Design adjustments required before offline implementation. No execution clearance.**

Reviewed frozen head `049869b58d21f7ef798a3edced3faa06e44f26bd` against PR80 merge `f6a8a58413a8bff9c5f85e6ffee9fd3c220083de`, the requested documents, pinned source text, and observed topology JSON. The diff contains documentation only. No imports, tests, parsing, network access, mutations, or delegation occurred. Model/effort-header verification remains with the external operator.

1. **P1 — Complete the GNU primary-source prerequisite.**  
   [Wire representations and references](/home/zelo/zelo/core-v015-browser-renderer/docs/v0.15-media-elf-version-metadata.md:33) correctly recognize the pinned aliases, but neither these structs nor `gnuversions.py` establishes GNU semantics: the latter explicitly covers SUNW/GNU formats. Before implementation, retain revision-pinned GNU primary-source excerpts covering hidden-bit masking, reserved indices, `vna_other`/`vd_ndx`, flags, and additional Verdaux names.

   In particular, do not present `0x7eff` as a verified GNU maximum. The pinned enum names `0xff00`/`0xff01`; it does not establish the status of every masked value in `0x7f00..0x7fff`. The narrower range can remain an explicit supported-subset restriction. Likewise, label mandatory BASE/SONAME equality, local inheritance closure, NEEDED membership, and zero padding as policy restrictions unless primary evidence establishes otherwise. These crosschecks remain prerequisites; I did not fetch or guess them.

2. **P1 — Specify the output schema and independent acceptance checks.**  
   [Output contract](/home/zelo/zelo/core-v015-browser-renderer/docs/v0.15-media-elf-version-metadata.md:88) currently names categories rather than defining a complete schema. Specify exact keys, types, ordering, table identities, absent-table representation, and all count relationships. Include:
   
   - Unique frequency buckets containing raw value, masked index, hidden bit, and positive count; their counts sum exactly to DYNSYM cardinality when VERSYM exists.
   - Explicit unversioned output with no fabricated LOCAL/GLOBAL frequencies.
   - Declared and decoded parent/auxiliary counts, definition/requirement cardinalities, and inheritance-edge counts.
   - Exact loader-report digest bytes, including whether the newline participates, bound to the same input buffer and loader schema.
   - Host-side rejection of extra keys, incorrect types/counts, inconsistent claims, and noncanonical output.

   Hashes remain recorded but unverified. Name dependencies are version-metadata references, not resolved providers or a complete dependency inventory. The structure-verification claim must describe this restricted language only.

3. **P1 — Finish the numeric work and serialization contract.**  
   [Bounds](/home/zelo/zelo/core-v015-browser-renderer/docs/v0.15-media-elf-version-metadata.md:58) bound tables and strings, but “charge bytes examined” lacks a complete accounting rule. Define numeric allowances for fixed-record reads, padding scans, repeated reads, interval checks, graph traversal, and frequency construction. Clarify that the two-table 131,072-byte bound covers Verdef/Verneed; VERSYM has its separate 65,536-byte maximum.

   The 32,768-name-byte limit does **not** imply a 65,536-byte JSON limit after record/key overhead. Define bounded serialization and whole-input refusal on overflow, without partial success. Charge repeated emitted names and provider names consistently; state whether NUL bytes participate in each budget. Preserve the separate loader budgets.

4. **P2 — Close table and chain ambiguities.**  
   [Table rules](/home/zelo/zelo/core-v015-browser-renderer/docs/v0.15-media-elf-version-metadata.md:41) say every “version section `sh_info`” agrees with a parent count, although VERSYM has no parent count. Limit that rule to Verdef/Verneed and specify VERSYM’s supported `sh_info` separately.

   [Chain rules](/home/zelo/zelo/core-v015-browser-renderer/docs/v0.15-media-elf-version-metadata.md:58) should explicitly require alignment of **every auxiliary-next displacement**, not just the initial auxiliary displacement. State each displacement’s origin, require complete record-width containment before decoding, and define occupied intervals as record bytes rather than entire parent ownership spans. Check the complement for zero padding once, with charging before scanning.

5. **P2 — Make index and graph uniqueness explicit.**  
   [Definition/requirement rules](/home/zelo/zelo/core-v015-browser-renderer/docs/v0.15-media-elf-version-metadata.md:70) explicitly reject duplicate requirement indices but leave definition-index uniqueness implicit in the later “unique map.” Require unique `vd_ndx` values before insertion, plus requirement uniqueness and cross-table disjointness; never allow dictionary replacement to resolve collisions.

   Specify whether repeated Verneed provider parents are rejected or retained distinctly. Preserve declaration order for Verdaux names while defining deterministic output ordering elsewhere. State graph traversal bounds and cover multi-node inheritance cycles independently of record-chain cycles. BASE/SONAME rejection must remain a supported-subset refusal, not an ABI-invalidity claim.

6. **P2 — Make the 39-case and four-attempt plans reviewable.**  
   [Case plan](/home/zelo/zelo/core-v015-browser-renderer/docs/v0.15-media-elf-version-metadata.md:121) adds correctly: `15 + 1 + 16 + 7 = 39`. Its labels do not yet establish complete coverage. Identify the versioned positive fixture and its expected graph, alongside the unversioned fixture. Map every negative to an independently valid baseline, exact mutation, and expected refusal.

   Add an explicit **offline** coverage matrix for companion/link/mapping failures, inheritance cycles, BASE/SONAME rules, definition and cross-table collisions, hidden/reserved boundary values, auxiliary alignment, budgets, output overflow, and authentication failures. This need not increase the 39-process plan.

   [Ownership/authentication plan](/home/zelo/zelo/core-v015-browserer/docs/v0.15-media-elf-version-metadata.md:104) should name the five-file closure and fixed new proof namespace now; literal pins follow once source exists. Provide the retained-file/entry/byte ledger showing that 39 cases fit 96 entries and 8 MiB. The new four-attempt quota must attach to that fixed reviewed scope, not reset through policy-digest or directory changes.

The retained PR80 evidence supports the prior qualification; the topology JSON supports sizes and counts only, not future version-body acceptance. The capacity-one admission, unchanged ownership helpers, pending-proof-before-release ordering, and late-finish receipt refusal are appropriate. Preserve the explicit limitation that a late finish failure may already have cleared the marker.

Resolve these design points before implementation, then obtain exact source/launcher review before any optional parser or guest execution. The old four attempts remain exhausted and retained. UI stays parked; symbol ABI, platform, Core, and decoder authority remain ungranted, with resource/filter gates still before decoding.
