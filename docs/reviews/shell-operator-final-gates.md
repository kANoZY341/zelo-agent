# Qualified Shell operator/application final gates

PR [#72](https://github.com/kANoZY341/zelo-agent/pull/72) merged at
`159caee5c5fba2bbc25c3b3f75b5365eebef1fed` from exact reviewed/tested head
`1d9fd54fc37cb977ec58f04f7c295f1bfc307835`. Both trees are
`e88e313a95f0dc97971e3eb05b195a8cb3ed3325`.

The opt-in fixed diagnostic uses actual application lifespan, authenticated
in-process ASGI and background Runner with default qualified Shell/native factories.
Private init-only settings, SQLite/admission/workspaces and a counted rejecting
provider verify no model requests. Production flags remain disabled. No listener,
external network, default Agent/Mind route, release or parked UI change was added.

| Gate | Result |
|---|---|
| Exact-head full CI 37097288190 | 1,817 passed, 2 skipped, 11 warnings; pytest 728.69 seconds |
| Focused operator/Core/Browser checks | 163 passed, including 25 proof controls |
| Other assembly compatibility | 302 passed; counts overlap with focused checks |
| Standalone exact-head supplements | 2 installed-Codex and 2 clean-source checks passed |
| Real normal and invalid-ambient application proofs | 6 cases passed in each clean exact-head run |
| Model request counters | Both inventory/generate counters zero in both proofs |
| Shutdown | Runner terminated, runtime cleanup confirmed, fresh DB ownership acquired |
| Negative controls | 2 intended failures for factory isolation and audit worker identity |
| Independent Astra Medium | Initial review fixes plus exact-head combined full review clear |
| Lint/format/diff | Passed |
| Merge comparison | Tested and merged trees match |

Initial review and the actual ambient CLI exposed default Settings construction
on importing app.main. The unchanged factory is now in app/application.py; the
normal app.main:app entry and create_app re-export remain compatible. Fresh-process
regressions forbid ambient settings-source access during factory/proof imports.
Audit checks bind routing, selected/revalidated workers, operator/policy and all
execution attempt IDs, with required event uniqueness. Shutdown checks completed
Runner and reacquired private DB ownership. Two deliberate regressions proved
those controls reject the earlier failures. Optimized Python is refused.

Private ignored evidence: operator-proof-kp0rpfx5 and operator-proof-d0emgsqu,
standalone/negative logs, exact review, full CI log and final gate JSON under
runtime/. Deliberate private unknown-fence bytes remain unchanged. Installed source
hashes are change detection rather than independent provenance. Trusted local
operator/ancestors, filesystem/kernel, approved fixed sources, process visibility
and a live owning event loop remain prerequisites. This is diagnostic acceptance,
not live enablement or production certification.

Continue with the separate Media PNG contract prerequisite. Computer/Media native
and external adapter qualification remains separate v0.15 work. Published v0.14.0,
parked draft UI #61 and v0.16 -> v0.17 -> v0.18 order remain unchanged.
