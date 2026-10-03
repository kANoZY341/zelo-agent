# pyelftools metadata-tool source candidate

pyelftools0.33, https://github.com/eliben/pyelftools, upstream tag v0.33 at
52ce435186022bd6b32012a998931fe86c985ec8. Its 52 bundled library Python files match
the exact wheel source byte-for-byte via recorded Git blob identity and SHA-256.
The unused readelf.py CLI is excluded from any planned tool import tree.

pyelftools is public domain under its preserved Unlicense notice. Bundled Construct
2.06 carries the separately preserved MIT notice (Tomer Filiba2009 and Corbin Simpson
2010-2011). Both release wheel and sdist omit Construct's referenced LICENSE; the
preserved notice comes from the exact upstream release commit, with matched blob
identity and SHA256 f8d0c347a0dcc6ebe1671640dfae8d2411b6ded892e06a6764f8208b218b2af4.

No executable parser code is vendored here. Exact public index and upstream HTTPS
transport are provenance assumptions, not independent release-signature verification,
a complete security audit or execution approval. The fixed fixture records candidate
archive/member identities. Acquisition/source inspection has not imported or executed
pyelftools, a target ELF, ctypes, libffi or libseccomp.
