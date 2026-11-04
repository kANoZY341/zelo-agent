# OCR real-diff evidence plan

## Goal

Add a bounded, offline quality receipt for two pinned historical Zelo Git diffs:
the destructive migration in PR #242 and the clean Browser change in PR #297.
The receipt must separate Git/JSON validity from semantic correctness. It must
never activate OCR or certify a review solely because a comment has a valid
line anchor.

## Steps

1. Add regression tests for exact commit pins, invalid JSON, known-defect
   misses, valid but unadjudicated comments, and a valid empty clean review.
2. Implement a small scorer over the existing `bind_ocr_review_from_git`
   adapter. Require a checkout at the pinned historical HEAD and hash raw
   output, evidence and result. Keep model calls and user data out of it.
3. Add a CLI that reads a bounded local raw JSON file and prints a receipt
   without printing review content. Document how to run each historical case
   in a disposable checkout and interpret the result.
4. Verify focused tests and lint on the Linux host, run the scorer on actual
   preserved outputs if available, obtain independent code review, then submit
   exact-head CI and merge if clean. Keep #132 open until independently
   adjudicated live model quality passes.
