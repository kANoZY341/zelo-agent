# Native reader attempt 4 refusal and build-context comparison — 7 October 2026

Direct continuing-ChatGPT review; no independent Codex/model execution is
claimed.

## Frozen source and CI

Attempt 4 ran from PR #93 integration head
`337435398356dd84f57b38d77a43c6549d3c506b`, tree
`402ce08912876f8e3d33507ac9fb1b284ca6b1a2`. Matching CI run
`37601387861` passed 4,734 tests with two skips; Ruff and diff checks passed.

The append-only v2 continuation grant is retained at
`runtime/media-native-reader-extra-attempt-v2/grant.json`, SHA-256
`4c3dbda2ae77db193c296dbffb105b18f14d306541c6793675e04f0e600d1271`.
It binds the prior three refusals and grants one additional attempt, making the
historical ceiling four without resetting, deleting or rewriting earlier proof.

## Attempt-4 refusal

The one authorized invocation created
`runtime/media-native-reader/native-reader-proof-kvhyvgri`.
Policy SHA-256:
`51a8532bf63d2f603d6e671815850aa86e7984b7040918e0e86fe04569a364b6`.
Cases SHA-256:
`2c3bd1f7b130d3ce57bf317b5119971cdb30b68820b2e4f19503c1c0d14e1251`.

The proof records `status=refused`, `stage=build`,
`support_release_confirmed=false` and `runtime_authority=false`. The bounded
build evidence reports Docker exit code 2, stderr total 20,851 bytes, stderr
capture overflow, stdout 0 bytes and an unclassified sanitized excerpt containing
Go-runtime `unknown pc` text. No matrix/cgroup success is accepted.

Post-run cleanup found no exact Media-reader-labelled containers or images. The
admission marker and lock are empty. Only policy/cases remain in the attempt.
Attempt 4 is consumed permanently. There was no retry.

## Successful build-only diagnostic

The separate diagnostic receipt
`runtime/media-native-reader-diagnostics/ba2d465c4b285717ef6ea75c450f959edf7620fc2e2143abcab12aab86b09857.json`
has SHA-256
`ee68674ecd2ba41feab06676ca60fa4de619f8a6ee0cc09d2f1d59f4897a3411`.
It records one network-disabled/pull-false build, success, confirmed cleanup,
no proof entrypoint and no proof-ledger touch. Bundle SHA-256 is
`0f53fb8375237850a0f3aacb850159ae9453e346f31c936ce85bbd6de2031e04`;
Dockerfile SHA-256 is
`a097907d925d3c2ac2efe9984fc91fa83cbf7c9def98e973b5fb742e5a1b4df9`.

A fresh read/copy/hash comparison from the retained bootstrap inputs produced the
same bundle and Dockerfile digests and the same build flags
`--network=none --pull=false`; only ephemeral lease/path values differ.

## Concrete execution-context mismatch

Before this correction, the successful diagnostic built with exact:
`PATH=/usr/bin:/bin`, `HOME=/nonexistent`, `LANG=C.UTF-8` and its private
`DOCKER_CONFIG`; `_docker` then added `GOMAXPROCS=2`.

The full qualifier's build supplied only its private `DOCKER_CONFIG`; the same
`_docker` added `GOMAXPROCS=2`. Because subprocess execution uses that mapping
as the entire environment, PATH/HOME/LANG were absent. This is a concrete
difference between the successful diagnostic and refused qualification.

The implementation now constructs one exact fail-closed Docker environment and
uses it in readiness, diagnostic and qualification paths. It does not inherit
host HOME, DOCKER_HOST, DOCKER_CONTEXT, credentials or arbitrary environment.

This mismatch is a **plausible cause, not proven causation**. Daemon timing,
resource pressure or another environmental factor may still have contributed.
No fifth qualification run is authorized by this correction.

## Validation and disposition

Focused environment/diagnostic tests pass 52 cases. The complete native-reader
and recovery test selection passes 412 cases. The broader Media/context regression
passes 2,855 cases. Ruff lint/format and diff checks pass.

Exact corrected source SHA-256 values:
- `scripts/qualify_media_native_reader.py`:
  `4a68f9be2694e5a516a2015d650b52782f275a8155b1886549f3b42d56e7e390`;
- `scripts/diagnose_media_native_reader_build.py`:
  `81aa7a5777e4e46f364bc2ffb789269c2c0a5f7d361ebe5b21734f5c1efb0d5f`;
- `tests/test_media_native_reader_operator.py`:
  `c47afcc3d4d2588d06e2049fdcb9c9929c0bfefcfcae3d76294dee180b325e69`;
- `tests/test_media_native_reader_build_diagnostic.py`:
  `7f38839abab98db95b88f91a2cb778eb2076adb29b18830761a7ef536da1bb54`.

Preserve all four refusal attempts, both append-only grant records, reconciliation
evidence and diagnostic receipt. Do not reset/retry/delete history. The next gate
is broader regression, exact-head CI and final review of this correction. Any
future proof requires a separately reviewed explicit budget; real acquired
packages, image decoding and Core/runtime authority remain uncleared.
