# R2 local identity design review — direct ChatGPT, 4 October 2026

Verdict: CLEAR only the three bounded local identity observations specified in
runtime/media-package-member-identity-proposal-r2.md. This is a direct review of
the existing proposal, not a completed Codex review or native-tool qualification.
The interrupted Codex log is not counted as a verdict.

PR86 actual archive evidence is accepted and merged at
b1638a0e7f074c8ca8a2bc47326e315d510a0820; the merged tree was verified identical
to reviewed tree 53f8fcd719b51058c99081557dc0bb37453c80e7. The prerequisite gate
is satisfied. No new acquisition, origin/index/ELF attempt or reset is cleared.

The three prior findings are resolved for this narrowed research scope:

1. Exact-version evidence: the literal /var/lib/dpkg/status path, 8-MiB bound,
   single dpkg stanza, exact identity fields and 64-KiB retained stanza ceiling
   replace unspecified discovery. The complete distribution Version is preserved.
   An absent Source field/version remains absent; no source correspondence is
   inferred. Installed status metadata is a conditional host baseline only.
2. Public retrieval bounds: the R2 phase permits zero public-source requests.
   Exact-version official source retrieval and downstream patch correspondence
   require a separate reviewed finite manifest; no recursive source chasing.
3. Resolved-path authority: exactly three literal paths, no symlink/fallback,
   explicit root ownership, modes, regular-file/nlink checks and stable held-FD
   metadata apply to binary, copyright and status. Trusted ancestors and same-UID
   operator remain explicit assumptions, not immutability or independent origin.

The observation must obey the existing one-attempt exclusive 0700 directory;
10,747,904-byte input ceiling, 64-KiB reads, AS512MiB/CPU10s/FD32/FSIZE8MiB/CORE0,
60-second cooperative checks, and three exclusive 0600 retained files totaling
at most 352KiB. Reserve maxima before writes; close each owned descriptor once,
with no close retry. Any error leaves partial output and stops without widening.
A bounded one-off trusted-stdlib observation script is the means of observation,
not permission to implement a reusable package member parser or materializer.

Execution-stage obligations: reject duplicate/continued identity fields and
ambiguous target stanza framing; hash the full held input before accepting the
record; retain only dpkg's selected stanza rather than installed package lists;
record any observation failure as a generic bounded stage with no success claim.
No installed-file reads or tool invocation were performed during this review.

This clears no dpkg/dpkg-deb invocation (including --version), archive/member
reading, decompression, extraction, import, installation, source download, platform
or ABI qualification, image decoding, Core binding, enabled flags or UI work.
All such later gates remain separate.
