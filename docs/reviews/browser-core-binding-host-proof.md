# Browser Core binding controlled host proof

2 October 2026. Dedicated development worktree; no live Core configuration,
database, service, UI or published release changed. Browser remains disabled by
default. Explicit opt-in harness: `scripts/qualify_browser_core_binding.py`.

## Actual host scope

Eight cases passed through real TaskService submission/persistence, claim, Runner,
qualified DocumentRenderer, native-sandbox Chromium, Docker handles, resource owner
and admission cleanup. HTTP/DNS used controlled fixtures; this is not live public
website browsing or production activation. Page scripts remain disabled. Separate
Python contender processes verified persistent cross-process admission.

1. Actual Chromium text persisted with matching contract/origin and untrusted evidence.
2. Long-node previews retained truthful truncation reporting.
3. Task success/evidence waited for actual cleanup; independent contender was denied.
4. Lost actual Docker-create acknowledgement failed without leaking a result.
5. Cancellation after actual render withheld evidence and confirmed cleanup.
6. Deadline after actual render withheld evidence and confirmed cleanup.
7. A terminal task with delayed cleanup retained its fence. A second Core task
   failed before HTTP or container creation; later cleanup did not change the
   failed task into success or expose evidence.
8. Fresh Core task readmission succeeded after confirmed cleanup.

Runtime preserved the prepared image across jobs. Harness setup cleanup removed
only diagnostic-owned labelled resources, with no cleanup errors. Image proof used
an unstarted exact-image filesystem export and checked all 339 expected file
bytes/modes without extraction. This is payload verification, not code signing or
independent provenance. The local operator/filesystem/Docker daemon remain trusted.

## Recorded evidence

Final expanded run: `runtime/browser-core-binding/core-proof-o09_fv84/result.json`,
run ID `61463ea96791419dae743e41ea11b6fb`, exit 0, 49.13 seconds.
Source bundle digest:
`5cd2da1e5d84d268dbe7c46e6a95f82d640babed410ffd1f31c681de37f424ca`.
Prepared manifest:
`c61e53c6957dbcb68e2c670f4fdd61a916ab834f4a23b1c25d047e842d770d94`.
Actual image payload:
`4823558cba331ed150d69ceb6bbeefea74ce9865110d7724bfb20689ade5c6a6`.
Proof contract:
`4b83c0a6280e1aee1b2a17497a802453bb77597419231779c5f657a0896ad8d0`.

Earlier seven-case run passed before final typed deadline refinement; it is
supplemental evidence, not the final implementation gate. Offline harness opt-in
regression separately passed (one test), verifying no host setup without the flag.
Final parent offline Core plus harness run passed **63 tests**, with three existing
warnings, after all implementation drift/cancellation/application-assembly cases
and a malformed receipt numeric-overflow regression were included. Parent also
passed whole-tree Ruff lint/format and diff checks. Counts overlap earlier focused
runs and must not be added. Full pytest, independent exact-head review and
exact-head CI are separate gates.
