# Additional reader helpers — static review, 4 October 2026

## Scope and records

Direct continuing-ChatGPT review; no separate Codex/model call. PR87 is merged
at 63d2aaea451c4639f117efbc9bd1c6e65ecacb44 with matching pre-merge CI: 3,740
passed and two skipped. Reviewed tree c14328d55701812d5c2ca0fb41dc4474c5b58d3c
was verified equal to the merge tree before continuing.

A new, bounded scope addressed only the helpers named as gaps in the prior
15-file source review. One fixed official directory listing resolved the names;
a separate closed manifest then retrieved eight NEW files at dpkg commit
ba5fe18eb68084206981fb7b3a7c281eb2d38372. No prior source URL was repeated and
no acquisition/identity/ELF/proof counter was reset. All eight requests succeeded,
retaining 31,201 bytes. The directory response was 91,287 bytes, below 256KiB.

The exact new paths are src/deb/dpkg-deb.h and lib/dpkg/{program.c,options.c,
options-parsers.c,command.c,meminfo.c,error.c,Makefile.am}. All source URLs use
https://git.launchpad.net/ubuntu/+source/dpkg/plain/<path>?id=ba5fe18eb68084206981fb7b3a7c281eb2d38372.
The source headers identify GPL-2.0-or-later. The earlier source notices remain
retained; no borrowed implementation or source corpus enters product code.

Separate retained-file audit: exact inventory and bounded stable held descriptors,
size/SHA256/URL/path checks against the eight observed pins. It did not reopen
installed files, parse archives, invoke dpkg or use the network. Audit SHA256:
5af60721e80e194a79747f1edc587d70df024c21c63580ca8f312f427b22d5ec.
Raw research/audit records remain under ignored runtime paths. This is conditional
source-host evidence, not independently signed package or binary provenance.

## Closed questions and remaining native constraints

- src/deb/dpkg-deb.h defines DPKG_TAR_PASSTHROUGH as zero. Combined with the
  previously reviewed extracthalf code, the fixed stdout action avoids its
  conditional external tar branch. It still forks copy/decompression children;
  this does not eliminate compressor fallbacks or legacy input semantics.
- options.c separates dpkg_options_parse from dpkg_options_load. The previously
  reviewed dpkg-deb main calls parse, which does not invoke the configuration
  loader. If a loader is ever invoked it can enumerate a config directory and
  use HOME; do not expose those paths or infer safety for other dpkg commands.
- program.c sets report/debug/error-context state and resets umask to 022. An
  outer private umask is therefore NOT sufficient for native tool-created files;
  use an immutable/no-write filesystem boundary. Debug/report/error/locale
  descendants are not completely reviewed by this eight-file scope.
- command.c executes via execvp/execlp and can search PATH or the default shell.
  The future sandbox must not contain executable shell/helper fallbacks. Merely
  setting a clean PATH is not proof that those execution paths are unreachable.
- meminfo.c reads /proc/meminfo on Linux for an estimate, not a hard sandbox memory
  limit. Its 4096-byte buffer is followed by buf[bytes] = NUL without a visible
  extra byte in this source. That is a source-level boundary concern if a full
  buffer is returned, not a demonstrated exploit or a claim about the installed
  binary. Do not expose a host proc filesystem or rely on this helper as a cap.
- Makefile.am names compression/native libraries but does not prove which build
  features or loaded libraries are present in the observed installed executable.
  Full immutable executable/library/locale/debug/runtime closure is still open.

## Downstream reader research

Official Python3.12 tarfile documentation was read at
https://docs.python.org/3.12/library/tarfile.html. It documents uncompressed r|
streaming, replaceable TarInfo, and extractfile returning a file object for regular
files OR links. extract/extractall are outside our intended interface. Format is
a writing option and does not restrict formats accepted on read; a zero block
can terminate normal reading. These facts prevent treating defaults as a strict
member format/termination policy or using extractfile on links.

This is API research only. The page identified itself as Python3.12.15 docs,
not the exact Ubuntu3.12.3 build. Two source-view attempts failed (raw GitHub
internal error and GitHub page DisabledError); no successful code inspection is
claimed. Three web research operations were consumed; no web-tool byte/memory
bounds are asserted. CPython tarfile is a candidate, NOT a qualified selection.
Its exact distribution-patched source, license/runtime closure, extension payload
pre-allocation, sparse handling, duplicate PAX keys and complete stream termination
still require bounded inspection before an adapter imports or parses archives.

## Disposition

Accept the bounded helper observations and the source-derived constraints. Do
NOT label helper/runtime closure complete or authorize a native launcher, archive
parser, extraction, materializer or image decoder. No native operation occurred.

Proceed with the separately reviewed pure metadata-validation contract in
../v0.15-media-member-contract.md. That component accepts synthetic plain JSON
and caller-owned identity/outcome values; it can establish only metadata
consistency, never file authenticity or runtime authority. Its completion does
not waive any parser/source/sandbox gate. Original proofs and quotas, UI #61,
user data and runtime flags remain unchanged.
