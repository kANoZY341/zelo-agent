**BLOCKED — do not execute the second 18-case attempt at `8f4a125a8487eb756801b13b61fdf4d77a68fc59`.** Three concrete mount/FD findings require fixes and exact-source re-review.

1. **The mounts do not preserve the claimed held-descriptor identity.**
   [Launcher line 192](/home/zelo/zelo/core-v015-browser-renderer/scripts/qualify_media_origin.py:192) supplies `--ro-bind /proc/self/fd/N`. Pinned bubblewrap [resolves ordinary bind sources with `realpath()`](/home/zelo/zelo/core-v015-browser-renderer/runtime/media-origin-bwrap-reference/bubblewrap.c:1528), then mounts the resolved pathname. The descriptor spelling therefore does not establish the mandatory held-inode binding.

   Use a source-supported attachment design that actually preserves that identity. Retain guest authentication and review its mount, mode, resource and ownership consequences. Add offline coverage that distinguishes descriptor binding from pathname resolution.

2. **Supplied support FDs survive into the bootstrap.**
   [Launcher line 245](/home/zelo/zelo/core-v015-browser-renderer/scripts/qualify_media_origin.py:245) passes all six tool/key/input and bad-pin descriptors on ordinary cases. These pathname-based mount operations do not register them as operation FDs, so bubblewrap’s [`close_ops_fd()`](/home/zelo/zelo/core-v015-browser-renderer/runtime/media-origin-bwrap-reference/bubblewrap.c:1509) does not close them. Its [command-child path explicitly retains passed descriptors](/home/zelo/zelo/core-v015-browser-renderer/runtime/media-origin-bwrap-reference/bubblewrap.c:3325).

   Consequently, the [guest’s extra-FD guard](/home/zelo/zelo/core-v015-browser-renderer/qualification/media_origin_guest.py:215) would refuse before GPG under these referenced semantics. Preserve that guard. Define and test explicit descriptor consumption/closure across the transition, including bad-pin and readiness descriptors, while retaining host ownership until settlement.

3. **Cancellation controls use an unsupported option in the pinned reference.**
   [Launcher line 194](/home/zelo/zelo/core-v015-browser-renderer/scripts/qualify_media_origin.py:194) emits `--bind-fd`. The pinned v0.9.0 parser has no such option; it reaches [“Unknown option”](/home/zelo/zelo/core-v015-browser-renderer/runtime/media-origin-bwrap-reference/bubblewrap.c:2532). Its documented `--bind-data`/`--ro-bind-data` operations copy data and are not equivalent FIFO attachments.

   Specify a supported readiness attachment and its descriptor lifecycle. If relying on a downstream-added option, retain exact supporting source evidence. Conditional trust in the Ubuntu build does not establish an undocumented interface.

The current fake-launch tests check constructed arguments and synthetic outcomes; they do not resolve these bubblewrap semantics. No native experiment is needed to justify fixing these findings.

The other reviewed areas do not add blockers:

- The corrected exact `1000/0/1` map follows the unprivileged devpts/second-user-namespace source path. IDs remain 1000 and capability checks remain zero.
- R1’s creation-specific `EROFS`, absence-specific `ENOENT`, CRC-case removal and complete pinned-key negative transcript are addressed.
- Positive status ordering, identity checks and settled-invocation acceptance remain bounded; synthetic statuses prove no cryptography.
- The ledger matches **44 entries / 5,533,357 bytes**. Shared admission, canonical four-attempt origin scope, observation-before-creation, cancellation settlement, support retention/close-once and pending-proof-before-release ordering remain. Late `Lease.finish` failure still cannot support universal fencing claims.
- Historical-root attribution and the conditional Ubuntu-tool baseline remain appropriately limited.

I read the complete new implementation and both test files, relevant reused lifecycle/process/admission sources, and pinned protocol paths. All ten GNU and three bubblewrap reference hashes match their manifests. The retained first-attempt proof matches `43d78fc777a9cef34d971c8ce18e930f79cfba1c5937c42dc4b3ca51914a065f`. HEAD remained exact, with no tracked diff.

**Origin remains 1/4; ELF version 2/4 and loader 4/4 remain untouched.** No execution, tests, edits, network, Git mutation or delegation occurred. No retry or grammar widening is authorized. Matching CI, refreshed supplements and final actual-evidence review remain later gates; excluded platform/Core authority remains excluded.
