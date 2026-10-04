# Media archive acquisition final gates

PR #86 merged at `b1638a0e7f074c8ca8a2bc47326e315d510a0820`. Its tree exactly matches
`53f8fcd719b51058c99081557dc0bb37453c80e7`. The records below bind the
pre-merge source, matching CI, existing supplements and accepted actual evidence.
Post-merge CI is a separate run; its result is not claimed here.

```json
{
  "archive_bytes": 8745084,
  "archives_verified": 8,
  "attempts_used": 1,
  "base": "19e0be24cb0a60bac1f540f08a27d82082895e9d",
  "ci_conclusion": "success",
  "ci_passed": 3737,
  "ci_run": 37178199742,
  "ci_skipped": 2,
  "evidence": {
    "actual_audit": {
      "path": "runtime/media-package-acquisition-actual-audit-20261004.json",
      "sha256": "b2bee92962c76c2d1e9a1278e517d7557b231c7258ea49755fdc7abe2e20d8f3"
    },
    "actual_review": {
      "path": "runtime/media-package-acquisition-actual-review-20261004.md",
      "sha256": "0a58ab87a6b3de646fe305d67a01da10a25785d4808e8641e2d8f98c73cf9252"
    },
    "receipt": {
      "path": "runtime/media-package-acquisition-actual-20261004.stdout.json",
      "sha256": "6d7d836aa55eca8ba2ca34897ab8462a5869bd42a6c33a4c4eacbe78e697f91c"
    },
    "source_review": {
      "path": "runtime/media-package-acquisition-r2-source-review.md",
      "sha256": "de90e13976224cd4a451c1cbcdd99881ed326ee40316b788f7435a97418b2309"
    },
    "supplements": {
      "path": "runtime/media-package-acquisition-r2-supplements.json",
      "sha256": "f1788d4c2eed7b8d1363d3264dd86303331cbd3e2b69a8018ca82094a91c80c7"
    }
  },
  "fresh_acquisition_tests": 97,
  "head": "023eccba160952f0623f2c1b2d132aa598384ca3",
  "intended_causal_failures": 7,
  "proof_sha256": "c7af88e91dc08c92b83f218c5553574f6a4df6ba89b42ce70010990e22771f51",
  "retained_media_tests": 1920,
  "review_method": "Existing Astra Medium source clearance; direct ChatGPT actual-evidence acceptance",
  "runtime_authority": false,
  "standalone_passed": 4,
  "tree": "53f8fcd719b51058c99081557dc0bb37453c80e7"
}
```
