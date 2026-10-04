**DESIGN REFINEMENTS REQUIRED before offline implementation clearance.** The architecture and reuse decision are sound, but several acceptance rules remain unspecified. No actual index download is needed to resolve them.

Reviewed HEAD `fbf1cb21aa1108b73818f81583fec5351729bd1a`; proposal SHA256 `b230203be1ac355e0e0690f574497242130434d08bc81105a2499116cb9ad886`. I read the entire draft, including its later refinements, original origin design/review, final PR83 review, reference manifest/source, origin validator and relevant test/style conventions.

1. **Freeze lexical rules and their numerical bounds.** [Concrete decisions](/home/zelo/zelo/core-v015-browser-renderer/runtime/media-origin-index-parser-next-design.md:111) still say “bounded lexical” and “fixed component/depth/length constraints” without specifying them.

   Define field-name grammar/length; Package, Version, Architecture and Source alphabets/lengths; selected path component alphabet, component length, depth and total length; canonical positive decimal Size grammar; and exact SHA256 grammar. Explicitly reject empty, `.` and `..` path components without normalization or decoding. Preserve the literal epoch in the selected zlib version.

   Also freeze whitespace handling around colons and simple values: ASCII space/tab trimming versus refusal, with no Unicode whitespace normalization. Define whether “other controls” includes decoded C1 characters; do not reject UTF-8 continuation bytes merely because their byte values overlap that range.

   The later decision to discard unselected Size as a bounded string is acceptable and supersedes the earlier numeric-domain proposal. State clearly that it receives generic syntax checks, not numeric conversion or resource authority.

2. **Complete Source and cross-pocket equality semantics.** [Source/merge rules](/home/zelo/zelo/core-v015-browser-renderer/runtime/media-origin-index-parser-next-design.md:120) omit the source-version result when `Source: name` has no parenthesized version. Specify binary Version as that diagnostic default, alongside the already specified absent-Source default.

   Define the exact comparison tuple: package, version, architecture, filename, size, SHA256 and source representation. Resolve whether absent Source and an explicitly equivalent Source coalesce or conflict. Either conservative choice can work; implementation must not decide implicitly. “Byte-identical” must refer to declared metadata equality, never observed package bytes.

   Require exactly one result for each of `noble`, `noble-updates`, `noble-security`. Validate bounded result structures, suite identity, selected membership, duplicate rows and exact integer types before merging; caller dictionaries remain untrusted metadata. Discarded descriptions and dependencies must not affect equality.

3. **Make streaming boundary accounting deterministic.** [Streaming rules](/home/zelo/zelo/core-v015-browser-renderer/runtime/media-origin-index-parser-next-design.md:95) have reasonable ceilings, strict complete-line UTF-8 decoding and correct all-input-before-output behavior. Finish these details:

   - Define which terminating LF/empty separator counts toward the 65,536-byte stanza bound, including EOF without an extra separator and repeated separators between stanzas.
   - Specify empty-input and separator-only-input outcomes.
   - Bound the **current stanza before appending partial-line bytes**, not only when a line completes.
   - Retain only the bounded current line, field-name set, necessary simple values and at most eight selected records; discard continuation bodies without accumulating them.
   - Make success independent of valid chunk partitioning and field order. Iterator failure must produce no result. The trusted iterable itself supplies no hard timing or I/O-isolation guarantee.

The fixed selection is correct: all eight require literal `amd64`, with no `all` substitution or version ordering:

| Package | Exact version |
|---|---|
| python3.12-minimal, libpython3.12-minimal, libpython3.12-stdlib | `3.12.3-1ubuntu0.17` |
| libc6 | `2.39-0ubuntu8.9` |
| libffi8 | `3.4.6-1build1` |
| libseccomp2 | `2.5.5-1ubuntu3.1` |
| zlib1g | `1:1.3.dfsg-3.1ubuntu2.2` |
| libexpat1 | `2.6.1-2ubuntu0.6` |

Per-index selected duplicates must refuse even when identical; cross-pocket conflicts must refuse; completion requires all eight after every input validates.

The **independent narrow validator reuse decision is justified**. The retained python-debian 0.1.39 hash matches its manifest. Inspected code confirms case-insensitive overwrite, continuation concatenation, paragraph buffering, optional `apt_pkg`, broader imports and encoding-detection fallback. These do not provide the proposed strict acceptance or allocation bounds. Its header states GPL-2.0-or-later; this remains historical reference inspection with no copied/imported implementation.

The proposal follows Deb822’s basic stanza, field and continuation model while deliberately restricting acceptance. Document LF-only input, mandatory final LF, whitespace-only-line refusal and restricted lexical forms as policy restrictions. I did not retrieve the linked public documentation under the no-network constraint; no fresh public-document verification is claimed.

Implementation must include independent synthetic expectations, exact-limit/limit-plus-one cases, chunk/field-order invariance, malformed trailing input, Source equivalence/conflict cases, forged merge structures and causal faults. Matching full CI and full source review must precede normal exact-tree merge; PR83 must first merge normally.

This review authorizes **no retrieval, decompression, package/member processing or further origin run**. Origin **3/4**, ELF version **2/4**, loader **4/4**, admission and old proofs remain unchanged. Pure selection establishes no actual index/package provenance. Platform, resource/filter-before-decoder and Core gates remain intact.

Only sequential static reads/Git inspection and SHA256 hashing were performed; tracked diff is empty. No edits, tests, imports, network, execution or delegation occurred. I cannot independently attest the session’s requested **gpt-6-astra Medium** configuration.
