# R2 next-stage proposal: three fixed local identity observations only

Supersedes the requested research scope in media-package-member-reuse-proposal.md.
No operation below is exercisable until PR86 actual archive evidence is accepted,
all matching gates pass, and its exact reviewed tree is merged. Current frozen
source is023eccb; no actual acquisition has yet occurred. All previous quotas stay.

## Closed input manifest

Exactly three literal paths; no discovery, PATH search, fallback or symlink:

| Literal path | Required metadata | Maximum bytes |
|---|---|---:|
| /usr/bin/dpkg-deb | regular; root UID/GID0; exact0755; nlink1 |2097152|
| /usr/share/doc/dpkg/copyright | regular; root UID/GID0; exact0644; nlink1 |262144|
| /var/lib/dpkg/status | regular; root UID/GID0; exact0644; nlink1 |8388608|

All must be positive size, resolve to the identical literal path, and be opened
O_RDONLY|O_NOFOLLOW|O_CLOEXEC|O_NONBLOCK. Metadata via held FD before/after must
match dev/ino/mode/uid/gid/nlink/size/mtime_ns/ctime_ns; hash and size the bytes
through that FD in <=64KiB reads. Each path opens once, with one finally-close
per owned descriptor and no close retry. No preliminary tool invocation.
All ancestors and same-UID host operator are trusted baseline assumptions;
observations do not make installed files immutable or independently authenticated.

Read at most10747904total bytes across these three files. For the executable,
retain only metadata/hash, never execute/import/decode/copy it into a platform.
For copyright retain the bounded raw notice plus its metadata/hash. For status,
retain only the single stanza whose exact Package field is dpkg, maximum64KiB,
plus whole-file size/hash and metadata. No general package enumeration retained.
Read status as inert bytes. Require exactly one matching LF-separated stanza;
record exact Status, Package, Version, Architecture, and optional Source fields.
Require Status install ok installed and Architecture amd64; preserve the complete
Version including distribution revision. Missing/duplicate/continued target
identity fields or ambiguous framing leave source matching unresolved and refuse
promotion. Do not infer a missing Source version or binary-to-source correspondence.
No dpkg-query, --version, shell package expansion or installed-code import.

## Bounds and retention

One research attempt only; failures retained, no automatic retry or repair.
Use a new exclusive private0700 runtime/media-package-member-identity-r2 directory;
refuse if it already exists. Before allocations, a trusted stdlib-only observation
script sets AS512MiB, CPU10s, FD32, FSIZE8MiB, CORE0. No project/target imports,
threads, subprocesses, sockets or native parsing beyond ordinary trusted stdlib
file operations/hash. Whole60s cooperative checks between reads and stages; no
hard filesystem deadline claim. Reading status may hold bounded copies; reserve
32MiB for them within512MiB process limit. This is offline local identity research,
not a qualification operator proof or reusable platform authority.

Retain at most three exclusive0600 files: identity.json<=32KiB,
dpkg-status-stanza.txt<=64KiB, copyright.reference<=256KiB; combined<=352KiB.
Charge fixed maxima before writes. Partial research output stays in place.
No binary/status-file copy, member archive reads, tar/ar/compressor or toolrun.
On any mismatch/error, stop and record only a bounded generic stage where possible;
never retry descriptors or widen the manifest. No live env/config/data/service
changes; existing qualification admission/proof scopes are not acquired or reset.

## Public source phase explicitly deferred

ZERO public-source retrieval requests are cleared here. After exact local fields
are observed, propose a separate closed manifest of official Debian/Ubuntu source
locations, immutable distribution source revision/patch identity, explicit source,
document and license paths, requests/files/per-file/aggregate/retained limits.
Count failed retrievals and forbid automatic retries or recursive dependencies.
An upstream version is insufficient for a distribution binary. Unresolved source
or helper/downstream-parser correspondence remains a recorded gap, not authority.

The original next-stage obligations (mature reader reuse, bounded native parsing,
owned process settlement, notices, immutable input copies and resource/filter
qualification before decoder) remain. This proposal grants no implementation,
archive parsing/extraction, package member provenance, ABI/platform, materializer,
Core binding, runtime authority or new actual acquisition attempt.

Requested verdict: clear or refuse only these three bounded read-only local
identity observations after PR86 actual acceptance and exact merge. Reviewer
must not perform the observations or installed-file reads during design review.
