# Shell boundary qualification final gates

PR [#69](https://github.com/kANoZY341/zelo-agent/pull/69) merged at
`a33dc9cdf43662ce9c158ad4cba0b51bdb97735d`, from exact reviewed/tested head
`32f843a3adef9f6b89d0b05aab6bf7f1ecb50dbc`. Git tree identities match:
`4543009c86ae4e6a9a5985f4ff5d0d43475b8dc6`.

The optional qualification uses sealed verified installed Dash/prlimit/library
bytes and Bubblewrap for fourteen harmless cases. Actual nonroot identity, zero
capabilities, no-new-privileges, hard limits and isolated network state passed.
Readonly/fork denials require specific evidence; permitted-pipeline EOF and
file-size failures cannot masquerade as those restrictions. No Shell task,
worker, capability, API, Agent/Mind route or default activation was added.

Review also corrected shared native process cleanup and its OpenCode caller.
An exact direct child is observed through pidfd/WNOWAIT without early reap;
owned group termination precedes final reap. Released/lost identities refuse
signals. Post-spawn pidfd failure enters emergency direct-child WNOWAIT cleanup,
never a weaker successful-runtime path. Creation and repeated cancellation own
cleanup. Linux prerequisites and trusted host/process-visibility assumptions
remain; uncertain cleanup retains ownership.

| Gate | Exact-head result |
|---|---|
| CI run 37086937973 | 1,686 passed, 2 skipped, 11 warnings; 659.17 seconds |
| Installed-Codex non-model supplements | Both skipped checks passed in standalone exact-head clone |
| Affected focused suite | 132 passed |
| Lint/format/diff checks | Passed |
| Normal native Shell proof | 14 cases passed, 14 launchers reaped; clean exact head |
| Invalid ambient ZELO/Docker/HOME proof | 14 cases passed, 14 launchers reaped; clean exact head |
| Offline denial negative control | Removing specific denial checks failed both incidental-failure regressions |
| Native NPROC negative control | Effective-state mismatch before any fork case; one launcher reaped |
| Independent Astra Medium review | Full review plus two exact-head correction follow-ups; no remaining blockers in combined scope |

Private ignored evidence is retained under `runtime/shell-qualification/`:
`shell-proof-wx4x0ynx` and `shell-proof-iangek18`. Reports bind exact clean Git head,
policy digest and effective guest state. Source hashes are installed observations,
not independent provenance. No binary was redistributed.

Next is distinct immutable Shell identity and durable admission/resource ownership
qualification, with explicit per-execution cleanup evidence. Core binding and
broader processes/filesystem access remain separate. Browser remains disabled;
published v0.14.0 and parked draft UI #61 remain unchanged.
