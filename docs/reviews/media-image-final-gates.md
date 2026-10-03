# Media sealed file-image qualification — final gates

PR: https://github.com/kANoZY341/zelo-agent/pull/77

- Reviewed and tested head: `e21c9156b830831157b1f41ddcc537ce64dadc78`
- Reviewed tree: `4dc8ee34cde437c97cde4f9000fa5bb5aff724ed`
- Merge: `3381ada6d78e654de614d93a08debeaf66f13c7d`, with the exact reviewed tree verified.
- Matching-head CI: run `37145586793`, success; 2,596 passed, two supplemented skips,
  11 existing warnings, 701.63 seconds. The earlier push run was superseded by the
  successful PR run under the existing workflow concurrency rule.
- Post-merge CI: run `37146933110`, success; recorded separately from the PR gate.

## Artifact and native evidence

The fixed wheel contains 140 files, six normalized directories including the root,
and 19,667,368 expanded bytes. All notices and 26 native-file roles are retained.
No package or native member is imported or executed.

| Identity | SHA-256 |
| --- | --- |
| Archive, 6,940,830 bytes | `78cb2c6865a35ab8ff8b75fd122f6033b92a62c82801110e48ddd6c936a45d91` |
| Complete normalized manifest | `47adde6932ce7c0c40cf7f781fe142057465771bc2895b00e80f9fdc411a5dc7` |
| Image policy | `85e998165f706030d50ee0173be4f3e6f4ba608cf8978514e147832c5ded6ff7` |
| Image identity | `211eb5b29c8b018ce8e74808a51f4ab75811a10da5e6af39a3195221489d7127` |
| Confinement policy | `bb9849dea40f27288a1086a75e4991684655642d1e4ea9a1ae041924e18c370f` |
| Native proof file | `32eb560644da2aff83ee9bcabffac0e6f4cf797ad7c62c495e378dce4008337e` |

The third of at most four retained attempts passes all 13 operations: two complete
copies; nine file/directory mutation probes; network isolation; absent host sentinel;
absent top-level PIL/app; memory refusal; output limit; CPU kill; live cancellation;
deadline; missing-mount setup refusal; and repeated cancellation. Every native case
confirms owned cleanup; the final image receipt confirms descriptor cleanup.

Host controls deny 140 file writes and growth attempts, and 139 shrink/shared-writable
mapping attempts. `PIL/py.typed` is the sole empty file. Every completed scanner
requires exact complete manifests before and after the operation. Guest writes and
truncates return `EACCES` (13) under the fixed 0400/0500 modes; all seven metadata
operations return `EROFS` (30), including file and directory chmod. No mutation succeeds.

Guest environment equality requires the four fixed forwarded entries plus exactly
Bubblewrap-generated `PWD=/workspace`. Trusted source hashes match before and after
each case. Network proof combines source-enforced namespace inequality, loopback only,
zero IPv4 routes and `ENETUNREACH` (101); no reachable external endpoint is contacted.

The first failed attempt rejected the fixed PWD entry despite a correct complete
copy and confirmed cleanup. The second rejected boolean denial classification despite
unchanged manifests. Both are retained and excluded from acceptance. Each correction
passed fresh tests and Astra Medium static review before the next attempt. The third
records strict integer denial codes. No fallback, unknown environment allowance,
package import, deletion or overwrite was introduced.

## Verification

At the final head, 779 focused Media tests pass with three existing warnings in
9.19 seconds. This includes 121 new image/launcher tests. Four standalone integration
checks pass with three existing warnings in 13.52 seconds. Two deliberately weakened
private clones fail at the intended assertions: removing the WRITE seal removes the
expected write exception; swallowing a reported close failure removes the expected
close-unknown exception. The final source worktree remains clean.

The CLI launcher headers verify `gpt-6-astra`, Medium and read-only operation. The
final static review covers all ten changed files and relevant unchanged helpers,
plus actual native evidence. It independently matches manifest, image, policy,
bootstrap/source and native-proof hashes, examines case-specific acceptance paths,
and clears source/evidence conditionally on successful matching-head full CI.

## Limits

Seals freeze content; modes and shared offsets remain mutable. Trusted source
ancestors, no concurrent host mutator and the platform baseline remain assumptions.
CPU exit 137 does not uniquely establish the kill cause. No aggregate host/tmpfs cap,
hard filesystem deadline, decoder/ABI assurance, complete security/license audit,
current TUF freshness, publisher inheritance, current prepared-tree comparison or
Core authority follows. Production flags remain disabled; v0.14.0 and UI #61 draft
are preserved.

## Retained private evidence

- `runtime/media-image-proofs/image-proof-gxiw_ryu/result.json` and case channels;
- `runtime/media-image-final-binding.json` and final-review.md/log;
- `runtime/media-image-final-focused.log` and final-standalone.log;
- `runtime/media-image-final-supplements.json/log` and final negative-control logs;
- `runtime/media-image-env-review.md/log` and errno-review.md/log;
- failed attempts `image-proof-vsk38r_b` and `image-proof-dk3bra90`.

All pre-merge gates passed at the matching head; normal matched-head merge used no
admin override. Exact merged-tree verification passed. Post-merge CI remains separate.
