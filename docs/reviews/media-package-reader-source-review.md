# Exact-version package-reader source review

Reviewed directly in the continuing ChatGPT session on 4 October 2026. No separate
Codex/model execution is claimed. This review accepts bounded static research
records only; it does not clear installed dpkg-deb execution or package parsing.

## Source identity and accepted observations

Ubuntu Noble primary publication metadata and the source descriptor both identify
source dpkg 1.22.6ubuntu6.6. The version-specific applied tag resolves to commit
ba5fe18eb68084206981fb7b3a7c281eb2d38372; imported commit is
921380b137c23dca61ed746e0136995a765686bf. Both official cgit pages report tree
037fc7f75d736e9c14fcd7dbf2c3b09288ac5dec. The applied commit lists the imported
commit as a parent. The dsc declares native source format and one source tarball,
5,548,716 bytes, SHA256
7048ee31e30066ae123cdcb891cfa1ad55b2f42a4e04fca2c5db41607ae25196.
The tarball was not downloaded and the dsc signature was not verified.

The retained source corpus contains 15 files / 1,115,169 bytes. Sixteen requests
were consumed: five successful files, one HTTP404 for an incorrect old-layout
path, then ten corrected, explicitly reviewed remaining paths. No successful
file or failed URL was retried; neither corpus nor original quota was reset.

A separate stdlib-only audit verified pinned manifests before JSON parsing,
private inventory and stable held-FD identity, all source sizes/SHA256/Git-blob
hash observations, exact URLs/commit/version and false authority claims. All
33 in-memory tamper controls passed. It used no network, project imports,
installed-input reads, archive reads or target code execution.
Audit SHA256: 44a0239b015f22a90e956f48d1428d7f047c080a2681a20a87c5b177641e273a.

These findings establish conditional official-host source research, not an
independently authenticated binary-to-source chain. The dsc signature, Git tree
membership proof, source-archive equivalence, exact installed build and native
library closure remain unverified. Observed blob hashes are not such proofs.
The source copyright matches the previously retained installed notice byte for
byte; that supports notice consistency, not executable membership.

## Findings that determine the reuse boundary

Source URLs below use only this immutable revision prefix:
https://git.launchpad.net/ubuntu/+source/dpkg/plain/
with query ?id=ba5fe18eb68084206981fb7b3a7c281eb2d38372.
The retained source manifests list each complete URL; the audit record binds
those manifests and each retained file identity.

### 1. Choose a stream interface, not the information/list/extraction commands

`man/dpkg-deb.pod` documents `--fsys-tarfile` and `--ctrl-tarfile` as sequential
archive-to-tar-stdout interfaces. `src/deb/extract.c` routes these to extracthalf
with DPKG_TAR_PASSTHROUGH. These are the candidates for a later adapter.
The numeric enum definition in src/deb/dpkg-deb.h is not in this corpus; verify
that definition before asserting that the tar-execution branch is unreachable.

`src/deb/info.c::info_prepare` creates a temporary directory and invokes tar
extraction. `--info`, `--field` and `--show` therefore are not zero-write inspection
alternatives. `--contents` invokes tar's human-readable verbose listing. Do not
parse that listing as a canonical machine member manifest. Exclude build,
extract, raw-extract, control, information and contents commands from the adapter.

### 2. Streaming still owns multiple native processes

`src/deb/extract.c::extracthalf` forks a compressed-member copy child and a
decompression child, and conditionally forks a tar child. `lib/dpkg/subproc.c`
uses fork and blocking waitpid; it is not Zelo's cancellation/ownership mechanism.
The adapter must own and settle the whole sandbox process tree, not only the
parent PID or a remembered descriptor. External compressor fallbacks can add
processes when native libraries were disabled at build time.

### 3. Resource limits must come from the sandbox

`lib/dpkg/compress.c::filter_unxz_init` uses UINT64_MAX for the decoder stopping
memory limit (and for the non-threaded decoder limit). The adaptive threading
memory setting is not a hard bound on total decoder memory. Thread selection
can depend on host CPU availability. Fixed --threads-max=1 is a useful input,
not proof of memory, process, CPU or decompressed-output limits.

The ar-information member allocation in `extracthalf` depends on the declared
member length. File-copy length bounds cover compressed bytes, not arbitrary
native allocations or expanded tar output. All native parsing must begin only
after aggregate memory/PID/CPU/wall-clock controls and bounded output handling.

### 4. Exit zero, pipe EOF and compressed-stream completeness are distinct

`extracthalf` reaps the decompressor with SUBPROC_NOPIPE, and `subproc_check`
treats SIGPIPE as success under that flag. Closing a capped output consumer can
therefore coexist with a successful parent status. Zelo must retain an explicit
saturation/cancellation refusal that a later exit code cannot overwrite.

The inspected zstd wrapper ends its stream on zero available input. Its wrapper
exit does not independently prove every framing/checksum/trailing-byte property.
This is a conservative scope limitation from source, not a newly demonstrated
vulnerability. No malformed archive or compressed stream was executed here.
A later manifest requires its own precise completeness contract and controls.

### 5. Environment, helpers and legacy formats require explicit exclusion

`src/deb/main.c` reads DPKG_DEB_THREADS_MAX and compressor type/level environment
variables before dispatch. Its program/locale/options initialization helpers
are not fully covered by this corpus. Use a fixed clean environment; retain no
ambient loader, language, compressor, temporary-directory or debug settings.

`lib/dpkg/ar.c` accepts '-' as stdin but otherwise reopens a path with plain
O_RDONLY. A verified inherited input descriptor with literal '-' is preferable
to exposing caller-selected host paths. This is a design preference, not a new
execution permission or a proved immutable-file boundary.

`extracthalf` also accepts legacy package formats. The legacy control path can
invoke a shell to move files. --deb-format=2.0 is a build option, not a reliable
input-format restriction. Require a separately designed fixed modern-format
precondition and an executable/filesystem policy that cannot run shell helpers.
Do not implement a new general ar/tar/compression parser for this precondition.

`compress.c` has external gzip/xz/zstd/bzip2 command branches. `debian/rules`
requests the corresponding native libraries, but build intent is not evidence
of the installed binary's feature flags or dependency closure. Pin those before
native execution; do not infer that no fallback can run from this Makefile alone.

### 6. Preserve the notices; prefer a narrow executable boundary

The retained headers and debian/copyright identify GPL-2.0-or-later for the
reviewed dpkg code; COPYING is retained. No third-party implementation has been
copied into Zelo. `configure.ac` explicitly describes the private libdpkg ABI as
unstable and disables shared libraries by default. A private-library binding is
not justified by this research. A later redistribution/build decision must retain
the applicable notices and source obligations for the actual selected artifacts.

## Verdict and next gate

ACCEPT the static source record and findings within these stated limits. Do not
claim full tool review, complete binary provenance, ABI compatibility or runtime
qualification. Remaining helpers/enum/build identities and the downstream tar
reader need a separate bounded manifest, not recursive retrieval from this one.

The next step is a bounded stream-reader/secondary-parser design with explicit
source/runtime closure, fixed inputs, resource and ownership evidence, then
separate offline implementation and source/launcher review. This record grants
no archive parsing, decompression, extraction, installation, native tool execution,
image decoding, enabled flags, Core binding or UI work. Old proof quotas remain.

## Retained research limitations

Public identity research used 11/12 logical operations including five failed
search/gateway/container attempts. Two successful tag pages were read using a
single bounded read rather than the proposed 64KiB loop; the actual pages were
30,432 and 30,422 bytes. This deviation is recorded, not presented as a passed
execution qualification. Later commit/source retrieval used bounded chunk loops.
HTTP transport and official source hosting remain trusted research baselines;
no hard wall-clock/network allocation guarantee or independent authenticity is
inferred. Source bodies were retained as inert text, never built or evaluated.
