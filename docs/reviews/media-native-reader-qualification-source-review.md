# Synthetic native-reader qualification source review — 4 October 2026

Direct ChatGPT review; no independent Codex/model execution is claimed.

## Frozen implementation identities

- `qualification/media_reader_container.py`: `8baec21ebe9c1a0711df86a3627deb407959baffe2665576f94429d5b80b1ce7`
- `qualification/media_reader_process.py`: `791e39d43aec4cec8b5954f195685842a8619a3288eaf9e769bcc575d0e51566`
- `qualification/media_reader_setup.py`: `a061efd2c9a76c36ec2a6748a4532dde09226bbbd6a1b6546970fd65451a3f00`
- `qualification/media_reader_cgroup.py`: `4e0b6cd1bf21c06a20eb5c923dc0bd8d9c24bfee4d425b497e6b46e32f223b0e`
- `scripts/qualify_media_native_reader.py`: `bb11a932a4d787edfef46ee0b1601806cd68b466298cf0ed398c6f018f5125e1`
- `tests/test_media_native_reader_operator.py`: `e0a13de6240a5fdcf68dee481647346d369f5858270b9c131780ab5526ff6864`
- `tests/test_media_reader_setup.py`: `20d80142efac55050702b9f990c93dfc6c87473e88f683bf6931af111d65a9a5`
- `tests/test_media_reader_cgroup.py`: `a499dc7a00376b506512bf2c416ef14269e44e7885f2ec03b19c988501d65921`
- `tests/test_media_reader_container.py`: `2d0cee9fe0027d1d6d30daa827d9641420724af3c0cd84cc505adeffb6eeb39b`
- `tests/test_media_reader_process.py`: `6f2ae83aa8c85867c5ef52666a78e71cc460d0c0d5acc7629098c0bf39323865`

## Prerequisite state

PR #92 merged at `f05abe11cd6b659a8339132028ca710e16393c48` with its exact reviewed
tree. Matching CI `37207303870` passed 4,505 tests with two skips. PR #91's
accepted ELF declaration proof remains version attempt 3/4; this branch does not
spend the final ELF attempt.

No Docker/native operation has run from this branch during source review. The new
native-reader proof namespace is independent and begins at 0/2. Acquired package
archives are not eligible for this qualification.

## Image setup and ownership

The build context contains only the eight already-retained bootstrap binary copies
and a deterministic `FROM scratch` Dockerfile. Every source copy is hash-checked
before and after copy. Docker build is fixed to the local Unix socket, network none
and pull false. The build image receives both the unpredictable 64-hex admission
lease label and the fixed bundle label before any container create.

This closes the lost-build-acknowledgement cleanup problem: cleanup discovers at
most one image and one container by the exact lease label, verifies the label
again before removal, and refuses ambiguity. It never selects resources by loose
name matching or deletes unrelated images/containers.

Before runtime, a stopped setup container is exported through the already-reviewed
bounded Browser export helper. The existing verifier hashes every expected image
file and refuses unexpected executable payloads, links or inventory differences.
The setup container is removed and the exact image is re-inspected before start.

## Runtime boundary and actual-resource evidence

The command remains fixed loader + fixed library path + `dpkg-deb
--threads-max=1 --fsys-tarfile -`; stdin is the only package input. Environment,
user, seccomp, network, root filesystem, capabilities, PID/CPU/memory/nofile and
namespace policy are exact.

After `docker start --attach --interactive` has started the container but before
stdin is written, the owner re-inspects the running container and invokes the
runtime probe. The probe reads only the host PID's cgroup-v2 membership and the
five exact cgroup files: `memory.max`, `memory.swap.max`, `pids.max`,
`pids.current`, and `cpu.max`. It requires 512 MiB memory, zero additional
swap, PID max 8 with a live count in-range, and CPU 50000/100000. Proc membership
accepts root or the configured non-root UID 65534; cgroup control files remain
root-owned. No recursive cgroup discovery is performed.

## Synthetic matrix and terminal controls

Exactly four fixed hand-built Debian fixtures are eligible: positive modern
format, bad version, wrong member order and truncation. The positive fixture must
return the exact previously-qualified synthetic USTAR byte stream. Negative cases
must return nonzero; stderr is bounded but never promoted as authority.

Four additional terminal controls reuse the same fixed positive bytes: stdout
saturation, timeout, cancellation and repeated cancellation. Output saturation
must raise exactly `process_output_limit`; unrelated exceptions are not credited
as a passing control. Timeout and cancellation remain permanent refusals even if
the underlying reader could later exit zero. Every case requires complete exact
container cleanup.

## Persistence and release

The new proof scope retains at most two attempts. Before another attempt, every
old attempt must be exact private 0700 and contain only private 0600
`policy.json`/ `cases.json`, each <=2 MiB and <=4 MiB aggregate. Malformed
old evidence is never repaired.

Temporary build context, Docker config, image export and iidfile are removed only
after exact Docker resource cleanup is confirmed. The pending proof records
`support_release_confirmed=false`; one existing support-release helper runs
after all Docker/local cleanup. Only then may a separate in-memory receipt claim
release confirmation. The known lease-marker close-failure caveat remains; this
review does not claim universal crash fencing.

## Validation and disposition

Current offline/fake validation:
- 173 focused native-reader operator/setup/cgroup/container/process tests passed.
- 2,752 combined Media/context tests passed.
- Ruff lint, Ruff format and diff whitespace checks passed.
- Context status remains within its 2,200-character budget.
- No application imports or runtime enabled flags were added.

No blocking source finding remains in the reviewed synthetic scope.

**Conditional clearance:** after this exact implementation is committed, its source
hashes and tree remain unchanged, and its matching full CI succeeds, clear at most
ONE explicit `--run` synthetic native-reader attempt. Any actual refusal consumes
that attempt and stops; no retry.

This clearance does not permit an acquired package, package installation, arbitrary
Docker operation, host PATH/helper fallback, Pillow/image decoding, Core binding,
enabled flags, UI work, system configuration changes, or claims of independent
binary provenance/full ABI/runtime authority.
