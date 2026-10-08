# Operator account ledger — append-only SQLite bypass and correction

8 October 2026. Independent read-only Codex Astra Medium source review of PR
#115 identified a P2 SQLite replacement-write bypass: BEFORE UPDATE/DELETE
triggers alone are insufficient while recursive_triggers is disabled.
A direct INSERT OR REPLACE could replace an existing account fingerprint and
secret selection without those triggers firing.

Migration 0037 was amended before merge: use WITHOUT ROWID for both tables,
and add an explicit duplicate-account-key BEFORE INSERT ABORT trigger alongside
the existing UPDATE and DELETE guards. The service remains inert and does not
add HTTP, credentials, provider authentication, messaging, Mind admission,
worker execution or runtime skill activation.

Proof: 32 direct account-ledger regressions passed at the corrective source,
including INSERT OR REPLACE, REPLACE, and INSERT OR IGNORE for selection and
revocation, original data preservation, and rowid refusal. A full CI/source
re-review of the corrective head remains mandatory before any merge.
