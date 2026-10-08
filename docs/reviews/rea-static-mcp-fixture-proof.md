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

## Actual Linux guest enforcement witness

A fixed local Node diagnostic ran with the same security flags and read-only
REA/target mounts as the MCP proof. Its observations on the Zelo Docker host:

- effective UID/GID: 1000/1000 (non-root)
- NoNewPrivs: 1; CapEff: 0000000000000000
- memory.max: 402653184 bytes (384 MiB)
- memory.swap.max: 0
- pids.max: 64; cpu.max: 100000 100000 (one CPU quota)
- Docker socket and host secrets path: absent
- writes to /etc, /target and /rea: EROFS
- write to isolated /tmp: allowed
- TCP connect to 1.1.1.1:443: ENETUNREACH

These are observations for the fixed diagnostic run, not proof against a
container-escape vulnerability or an arbitrary hostile artifact.

The dependency lockfile was also checked with npm audit on 8 October 2026:
zero low/moderate/high/critical advisories reported for the installed
production dependency closure. This is one timestamped advisory database
check, not proof that the dependencies contain no vulnerabilities.

## Independent review corrections

Read-only Codex Astra Medium review of the stacked REA bridge identified two
P2 source issues: fixture creation could follow a symlinked parent before the
reader's later refusal; and an uncertain Docker cleanup removed the CID file
before an operator could reconcile a potentially surviving container.

Both were corrected before qualification rerun. Fixture creation now uses
directory-descriptor-relative NOFOLLOW opens/mkdirs and rejects symlinked
ancestors before any writes. A proven cleanup erases the private staging
directory; on unproven cleanup the exact CID and private owned stage are
retained under the ignored qualification workspace for operator recovery,
with no success receipt. New offline tests exercise symlinked-parent refusal,
exact fixture content, successful cleanup and retained-CID failure. The
corrected fixed-fixture native Docker/MCP proof passed and left zero
owned staging directories in the success case.

## Third independent review correction — missing Docker CID

A second independent Codex Astra Medium review identified one P2 cleanup
edge case: a Docker start that has no CID file could previously be considered
clean without querying the daemon. The corrected bridge now commits a randomly
generated 128-bit ownership token into the private staging directory before
launch and applies both an exact Docker name and unique
zelo.rea.qualification.run ownership label. Cleanup consults the daemon's
full, non-truncated container IDs filtered by that label, and only removes
an ID verified by that same owner label. It rejects mismatched ID/label
records, malformed or multiple daemon results, and Docker daemon errors.
The bridge never reports success with a missing CID, even if daemon
enumeration is empty; the private owner record remains available for
reconciliation. Three missing/mismatched-CID regression tests and additional
invalid-owner/daemon-failure tests exercise these paths.

The corrected real native fixed-fixture MCP call passed again, with no
qualification stage remaining on the successful path. This result remains
restricted to the fixed, harmless two-file fixture.

## Fourth independent review correction — interruption-proof retention

A third independent Astra Medium review found an interrupt path: a Ctrl-C
during Docker cleanup raised KeyboardInterrupt, bypassing the narrow
ReaStaticRefusal recovery handler and deleting owner.json/CID before
cleanup was proven. The code now defaults to retaining stage ownership
before Docker Popen, and releases that retention only after the exact
owned cleanup completes successfully. The new KeyboardInterrupt
regression verifies private stage, owner token and CID survive interruption.
