# REA static MCP manual qualification witness — 8 October 2026

This result concerns one harmless, Zelo-owned JavaScript fixture. It does not
authorize inspection of other software or production reverse engineering.

## Identity

- Distribution: npm rea-agents 5.0.0, MIT, acquired with install hooks disabled.
- Published SRI: sha512-H5z7irOKw0L60TksrW34ELJ9JZkRlgRVUaLX6N+yqHP45Vg+BiM/TMZHhLxJQqYNGeHBXnK2TvQepO2X7WzW+w==
- Tarball SHA-256: 1879bcd2e5c83644d437363afda12dc0c45ec0491e3836e507d4ff4d05f4624b
- Package-lock SHA-256: 6209f46f3f9e69be8e2f418e019957cbb039a5fb2e4110c7734ae23d4762b342
- Installed module-tree SHA-256: bee491ce5e4ff4196e1e96f06584dafb77342e4987eb45888f578602894ee1f0
- Image ID: sha256:43ac6c60b8f89723f746e8a92ce91abd5017e627ce1ddfe4238355d3a30b772c
- Server: rea 5.0.0, MCP protocol 2025-11-25, 133 advertised tools.
- Allowed tool actually called: analyze_javascript_application.

## Observed evidence

The locked-down Docker guest analyzed a two-file, 130-byte static JavaScript
tree, using no network or writable target mount. REA returned Evidence
ev_3a1e14b9e66b641ff1701d895f69cd2849e5d849be0de4d574cc21a4c1e8ca5e.
The source snapshot digest was
a68ef06cab5e6a226db2bb45494bf82a6a2e6934eb785cb540cf5d6fd0b4bbdf.
REA's own subject digest was
7ce41765b373fd033a16eda4a5f4ca2d7c26b0d34f237cc4e6a35037e7af3c5b.
The bounded evidence digest was
b6bafa8c913d3e9371981c8ec8f1d7ac21d8ac1669e350cfefae53db8b3e8109.
Result: one static finding, one parsed JavaScript source, no application
execution. Exact owned container removal was checked after the call.

Without explicit opt-in, the same manual script returned refusal without a
guest launch. Host-boundary assumptions remain: Docker daemon and image must
be trusted, and source-to-npm provenance is not independently attested.

## Release and authority verdict

Qualification-only proof. No new Core task, tool, Worker, live agent instance,
external credential, persistent computer, MCP registration or production
execution grant. Future user-selected targets and native/runtime provider
analysis need separately reviewed permissions, resource gates and source proof.
The unrelated four Media-reader refusals remain exhausted and preserved.
