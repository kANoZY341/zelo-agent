# Reviewed Hindsight comparison evidence

Run `5c5eae20c40a4a26a9915e7ca63ecf50` completed fourteen native admissions and
thirty answers on 10 October 2026. The unchanged grading command accepted the
exact operator review and result bindings before documentation edits began.
The owned API process and PostgreSQL container were then stopped; database
contents and original runtime evidence remain preserved.

The [comparison guide](../../v0.15-hindsight-memory-comparison.md#observed-result)
contains the findings, costs, extraction-fidelity review and adoption assessment.

## Principal evidence

| Record | What it establishes |
|---|---|
| [Export manifest](manifest.json) | Original source paths, relative export paths, byte counts and SHA-256 values for all 368 exact copies, plus the existing public-workflow evidence bindings. |
| [Comparison protocol](comparison-protocol.json) | Frozen corpus, questions, expected facts, rubric, scope, model profile, deadlines and source/code identity. |
| [Runtime protocol](runtime-protocol.json) and [readiness](readiness.json) | Pinned runtime, dependency-manifest and model identity, verified native API process and explicit local configuration identity. |
| [Core registry](execution/core-registry.json) | Actual fresh Core identities and lifecycle state after all writes, including correction, expiry and the separate agent scope. |
| [Native ingestion](execution/hindsight-ingestion.json) | All fourteen stored documents and mapped native facts, document identity checks, operation timing and native extraction usage. |
| [Comparison result](execution/comparison-result.json) | All thirty answer/input hash bindings, automatic checks, retrieval and answer measurements, resource snapshots and ungraded aggregate diagnostics. |
| [Operator review](execution/operator-review.json) | Complete factual and grounding judgments against the frozen expectations, exact answer hashes, canary qualification and explicit adjudication. |
| [Graded result](execution/graded-result.json) | Verified combination of factual correctness, whole-answer grounding and all retained automatic checks. |
| [Execution supervisor receipt](execution-launch-receipt.json) | Completion, monotonic duration, wall timestamps and 633 interference observations. |
| [Startup receipt](startup-receipt.json) and [owned cleanup receipt](owned-cleanup-receipt.json) | API startup cost and the final verified API/PostgreSQL process states. |

The code frozen for execution and grading was
`8768b7637a7d0b3ef9129ea30cab04ea51d0be87`, tree
`a1b776c2f4b9df984d6cf8420055262c1dc8f9be`.
Later documentation commits preserve that original identity in every copied
protocol and receipt.

The review was performed by the assistant acting as operator, with independent
assistant-agent cross-checks of every case. It was not blinded human review or
a user-supplied label set. The requested fact and the complete explanation were
graded separately. No retained automatic check was overridden.

| Arm | Factual correctness | Whole-answer grounding | Grounded-correct |
|---|---:|---:|---:|
| Zelo eligible memory | 9/10 | 10/10 | 8/10 |
| Raw Hindsight | 8/10 | 6/10 | 6/10 |
| Hindsight with Core filtering | 8/10 | 8/10 | 8/10 |

## Reading a case

Each `execution/qNN-ARM-input.json` contains the actual packed native excerpts
and model request controls. Its `-model-result.json` preserves the provider
result, and `-answer.json` binds both artifacts and records the parsed answer,
automatic checks, usage and resources. The final result and operator review
bind the answer bytes by SHA-256.

For each question, `qNN-core-retrieval.json` preserves native Core retrieval;
`qNN-hindsight-recall-{request.json,response.body,finished.json}` preserves the
single Hindsight recall shared by its raw and filtered arms. The corresponding
`qNN-mapped-evidence.json` preserves mapping, filtering and their measured costs.
The answering contexts use returned native text with the frozen packing rules.

Each `rNN-admission.json` binds the stored document, native facts, Core
provenance and extraction audit. The retain, document and facts-page
request/response/finished records are also included, along with all four bank
absence/create checks. The cumulative native extraction audit and its per-record
slices retain the actual model-call accounting.

## Setup history and scope

The two directories under `prior-failures/` preserve setup failures before any
extraction or answer inference: a PostgreSQL mount-parent permission failure and
an incorrect bank-absence route. Their protocols, failure receipts and available
route evidence remain exact. They are outside the thirty scored answers.

The historical `inputs/provenance/closure-report.json` records an earlier
excluded-Torch lock-metadata problem. The correction preceded the scored run;
current readiness and installed verification establish the successful setup.
The pinned wheel selection, verification and notice records retain their original
content. Model weights, wheels, source archives and database files remain in the
owned runtime location. Runtime credentials and environment-bearing plan/launch
files are excluded from this review packet. Copied bytes were checked against
the generated PostgreSQL secrets and common encodings before export.

The existing [public report](../public-research-v0.15/report.json),
[semantic review](../public-research-v0.15/semantic-review.json) and
[memory receipt](../public-research-v0.15/memory-receipt.json) retain the earlier
workflow's operational success, raw answer failure and separately reviewed
memory admission.

## Verifying the export

From the repository root, this read-only check recomputes every copied file's
byte count and digest:

```python
import hashlib
import json
from pathlib import Path

root = Path("docs/reviews/hindsight-memory-comparison-v0.15")
manifest = json.loads((root / "manifest.json").read_text())
for entry in manifest["files"]:
    raw = (root / entry["export_path"]).read_bytes()
    assert len(raw) == entry["bytes"]
    assert hashlib.sha256(raw).hexdigest() == entry["sha256"]
print(f"Verified {len(manifest['files'])} exact copies")
```

This directory is a review packet. The CLI's execution and grading checks also
require the original clean code identity and run layout; the manifest maps those
original paths without rewriting them. A new comparison requires a fresh run ID,
new databases and a newly frozen protocol. The existing run remains consumed.
