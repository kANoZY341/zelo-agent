# Third-party provenance — persistent agent computers

This integration intentionally reuses the computer infrastructure pattern from
`CopilotKit/OpenDots` rather than rebuilding it from zero.

Imported/adapted source:

- `CopilotKit/OpenDots` commit
  `c2569bb6a13a22e565cf3eb791c62267d06babb1`;
- `compose.computers.yml`;
- `deployment/computers/supervisor.Dockerfile`;
- `deployment/computers/harden-supervisor.mjs`.

The runtime source is not vendored wholesale. Docker builds the exact pinned
`CopilotKit/OpenBot` commit
`b6932d31a8d6e7896c15139dfc27a6c6911deb27`, matching the revision reviewed by
OpenDots for this integration shape.

Both projects are MIT licensed. The copied license notices are retained beside
this integration.

Zelo-specific changes are deliberately narrow:

- the container namespace defaults to `zelo`;
- image/service names are Zelo-specific;
- per-agent child credentials use
  `HMAC-SHA256(ZELO_COMPUTER_TOKEN, "zelo-computer:" + agent_instance_id)`;
- the supervisor remains loopback-only for the first host-run integration;
- Zelo Core remains authoritative for agent identity, permissions, tasks,
  approvals, budgets and audit;
- this import does not itself grant browser, file or shell execution authority.

Upgrading either upstream commit requires a fresh license/security/API review and
must fail closed if the patched OpenBot environment contract changes.
