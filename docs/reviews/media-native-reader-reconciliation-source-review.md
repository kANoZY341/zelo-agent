# Media native-reader reconciliation source review — 5 October 2026

Direct ChatGPT review; no independent Codex/model execution is claimed.

## Frozen historical evidence

The recovery source is bound to the refused PR #93 attempt at head
`c6127669cda7923c851febe1b4f8530402f58ef8`, tree
`d6eaa8a1fa029cd049384f66d27de5218ba7c8cd`.

Tracked inert fixture copies are byte-identical to the retained runtime evidence:

- attempt-1 policy SHA256
  `a566a81238d144163e82ce91d728067c8afe36011fea404594f03f200ac868c3`;
- attempt-1 refusal SHA256
  `027e7165fc21001eafb19cdb0a9c89bb0315012931c0a862ad742e5883655378`;
- fenced marker SHA256
  `f88573e617e66156815491696f8a51fed078e07df85658800cc1cb259bb34472`;
- failed lease
  `9dd76284f54fe91361c4646fafc97a78c98f5179437345d54f9c815b8ea8bd0f`.

Attempt `native-reader-proof-uvjet91z` remains private and unchanged. Its retained
Docker config directory is empty. The admission marker is still 169 bytes and the
lock file is currently empty/unheld. Read-only exact-lease Docker discovery
returns zero containers and zero images. None of those later observations converts
the historical refusal into success.

## Reconciliation source identities

- `qualification/media_reader_reconcile.py`:
  `3d0809041a3a4a01859ba1e95e72ce73dcc59c62c7454a6239b6f7e8b2eaa230`
- `scripts/reconcile_media_native_reader.py`:
  `57c99d32a629832749d28d3af2c311ab7437a3044322cdebfc8b79c3b09db10d`
- hardened `scripts/qualify_media_native_reader.py`:
  `4cd054bfbe5fe5317093f183c9dfb400a47497961a9e40408be299fbb1f7ccef`
- `tests/test_media_reader_reconcile.py`:
  `1e9064a098a49a4af7e2398663126685597b7f3e6c3a54111f18ff5f4543985a`
- `tests/test_media_native_reader_reconciliation.py`:
  `3c8429b1be9a5874e3d5dc0e1d15153807f1daf213008236973e6bdef918f698`
- `tests/test_media_native_reader_operator.py`:
  `815bc6cd4e0290840ceedc9742e61b4bbab0a12b6ede1a684c22bfc71ca3ee54`

## Reconciliation ordering

The reconciler is intentionally narrower than a cleanup tool. It does not remove
containers/images and does not rewrite failed evidence.

It opens the existing admission directory nofollow, takes the existing lock inode
with an exclusive nonblocking flock, and authenticates the exact marker before
performing any Docker observation. It then authenticates the exact retained policy
and refusal bytes and requires the retained attempt inventory to be exactly
`policy.json`, `cases.json`, and an empty private `docker-config`.

A fresh private Docker config is used for read-only discovery. Reconciliation
requires zero exact-lease containers/images, then a **global two-second Docker
event-silence window with no label filter**, then repeated zero exact-lease
discovery. The failed policy/proof are re-read and the marker is re-read
byte-for-byte after the quiet window.

Only after those checks does the reconciler create a new one-use private evidence
directory. Pending evidence is written with exclusive creation, file fsync and
parent-directory fsync. A regression test observes that this durable pending
write occurs **before** `ftruncate(marker, 0)`. Marker truncation and marker/admission
directory fsync happen while the exclusive lock is still held. The lock is released
before the separate in-memory reconciliation receipt is returned.

This is conservative recovery, not historical cleanup proof. If an error occurs
after pending evidence creation or after marker truncation, the operation may be
left without a successful receipt and must return to design. Existing reconciliation
evidence prevents automatic retry.

## Docker quiet-window limitation

The global event window materially reduces the shared-daemon confounder observed
during attempt 1. It does not establish exclusive Docker ownership after the
window and is not claimed as a daemon lock. The later native operator must still
own, inspect, settle and remove every exact labelled resource.

The concurrent Aegis build is recorded only as a confounder. The refusal record
does not prove it caused the attempt-1 failure.

## Final-attempt hardening review

The operator now checks existing attempt count before creating another attempt.
The historical attempt-1 directory is accepted only by exact known hashes and its
exact empty retained Docker-config. With one historical attempt present, only one
new directory can be created; a third is refused independently of directory
enumeration order.

Before admission, the operator uses a fresh private Docker config to require a
Linux/amd64 Docker server, a valid available `runc` component, and the same
global two-second daemon event-silence window. The runtime itself is separately
fixed to `runc` by the container contract; this review does **not** claim Docker's
ambient default runtime is runc. Any readiness failure or Docker event occurs
before a lease or second proof attempt is created.

After admission, exact container and image lease namespaces are checked separately
before image build. Existing runtime/cgroup/result/cleanup gates remain unchanged.

This hardening is reviewed offline only. It is **not clearance for attempt 2/2**.

## Validation

Current recovery worktree validation:

- 57 reconciliation/operator recovery checks passed;
- 211 focused native-reader/reconciliation checks passed;
- 2,790 combined Media/context checks passed;
- Ruff lint passed;
- Ruff format check passed;
- git diff whitespace check passed;
- machine-consumed active status is 1,925 characters, below its 2,200-character
  budget.

No reconciliation run, marker mutation, image build, container creation, dpkg-deb
execution, package-member read, acquired archive read or Pillow decode occurred
during this source review.

## Disposition

**CLEAR the reconciliation implementation for normal commit/PR/full-CI review.**
After this exact recovery source is committed and matching full CI succeeds, clear
at most **one reconciliation run**. A refusal is retained and stops; do not retry.

This review does **not** clear the final native attempt. After a successful
reconciliation and independent retained-evidence audit, Media must integrate the
**then-current main** while preserving attempt-1 evidence, rerun focused/full CI,
freeze a new exact source/tree and receive a fresh direct source review. Only then
may the sole remaining native-reader attempt **2/2** be considered.

No acquired package, image decoder, Core binding, enabled flag, UI work, proof
reset, ABI/runtime authority or later milestone completion is cleared.
