# Isolated USTAR source and launcher review — 4 October 2026

Direct ChatGPT review; no independent Codex/model execution claimed. This record
covers the implementation and synthetic validation, not an already-run native
proof. A separate frozen-commit runtime clearance must bind these source hashes.

## Exact code

- `qualification/media_tar_isolation.py`: `041b63fab388f2be41a08ba49105f3189581491875f6cfc73452c214ed332082`.
- `scripts/qualify_media_tar_isolation.py`: `3d5924b45c4d2dace41bafc76dc35106199b848037596ee60dece940a8429138`.
- `tests/test_media_tar_isolation.py`: `3aee2471def3e4aac18f83f6d8bde9967631867b08dc86a5a6e63d86fa399770`.

## Review findings and disposition

No remaining blocker for one controlled SYNTHETIC matrix after exact commit
freeze/preflight. No acquired .deb/native package tool/image decoder is cleared.

The 18-case matrix has independent golden metadata and strict canonical JSON
comparison, including exact source hashes, fixture byte/hash, run/policy binding,
member payload SHA and false authority claims. Seven malformed fixture cases
require the intended scanner refusal, not an arbitrary error. Native timeout,
stdout/stderr saturation and both cancellation cases require exact expected
host error AND confirmed per-call native cleanup with no competing result.

Three explicit read-only source inputs are hash-checked through held descriptors,
rewound before every transfer, then read back after all cases. Their total two-pass
read budget is below512KiB. Namespace --file copies are immutable/read-only and
independently hashed before imports. The copied tarfile is selected before any
ambient cached tarfile; project namespace has no bytecode, __init__ or app config.
Other interpreter/stdlib/loader/library and bwrap bytes remain a conditional
trusted host baseline, not independently authenticated complete Media runtime.

Guest limits precede optional source imports. The fixed environment, capabilities,
read-only mounts and no-network namespace reuse existing helpers unchanged. The
resource case verifies effective rlimits and caught allocation refusal. It is not
aggregate cgroup accounting or a general hostile-code/fork containment claim.
All cases use fixed, reviewed Python code, never executable archive content.

Caller creates NativeExecutionObservation before scheduling startup. Shared
OwnedProcess/run_process/_own_operation and the existing repeated-cancel helper
settle launch/read/write/group/pipe ownership. Named FIFO readiness is observed
only after pinned-source import, not inferred from task scheduling. Ready/source
FDs stay held until all native observations are cleanup-confirmed. Unknown
settlement produces no receipt and keeps support owners; source closes are not
retried. Shared lease-close failure can clear the marker before failing; the
receipt is refused rather than claiming a universal durable fence.

The initial draft permitted existing0700/0755 proof directories through the
shared no-group/world-write helper. Review tightened both base and old attempts
to exact0700; two added regression cases retain the refusing directory unchanged.
There are two total attempts in the new semantic scope across revisions, no retry
or reset from any previous origin/index/ELF/loader/acquisition/source scope.

The closed evidence wrapper reserves named output ceilings before persistence
and refuses duplicate/unknown names. Three JSON files plus at most two named
FIFOs fit the explicit aggregate/entry budget. Pending cases bind the policy and
run; one support release precedes the separate in-memory proof-bound receipt.
No proof-file I/O follows release. Time checks can refuse late output but do not
claim hard local filesystem deadlines. Fixed stage names preserve failed-case
context without promoting native error text into authority.

135 fake/synthetic tests passed, including real orchestration over fake native
backends, readiness and repeated cancellation, unknown startup, source corruption
before any case, source drift after the matrix, malformed old proof state, late
release failure and output tampering. Four causal controls each remove one
receiver guard in memory and admit the previously refused bad result; on-disk
module remains unchanged.2,441 broader Media/context cases passed before the last
two directory-mode regressions. Final full-source CI and actual evidence remain
separate requirements; the prior scanner's CI is not a substitute.

No shared helper, application route, dependency, UI, daemon/config setting or
live data has been modified. Core v0.15 remains active. The next native packet/
package compatibility and full runtime/import/decoder gates are not cleared here.

Two documentation/context checks each had one failure and140 passes because the
refreshed roadmap omitted the member-inventory-contract and metadata-consistency
phrases. The roadmap now names that completed boundary and limitation explicitly; no context assertion was weakened.
That failed run is not counted as a passing gate.
