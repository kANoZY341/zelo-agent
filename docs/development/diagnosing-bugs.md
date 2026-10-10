# Diagnosing bugs

Use for a reported failure, inconsistent result, or measured performance
regression. The goal is a demonstrated explanation and a correction that
addresses the reported symptom.

## Procedure

1. Record the affected revision, relevant environment, input, expected behavior,
   and observed behavior. Separate the user's symptom from nearby failures.
   Reuse existing redacted evidence before collecting more.

2. Choose the smallest authorized feedback loop that reaches the failing path:
   an existing test, a fixture-driven command, or an isolated integration case.
   Assert the actual symptom. A successful process exit alone is insufficient.

3. Run the loop before changing behavior and record its result. Reduce irrelevant
   setup while preserving the failure. For timing defects, prefer controlled
   callbacks, events, or barriers over arbitrary sleeps and repeated full suites.

4. Compare a small set of plausible explanations. For each, state an observation
   that would support or disprove it. Use source inspection and targeted probes
   to distinguish them. Change one relevant condition at a time.

5. Add the correction and a regression at the interface that reproduces the
   defect. If the original failure was demonstrated on another revision, preserve
   that revision and its result. An intentionally reintroduced defect must be
   identified as a controlled experiment, with evidence of the actual change.

6. Repeat the original scenario and the relevant neighboring checks. Remove
   temporary instrumentation and artifacts created for this task. Preserve
   existing user data, retained qualification evidence, and refusal records.

## If reproduction is unavailable

Record the missing evidence and what the available checks establish. Continue
useful source inspection and independent work within existing access. Describe
a plausible explanation as a hypothesis; do not claim that a passing synthetic
test proves the cause of an earlier incident.

Request additional access or user evidence only when it is necessary to resolve
a concrete remaining uncertainty. This procedure supplies no new proof budget,
production access, or permission to retry a previously refused qualification.

## Completion evidence

Report the symptom, tested explanation, affected revision, reproducer,
before/after result, relevant regression results, and remaining uncertainty.
Do not add overlapping test counts together.

## Attribution and adaptation

Adapted from Matt Pocock's MIT-licensed
[diagnosing-bugs](https://github.com/mattpocock/skills/blob/49dd158d1076134a641b33efb035946536778336/skills/engineering/diagnosing-bugs/SKILL.md).

Preserves symptom-specific feedback, hypothesis comparison, targeted
instrumentation, and regression evidence. Zelo bounds repeated experiments,
does not automatically invoke the upstream human-assisted script, and records
uncertainty when the original environment cannot be reproduced.
