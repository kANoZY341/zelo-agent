# Behavioral testing

Use when changing important behavior or preventing a demonstrated regression.
Choose tests because they can detect a relevant failure.

## Choose the interface

Identify the narrowest stable interface that exercises the behavior: a module
contract, Worker entry point, HTTP route, or operator command. State what the
chosen test catches and any important path it does not cover.

Use task requirements and existing contracts to make routine testing choices.
Ask the user only when the intended product behavior is materially ambiguous.
Documentation edits and other low-impact changes do not require invented tests.

## Build a useful test

1. State an externally meaningful outcome: returned data, persisted state
   available through its interface, a refusal, cleanup, or prevention of a
   forbidden side effect.

2. Obtain expected results independently from the requirement, a worked example,
   or a known fixture. Do not reproduce the implementation's algorithm inside
   the assertion or derive expected output from the code under test.

3. Exercise the real coordination path. A direct helper test cannot establish
   behavior that depends on multiple callers, an async boundary, retry, or
   revocation during execution.

4. For a bug, observe the regression fail for the relevant reason before the fix
   where practical. Implement one coherent behavior at a time, then verify it.
   Avoid generating a large suite around assumptions about unfinished behavior.

5. Run affected tests during editing. Broaden verification for a concrete
   remaining risk or an established merge gate. Stop when the intended behavior
   and required gates are sufficiently verified.

## Test doubles and security boundaries

Prefer real in-process components and isolated test databases where practical.
Use controlled substitutes for external services, computers, clocks, randomness,
and other nondeterministic boundaries.

A backend spy can establish that a revoked request never dispatched a file or
browser operation. That is a security outcome, not merely a preferred internal
call sequence. Assert both the refusal and absence of the forbidden effect when
the contract requires them.

Avoid mocking away the authority check, identity binding, or coordination path
whose behavior the test is meant to prove. Add private-helper tests only when
they provide distinct evidence; do not substitute them for integration coverage.

## Attribution and adaptation

Adapted from Matt Pocock's MIT-licensed
[tdd](https://github.com/mattpocock/skills/blob/49dd158d1076134a641b33efb035946536778336/skills/engineering/tdd/SKILL.md),
[tests](https://github.com/mattpocock/skills/blob/49dd158d1076134a641b33efb035946536778336/skills/engineering/tdd/tests.md),
and [mocking](https://github.com/mattpocock/skills/blob/49dd158d1076134a641b33efb035946536778336/skills/engineering/tdd/mocking.md)
guidance.

Preserves independent expectations, stable interfaces, and small implementation
cycles. Zelo does not require user confirmation for every test interface, import
other upstream skills automatically, or prohibit meaningful denial-before-effect
assertions.
