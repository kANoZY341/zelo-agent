# Restricted USTAR adapter source review — 4 October 2026

Direct continuing-ChatGPT review, not an independent Codex execution.
Module SHA256: 7a1afd0bbd6b7616ce9ee1ff286d8004d47926fddd36524fd690600bed897be3.
Test SHA256: 717ee922c6e9a73741fd44dfbccd546a39d47125cf4ceb07aeca93dfe640a8c6.

## Source observation and reuse

The first tarfile observation required0644 and refused. One metadata-only
lstat identified root-owned regular0755/nlink1/exact113907. This is a precondition
mismatch, not evidence of compromise. An explicit revised review approved only
a final inert read of that fixed non-group/world-writable identity. Across R1/R2,
source-content attempts are2/2 consumed, not reset; original refusal unchanged.
Neither source observation imported or executed the target.

The retained source SHA256 is
664f862558196d80a46663a81bc16cd51061ccec6c1fdda5950a106e97e12512.
A separate stable held-FD audit of only the two retained files verified inventory,
size and hashes; four altered-byte controls were rejected. This is conditional
installed-source evidence, not independent package/binary/bytecode correspondence.
The source's full notice is retained with the inert reference. No third-party
implementation is vendored. Official Python3.12 tarfile API documentation was
viewed once as API evidence, not a replacement for installed-version source.

Read source imports/constants/TarInfo initialization, fixed-header conversion,
checksum/string/numeric helpers, and automatic GNU/PAX/sparse processing paths.
The latter can read extended payloads before caller policy checks. The adapter
therefore uses only TarInfo.frombuf, nts and calc_chksums; it never invokes
TarFile, extraction, automatic extension handling or compression. Its fixed
USTAR acceptance policy is intentionally narrower than the upstream library.

## Implementation review

Preflight checks512-byte width, POSIX magic/version, four allowed type bytes,
string/NUL and octal lexical bounds and reserved tail before decoder dispatch.
The library performs actual checksum/numeric/string decoding. A follow-up guard
requires the unsigned checksum; legacy signed forms have a negative test.
Original directory-name spelling is checked through the contract rather than
letting the library's slash normalization silently admit ambiguous names.

Caller binding is checked before stream reads. Metadata/name/type/UID/GID/mode
and size are validated before payload reads; canonical duplicates and both
orders of non-directory ancestor conflicts refuse. Links remain inert targets.
Byte/call/member/output limits and cooperative cancellation/time checks bound
work. File hashing uses<=64KiB chunks. Payload extent is reserved before reads;
padding must be zero. Two terminal zero blocks, aligned zero remainder and EOF
are required. Empty, truncated, extra-record and unsupported-extension input
never yields an accepted result. There is no extraction or filesystem writer.

JSON accounting uses a worst-case prefix and fixed-size digest placeholder
before payload work, then checks actual serialization. Immutable output contains
only parser observations/assertions with authority false. It does not claim
original .deb authentication, complete compressed-stream consumption, process
settlement, sandboxing or cleanup success. Those remain trusted-owner obligations.

## Verification

201 synthetic tests passed, then2,308 combined Media/context tests passed.
Nine in-memory guard-removal controls each show exact baseline refusal and a
mutant admitting the bad synthetic stream: unsigned checksum, payload padding,
second terminator, trailing record, cancellation, duplicate, ancestor, JSON budget
and member budget. Actual module bytes stayed unchanged. Source-hash binding is
retained in media-ustar-causal-gates.json. Tests include actual stdlib fixed-header
calls, short-read chunk boundaries, file-operation/extraction tripwires, hostile
names, exact budget edges, wrong callback/decoder types and interrupt behavior.

The first lint invocation reported formatting issues before tests started;
formatting corrected them. The first actual scanner run passed183 tests; later
checks expanded to201. No failed validation is being relabeled as a pass.

## Verdict and remaining gates

No blocking findings within this restricted, qualification-only synthetic scope.
Clear normal draft-PR validation and exact-head full CI, not live package use.
Future real inputs need a pinned/qualified parser runtime plus externally owned
process/resource/cancellation/cleanup enforcement. USTAR-only compatibility is
not evidence that the eight acquired packages will parse; GNU/PAX support remains
explicitly absent. No original native proof attempt/quota was consumed or reset.
No model call, native package tool, installation, application binding, UI change
or image-decoder activation occurred. CI must match the final commit and reviewed
tree before merge; PR88's successful run is not this increment's CI result.
