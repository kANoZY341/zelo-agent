# Static origin protocol references

Official repository: https://github.com/gpg/gnupg

Pinned commit a43271cc08e2068acc75a1742f90740afe0479e0 (tag gnupg-2.4.4).
GitHub HTTPS and Git-blob identities checked; no independent tag signature claim.
No source code incorporated or executed. GnuPG supplies external cryptography.

| Path | Bytes | SHA256 |
|---|---:|---|
| doc/DETAILS | 75470 | f98db9bce8147db0652809e7f6aade8185c5337a20f8a914e0b7e5a2f1d7d138 |
| g10/gpgv.c | 17009 | 46acf273955617b8d5b0103c5cff688b17144dbda28b6b0bfb4b83635b7b180e |
| g10/mainproc.c | 90698 | 2ce7446ea81a838027f032c7350ccbc220d0c7862fd26a180e4b9c4252908308 |
| g10/sig-check.c | 43275 | c61d0dcc496dcc6fcb821082315bb407e2fbdf5f33f24ea0dca167c1afd0f1e0 |
| g10/plaintext.c | 20048 | 34195f1f7255e72a46813db136034e2750ad96cd9eac3e267864685b80067260 |
| common/status.c | 4527 | 0e4952f231afcd44e44fc19c207f8e8f66ce4497fef63e22c24f10a7b1dd5fdb |
| common/sexputil.c | 32941 | 16340e90a3cc9d7e1584e5c66b0f42faee7fdaed0fedafc97cdcfd57b5960024 |
| g10/armor.c | 42692 | 53e1ecdc2dbb04c093a6ff87aeb10331cb3b9413de8e85e1d8cc5a35082c6eab |
| g10/parse-packet.c | 104766 | ed99fcea2db8a92e9616ca33815d9ae2ab8b2f1136208e19c66079995a6268fa |

Canonical Chisel reference commit 567ecfe8b15846c4367ab6c9e86f4f9844a85e38;
AGPL-3.0, research only. Release definitions commit
8e43bc3ea4cd0b10c0f5b9f275409155b4d4cf8a; no vendoring permission inferred.
Public fingerprint independently selected in policy and corroborated by pinned
chisel.yaml SHA256 954a98ea87100fd0e892f2933c7f6832b8b85608e2bc55db3ee60f01de7f1ebf.
Historical selected-root attribution only; no current revocation/freshness claim.

Additional status-writer source g10/cpr.c: 16571 bytes, SHA256 362a4192537359b4d85a2154782ace1665465609c19f0b94c5979682fb58ac24.

## Static bubblewrap identity reference

Official repository: https://github.com/containers/bubblewrap

v0.9.0 commit 8e51677abd7e3338e4952370bf7d902e37d8cbb6.
GitHub HTTPS/Git-blob identities checked; no independent tag signature claim.
Read only, no code copied or executed. Downstream installed build remains the
explicit trusted operator baseline. --dev selects devpts setup; --disable-userns
creates the final namespace. Guest1000 maps to setup0, which maps to host1000.

| Path | Bytes | SHA256 |
|---|---:|---|
| bubblewrap.c | 107265 | 6a89bc9b7a794d19e2bdb1e139ab9d5bba84e807b330336f363f88f130cfeab6 |
| bwrap.xml | 27203 | 076efc46f0db00b06be6d28cde306659da39fa536a6803b7135e19f952b7c564 |
| utils.c | 18278 | 68a39cc25753addb04a21c22f41e3805e63f3106d24e67d39a24ce6727f6bd69 |
