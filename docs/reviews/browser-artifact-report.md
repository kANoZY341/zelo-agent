# Browser artifact integrity prerequisite — 2 October 2026

Implemented only in `feat/v0.15-browser-artifact-qualification`, stacked on exact
parent `f841a14c01928d3cb5333654421c3c6eb3030b0c`. Parent PR #63 is still under
validation. This slice records and verifies prepared artifact content; it grants
no execution permission and activates no worker, task, capability or AgentSpec.

## Interface and identity

`qualification/artifacts.py` exposes frozen `ArtifactPolicy`, `ArtifactFile` and
`ArtifactManifest` records plus `capture_manifest` and `verify_manifest`.
A trusted operator supplies the prepared root, explicit policy, and a tuple of
`(role, relative_executable_path)` pairs, for example browser and controller.
There is no page/task input surface or environment discovery. Roles are unique
lowercase ASCII identifiers, at most 32 roles of at most 64 characters each.
Relative paths are canonical printable ASCII, at most 4096 characters; absolute,
empty, dot/traversal components, backslashes and control characters are rejected.
Symlink-bearing bundles must be independently prepared as regular-file copies;
this API does not resolve or materialize them.

The candidate manifest includes version, canonical sorted file paths, sizes,
permission modes and content SHA-256, sorted directory paths/modes (including
root and empty directories), sorted required-role mapping, and policy bounds.
Its SHA-256 is over canonical JSON. Executable-bit, role and policy changes alter
identity. Root location, inode/device and timestamps are deliberately absent
from persistent identity, so identical prepared copies relocate deterministically.
All required paths must resolve to inventoried regular files with at least one
executable bit. This checks mode bits, not ELF/script validity, interpreter or
shared-library closure, effective-user access, ACLs or mount `noexec` semantics.

Capture only records a candidate: arbitrary captured content is never labelled
approved. Verification requires an independently trusted expected digest, checks
that it identifies the expected manifest, rescans and requires exact equality.
The caller must supply digest provenance through a separate trusted channel;
passing a digest from the same arbitrary capture proves no trust. No content is
returned, and no metadata is loaded through pickle, exec or import.

## Bounds, races and descriptors

Explicit integer maxima are validated against fixed ceilings: 4096 files,
16 path components below root, 512 MiB per file, 1 GiB aggregate and 8192 total
entries including directories. Booleans, floats/nonfinite values and unbounded
values are rejected. Directory enumeration is incremental and entry-bounded;
file reads use 64 KiB chunks and reject growth beyond the observed bounded size.
A one-byte overflow probe can be read solely to reject growth. Empty directories
consume the entry budget. Traversal retains only descriptors along a bounded
ancestor chain and closes them in finally blocks; scandir iterators use contexts.

Linux descriptor-relative stat/open/scandir operations use no-follow semantics;
opens include O_NOFOLLOW, O_NONBLOCK and O_CLOEXEC, with O_DIRECTORY for directories.
The root is checked without following its final component and opened as a directory.
Trailing slash/dot spellings that could hide a symlink root are rejected.
Symlinks, multiply-linked regular files, nonregular objects, and group/world-writable
files/directories/root fail closed. Object type is checked before open/read;
a type replacement between stat and open is checked before any content read.

Device/inode, size, mode, link count, nanosecond mtime and ctime must agree across
stat/open/hash/close-boundary observations. Directory metadata changes during
traversal fail. A second complete metadata inventory revisits earlier files and
directories after hashing; final root pathname identity is checked too. These
checks detect controlled file replacement, in-place mutation and inventory races.

This is change detection, not filesystem freezing, mount enforcement, code signing,
defense against trusted administrators or complete TOCTOU elimination. Trusted
ancestors and operator, and local Linux filesystem metadata semantics, remain
assumptions. Ancestor symlinks are not independently traversed/qualified. Concurrent
mutation after the last observation, mount substitutions, malicious filesystems,
and metadata-restoring privileged actors are not defeated. No hard wall-clock
bound on filesystem I/O is provided. Actual execution must later bind a verified
immutable image or appropriately held artifacts; this API closes its descriptors.
Extended attributes, ACLs, capabilities and ownership are outside content identity.

## Validation

Only controlled temporary bundle fixtures were scanned, never installed browsers
or arbitrary system trees. Commands used the already-existing shared environment
executables without installing or modifying that environment:

- `/home/zelo/zelo/core/.venv/bin/python -B -m pytest -q tests/test_browser_artifacts.py`:
  **106 passed, 1 skipped**, three pre-existing collection/import warnings.
- `/home/zelo/zelo/core/.venv/bin/ruff check --no-cache qualification/artifacts.py tests/test_browser_artifacts.py`.
- `/home/zelo/zelo/core/.venv/bin/ruff format --no-cache --check qualification/artifacts.py tests/test_browser_artifacts.py`.
- `git diff --check`, plus whitespace checks of all three new untracked files.

Focused cases cover relocation/determinism, immutable results, content/inventory/
rename/mode/role/policy mismatches, missing/nonexecutable roles, invalid inputs,
all budgets, links/FIFO/writability, real controlled hash-time mutations and
replacements, late directory changes, root replacement and descriptor accounting
on repeated failures. Socket creation is forbidden by this execution environment
(`PermissionError` on Unix socket bind); its real-object case is explicitly skipped.
Synthetic socket/character-device/block-device metadata tests verify rejection
before open. No real device object was created or accessed. An initial run exposed
the socket restriction (98 passed, one setup failure); the final run above makes
that limitation explicit. The developer-validation example's `/opt/zelo-venv`
executables were unavailable; no developer-validation contract was changed.

Full pytest, independent review and exact-head CI remain parent-owned gates; none
was run here. No GitHub/Docker call, install, browser/service run, commit, push or
merge occurred. Only the new module, focused tests and this report were added.
Core application, existing qualification modules, UI and other worktrees were not
modified. Core-first/UI-parked priority and canonical milestone order remain intact.
The default nonroot Docker browser candidate previously failed `No usable sandbox`;
no bypass was enabled. Artifact verification does not resolve that native sandbox
gate or establish browser-runtime compatibility.

## Parent host verification

All 107 artifact cases passed on the host, including the real Unix-socket case that
was skipped under the delegate sandbox. The expanded serial gate passed 355 tests
with three existing warnings. Real candidate bundle capture/rescan matched across
339 files and 291287637 bytes without executing content. See
`docs/v0.15-browser-artifact-qualification.md` for proof scope. The parent branch
has now been fast-forwarded onto the verified PR #63 merge (`2bb48bb`).
