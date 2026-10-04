# Offline member contract review — 4 October 2026

Direct ChatGPT source/design review, not an independent Codex execution. Scope:
qualification/media_member_contract.py, tests/test_media_member_contract.py,
updated Project/context checks, documentation and retained research records.

Module SHA256: ff297c9c854255508289889ae6cc44e674d6b7dc0123ca0c2686e88f7c8acba0.
Contract-test SHA256: 059818e8d9b156b506800de610091a454e245abcdf132088805519675a380256.
The final commit/tree and its full CI must additionally be bound in the PR review.

## Results

- 181 new synthetic contract cases passed. The 284-case focused combination with
  Project/context and acquisition tests passed before the final checkpoint edit.
- All 2,101 Media tests passed after the contract implementation; no native
  member qualification or new Codex model call was run.
- After updating status/roadmap, a line-wrapped status phrase failed the literal
  integration assertion. The phrase was corrected without loosening the test.
  Fresh Project/context plus contract rerun: 187 passed, three warnings.
- Six in-memory guard-removal controls were detected: owner outcome, exact
  binding, normalized duplicate, non-directory ancestor, payload extent and
  batch-member ceiling. The unchanged source refuses each intended family;
  removing the specific guard permits the synthetic bad input, demonstrating
  that its refusal expectation would fail. No on-disk source was patched.
- Repository Ruff lint and formatting passed; 350 Python files formatted.
  Diff whitespace checks passed. Full CI on the new commit is still required.

The initial recursion test assumed a particular interpreter recursion threshold.
The real nested input was rejected as a non-manifest instead. Replace that
assumption with a modest real nested-nonmanifest case and a separately injected
RecursionError test requiring a fixed sanitized diagnostic. The decoder itself
was not relaxed. Do not report the initial test failure as a successful run.

## Source assessment

No blocking findings for this offline metadata scope. ExpectedBinding and
OwnerOutcome are separate frozen caller-supplied values, not manifest-controlled
authority. Binding exact types/values, owner flags/exit and raw byte limits are
checked before JSON decoding. Duplicate keys, non-finite values, unknown shapes,
bool-as-int, malformed UTF8 and wrong schema/ref identities refuse explicitly.

Members preserve ordinal/raw name and a tightly normalized relative canonical
name. Both orders of non-directory ancestor conflicts are checked; canonical
collisions refuse. Link targets are bounded inert strings, not resolved paths,
opened files or accepted content. Metadata mode/UID/GID are recorded, not applied.
The regular size/digest contract does NOT authenticate any payload bytes.

Single and batch caps cover raw JSON, member count and reported expanded bytes.
All batch caller outcomes/identity/raw limits are checked before decoding any
item; total members are checked before returning the complete tuple. Results
are frozen, independently built metadata values with runtime_authority=false.
No partial accepted batch or support-release receipt is exposed.

The module imports only dataclasses/hashlib/json. It has no filesystem, archive,
network, native, settings, route or process entry point. This is tested as a static
import boundary and confirmed by source inspection. No shared admission/ownership
helpers were modified. The current status retains exactly one machine-consumed
active heading and current facts inside its existing 2200-character budget.

## Limits of this review and next gates

The outcome fields still require a real independently qualified owner to supply
truthful observations. Anyone calling a pure Python validator can manufacture
synthetic consistent metadata; it is intentionally not an authentication service,
capability token, sandbox, complete archive validation or promotion authority.
Reported hashes and minimum tar-size arithmetic cannot certify actual content,
PAX/GNU/sparse rules, complete framing, termination or native cleanup.

The bounded JSON input limits do not replace process memory/CPU controls. This
code is not wired to untrusted live traffic. Future execution still needs exact
parser/runtime selection, resource/ownership evidence and source/actual review.
Known unsupported parser/initialization questions remain in the helper review;
no signature, installed binary provenance, ABI or runtime closure is inferred.

Disposition: source is acceptable for the pure qualification-only contract.
A draft PR may carry it through matching full CI and final exact-head review.
Do not merge with a pending/failed/mismatched CI run, and do not activate native
parsing, decoder, Core runtime flags or UI as a consequence of this contract.
