# First local-index attempt: preserved refusal

Frozen source60f01997598691d229ed12aaf4a56f7a3e256506 received full source acceptance
and explicit one-attempt clearance. Operator exit1 refused source metadata before
complete authentication or any parser call. No success receipt was emitted.

Proof: runtime/media-package-index-proofs/index-proof-qe5u08n9/cases.json
SHA256:0030fb0a8a4e87aaa2d5f762b94b3a232f7f2a0925d3cf49fc0eb8b12fc27e3b
64bytes; attempt2files/2031bytes. Index scope1/4; other scopes unchanged.
The admission marker was observed empty after command exit; no retrospective
success or universal fencing claim follows from this observation.

Operator stat found updates size7375495 versus signed expected7375524, with
rootUID0/GID0,mode0644,nlink1. Noble and security index sizes still matched.
Ctime changed relative to prior inventory; it does not establish how the files
changed. The read-only reviewer sandbox reports unmapped host roots as65534;
its stat view does not refute the separate remote operator's UID0 observation.

Candidate-only inventory subsequently captured the same three local InRelease
paths without parsing or signature validation. Updates and security full-file
hashes changed; noble hash and all three sizes were unchanged. These candidate
hashes are unauthenticated until a separately reviewed native run succeeds.
Original origin proof remains historical evidence for its own older declarations.

Validation of source60f0199:201selector/operator tests,1800Media tests,4standalone
checks and6causal controls passed. Matching CI37172723089 succeeded. These do
not override the actual refusal. New origin cleanup/pin implementation is a new
source revision with separate focused, full-source and actual gates.
