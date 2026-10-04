# Media native-reader offline boundary — direct source review

Reviewed directly in the continuing ChatGPT session on 4 October 2026. No
independent Codex/model execution is claimed.

## Verdict

CLEAR this consolidated **offline/fake-only implementation** for normal repository
CI and merge review. It does not clear any Docker daemon call, image build/create/
start/export, dynamic-loader execution, dpkg-deb execution, compressor execution,
acquired archive/member read, Pillow decode, Core route or runtime activation.

Exact source SHA256 values:

- `qualification/media_reader_container.py`:
  `f964288fc9647177e1210dbb51b5e0b81f45e161329468ba03786046ead19104`
- `qualification/media_reader_fixture.py`:
  `f04aee2dfe8cdfdd8266986ff9c981649223ffbbdfe04439d09bb929b83132e9`
- `qualification/media_reader_image.py`:
  `eaaf10144f3e4f0d2fc574e97789e9af4fb9839b80a0b572d8808c44960a27e4`
- `qualification/media_reader_process.py`:
  `fe8e639c5cb32f663234448baeccafc3151de114b7ec264c150bcb419ab44fcf`
- `qualification/media_reader_seccomp.py`:
  `1c77745c497c971929ef1ba2c7931785a9d3b6ce0fd7d5342886bd286ad5e1b3`

## Prerequisite evidence

PR #91 merged at `09b68a5355098e15f3549faa98126203c08309eb`; its
tree `4b39b2f253108e7bbf1461b4cccaffac321d270e` exactly matched the
reviewed tree. CI `37204122789` passed 4,347 tests with two skips.

The single source-cleared 40-case ELF declaration batch passed exactly once as
version-proof attempt 3/4. Proof SHA256 is
`cc5c463a62ccd73534d88c37b2dbc9ad4b0343161066998343877e469a6601e5`.
Its independent stdlib-only audit accepted all 40 records and the eight-node
dependency declaration graph. That evidence remains non-authoritative for actual
dynamic loading, symbol resolution, ABI, independent binary provenance or runtime
authority. Version scope remains 3/4 consumed.

## Consolidated boundaries

There is one container policy rather than parallel prototypes.
`media_reader_container.py` binds the accepted bootstrap proof/lock and requires
a content-addressed inert image plus exact runtime configuration. Admission lease
identity is the actual 64 lowercase hex characters produced by
`AdmissionController`; an earlier local 32-character draft assumption was caught
and corrected before any daemon operation.

The image stays inert: it has only the bundle label and the eight fixed binaries.
`media_reader_image.py` derives a canonical bundle digest and Dockerfile from the
accepted lock. The Dockerfile is `FROM scratch`, has exactly eight COPY entries,
one bundle label, and no RUN/ADD/ENTRYPOINT/CMD/ENV/USER/WORKDIR/VOLUME/EXPOSE/
HEALTHCHECK/ONBUILD or URL. Runtime loader, argv, environment, user and seccomp are
provided only by exact create arguments.

The runtime command is fixed:
`/reader/ld.so --inhibit-cache --library-path /reader/lib /reader/dpkg-deb
--threads-max=1 --fsys-tarfile -`.
Environment is exact C locale with PATH/HOME/TMPDIR set to nonexistent paths. No
DPKG_DEB_* or compressor environment enters policy.

Container inspection requires runc, network none, read-only root, uid/gid 65534,
cap-drop ALL, no cap-add, no-new-privileges, exact vendored Moby default seccomp,
512 MiB memory with equal memory-swap, PID limit 8, half CPU, nofile32, private
IPC/cgroup namespaces, no host PID/UTS/user namespace, no init insertion, OOM kill
enabled, 1 MiB shm, no restart/logging/autoremove, and no mounts/binds/devices/
ports/DNS/groups/sysctls/cgroup-parent/storage/tmpfs.

`media_reader_seccomp.py` reads only the repository-vendored upstream Moby default
profile, enforces its recorded SHA and basic structure, and deliberately does not
use the Chromium-added profile.

## Fixed synthetic data

`media_reader_fixture.py` hand-builds exactly four bounded Debian-format inputs:
positive modern format, bad version, wrong member order and one-byte truncation.
The positive package contains `debian-binary=2.0\n`, empty `control.tar`, and
the already-qualified fixed synthetic USTAR bytes as uncompressed `data.tar`.
A separate test-only ar decoder verifies that layout without invoking dpkg or a
general parser. Negative results may retain only bounded byte counts/hashes and
require nonzero refusal; stderr text is never promoted as authority.

No acquired archive is eligible for this slice.

## Owned process model

`media_reader_process.py` is qualification-only and has no application import.
It owns one container under one admission lease and one unpredictable 64-hex
identity. Docker CLI is fixed to the local Unix socket with a private empty
DOCKER_CONFIG and no inherited HOME/DOCKER_HOST/DOCKER_CONTEXT.

The handle verifies the image before create, writes/rechecks one private exact
seccomp file, marks creation attempted before acknowledgement, re-verifies image
and container before start, streams only one of the fixed fixtures, bounds stdout
and stderr, and is one-shot. Cleanup settles output readers, attached CLI and all
owned command tasks; it discovers only the exact lease label, verifies id/name/
label before force removal, confirms absence and refuses cleanup on ambiguity.
It never builds, pulls or deletes images.

Current owner evidence uses a fake daemon only. No real Docker API/CLI process ran.

## Validation

- 158 focused offline tests passed across image, fixture, seccomp, container and
  owner modules.
- 2,691 combined Media/context tests passed with three existing warnings.
- Ruff lint/format and git diff whitespace checks passed.
- 13 independent offline control mutations refused in their intended families:
  network, writable root, capability add, runtime, memory, user namespace,
  seccomp removal, mount, argv, environment, lease width, positive-result drift
  and image-lock pin drift.
- The machine-consumed active project section stays below 2,200 characters.

The first source-generation typo in an earlier duplicate prototype and an early
fake-owner mismatch were caught before any native operation. The duplicate
untracked prototype was consolidated into the five modules above and removed; no
user data, prior proof or accepted evidence was deleted.

## Next gate

After exact-head CI and exact-tree merge of this offline slice, a separate branch
may implement the one-shot setup/operator. It must copy only the already-retained
fixed binaries into a private build context, build with network disabled and pull
false, verify the stopped exact image filesystem byte-for-byte, then seek a fresh
frozen-source review before **one** bounded synthetic native matrix.

That future matrix may use only the four fixed synthetic packages. It must prove
actual cgroup/resource/process-tree enforcement and complete labelled cleanup
before receipt. Any mismatch stops without retry. Real authenticated package
compatibility remains a later separate gate.

No proof counter is reset. Origin and old-loader remain exhausted; index 2/4,
ELF version 3/4, acquisition 1/4 and USTAR isolation 1/2 remain. UI #61 stays
parked and v0.14.0 remains unchanged.
