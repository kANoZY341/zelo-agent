# Moby seccomp profile provenance

Unmodified upstream default.json and LICENSE from moby/profiles commit
2ceae35d351c156cb5a8efc0fdc4a08cf94569d8, Apache License 2.0.
Source: https://github.com/moby/profiles/tree/2ceae35d351c156cb5a8efc0fdc4a08cf94569d8
Profile SHA-256: 6416b47770785a41ac59073cdc77d9fe98517df2799dc83ef207e622de3053f6
License SHA-256: cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30

Zelo's qualification-only adapter appends narrowly scoped native-browser rules;
it does not edit these upstream bytes. This is not Docker's automatically chosen
profile or a production security endorsement. See the qualification design.
