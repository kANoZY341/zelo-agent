# PR81 GNU version graph metadata — final gates

PR: https://github.com/kANoZY341/zelo-agent/pull/81

- Reviewed head:0bf270640a6b63e823351ecdd254ec84bf39c894; tree:de2ab30f9be9abfa034a288bd48087bb2f3ffe83.
- Merge:8aeaf6b18d45f9b9ed04831f65135992727117f7; reviewed and merged trees are identical.
- Matching PR CI:37161059534 succeeded:3,189passed,2supplementedskips,
  11warnings in700.73seconds.
- 1,372focused Media passes;389new+loader passes,including261new.
- Fourstandalone checks passed in a pristine detached checkout; two causal faults
  each produced the intendedDIDNOTRAISE failure with1failed/35passed.
- AstraMedium complete source/evidence review cleared conditional on matchingCI;
  that condition is met. Original review SHA256:99c38cde25caa9253e8ce13f50ebabdcb1e9a1f32e773044d11b768aaf3ae5ab.
- One explicit version attempt passed39cases/seveninputs11,487,248bytes, allowned
  cleanup and admissionrelease confirmed. Fourpriorloaderattempts remain retained
  and exhausted; no retry/reset/delete occurred.
- Proof:runtime/media-elf-version-proofs/version-proof-hi8ce0sd/cases.json.
- Proof SHA256:334542441c1873a6c0cde3525e112bf3b135520b0f425210c730cf36217f72fd.
- Policy SHA256:5de01c080755e65b174013ea38a0db1facfd7137478286cce314ef3ea4a42a1d.
- Finalstdout SHA256:d93f21fe39ba22b48487c99846afa700c1840134e30f36cbeead713ceea6bbcc.
- Retained77entries/384,654regularbytes. Savedproof releaseFalse; finalstdout
  releaseTrue bindsproofSHA with no postreleaseproofIO.

Accepted scope is restricted GNU version-graph structure metadata only. Allseven
embeddedloaderreports match the priorPR80 data. No symbol-provider resolution,
hashverification, ABI/platform/provenance qualification, decoder/filter/build or
Core/runtimeauthority. Trustedhost/ancestors/no-concurrent-mutator andreviewedguest
parsing remain assumptions. Late Lease.finish may already clear marker before
refusingreceipt; no universal fence claim. UI61parked; flagsdisabled/v0.14.0preserved.

Complete review: media-elf-version-final-review.md. Private fullbinding:
runtime/media-elf-version-final-binding.json; finalgatesJSON:
runtime/media-elf-version-final-gates.json. PostmergeCI is separate from this
matching-head acceptance; no postmerge success is inferred here.
