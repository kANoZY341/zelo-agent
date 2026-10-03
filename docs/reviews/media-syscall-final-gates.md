# Media pure syscall candidate — final gates

PR: https://github.com/kANoZY341/zelo-agent/pull/78

- Reviewed/tested head: `da006c8c23ef1f15992d957f26670d1b28b832f6`.
- Reviewed/merged tree: `773effc21f1e8b25950bc72718b331e953083133`.
- Merge: `0d3ecec55b63b03592592137e3a9a7b1378755aa`; exact tree verified.
- Matching-head PR CI `37147603265`: success, 2,751 passed, two supplemented
  skips, 11 existing warnings, 711.28 seconds. Earlier push run `37147601185`
  was superseded under existing workflow concurrency.
- Separate post-merge CI `37148652663`: success.

The candidate is 4,198 canonical ASCII bytes with SHA-256
`82478b3fed9fe1e6922cd5120477f5c94a610547d60a1f6ab6d5eded5324c55a`.
It expresses 40 fixed x86-64 syscall intentions, read-only openat flags and no new
executable mmap/mprotect mappings. Exact current bytes, fresh authenticated vendor
reads, immutable fields and fixed false execution claims are required. Browser
additions and helpers remain unchanged; Moby license and attribution are preserved.

Focused checks pass 1,028 cases, including 155 new policy cases and 94 Browser
seccomp cases. Four standalone integration supplements pass in 20.32 seconds.
Two deliberately weakened private clones fail at the intended assertions: removing
exact candidate identity permits a removed rule; removing the vendor SHA pin reaches
the parser before source authentication. The reviewed source worktree is clean.

Externally checked CLI headers establish gpt-6-astra, Medium and read-only operation.
Frozen-source and final full source/evidence reviews clear conditionally on successful
matching-head CI. The condition is satisfied by the successful PR run above.
Retained private evidence is in runtime/media-syscall-final-gates.json,
final-binding.json, source-review.md, final-review.md, focused/full-CI logs and
standalone/negative-control records.

No compiler, installer, library load, filter enforcement, ABI compatibility, decoder,
aggregate resource acceptance or Core authority is granted. The artifact is a
candidate, not an enforcement receipt. Native platform/installer and original
RLIMIT/cgroup/seccomp gates remain before decoder qualification. Production flags
remain disabled, v0.14.0 is preserved and UI #61 remains draft.
