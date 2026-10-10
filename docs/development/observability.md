# Observability

Use when changing I/O, retries, background work, or a failure path that existing
evidence cannot explain. Make the important outcome diagnosable using the
smallest useful addition to Zelo's existing events and diagnostics.

## Start with a question

Write the concrete question the new evidence should answer. Examples:

- Which validation phase refused this operation?
- Did permission change before backend dispatch?
- Which checked metadata field changed?
- Did a dependency fail, time out, or return an invalid response?
- Which entry point started the operation, and where was time spent?

If existing evidence already answers the question, reuse it.

## Select and bound the evidence

- Use established event names and schemas. Add stable reason categories instead
  of relying on free-form exception text.
- Carry existing trusted task, attempt, or operation identifiers across relevant
  async boundaries. If several entry points share a sink, record the entry point
  where the operation starts; do not infer it afterward.
- Measure duration with the existing monotonic timing facilities where relevant.
  Keep timing diagnostic; it does not establish identity or permission.
- Allowlist fields and bound their size. Prefer phase, outcome, safe reason,
  attempt number, and duration over entire requests or responses.
- Do not record credentials, auth headers, private document contents, raw model
  prompts, unrestricted URLs, or arbitrary exception representations.
- Use bounded categories for metric labels. Individual IDs and raw error text
  must not create unbounded metric series.

Recording which metadata fields changed can explain a verification refusal
without logging file contents or weakening the comparison.

## Verify the evidence

Exercise one representative success and the relevant refusal or failure in an
authorized isolated environment. Inspect the actual emitted record: does it
answer the question, retain correlation, and exclude sensitive fields?

For events used to prove an ordering claim, check where they occur relative to
the operation. An event saying work started is not proof that a backend action
completed. Logging must not alter dispatch, retry, cancellation, or cleanup
semantics.

Add assertions for important diagnostic contracts and redaction behavior when
those contracts change. Avoid a separate test for every harmless log line.

## Keep infrastructure proportional

This procedure does not require a new telemetry service, tracing package,
dashboard, or alert for each change. Propose additional infrastructure only
when existing facilities cannot answer an operational need. External delivery
and production instrumentation retain their existing authorization requirements.

## Attribution and adaptation

Adapted from Addy Osmani's MIT-licensed
[observability-and-instrumentation](https://github.com/addyosmani/agent-skills/blob/1401c8b8030e023baeebb31781a6653fe8e93026/skills/observability-and-instrumentation/SKILL.md)
and [observability checklist](https://github.com/addyosmani/agent-skills/blob/1401c8b8030e023baeebb31781a6653fe8e93026/references/observability-checklist.md).

Preserves question-driven instrumentation, structured evidence, correlation,
entry-point attribution, bounded cardinality, and inspection of actual output.
Zelo adapts the upstream deployment-wide checklist to the current change and
existing telemetry stack.
