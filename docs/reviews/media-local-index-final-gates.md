# PR85 final gates

PR85 merged at 19e0be24cb0a60bac1f540f08a27d82082895e9d with its exact reviewed tree.
Matching CI passed 3,640 tests; both actual qualifications and final reviews passed.
The first failed index attempt remains refused and preserved.

```json
{
  "head": "7cc8849c761e450bf683b7010b7b2f38cf4577f6",
  "tree": "c93804d0fd205b654892e9b4edb7631418886201",
  "ci": {
    "conclusion": "success",
    "headSha": "7cc8849c761e450bf683b7010b7b2f38cf4577f6",
    "status": "completed"
  },
  "ci_run": 37174453001,
  "selector_operator_passes": 209,
  "media_passes": 1823,
  "standalone_passes": 4,
  "causal_controls": 10,
  "intended_failures": 14,
  "index_proof": {
    "head": "7cc8849c761e450bf683b7010b7b2f38cf4577f6",
    "tree": "c93804d0fd205b654892e9b4edb7631418886201",
    "proof": "/home/zelo/zelo/core-v015-browser-renderer/runtime/media-package-index-proofs/index-proof-_g3tg80j/cases.json",
    "proof_sha256": "199b3b16ee247497b782d78356bb88f3f67ed717401d5cb39ec7a8fc2c0b210d",
    "policy_digest": "9a2f60cfaec85edad8ce501f38d0b614ed875df8de1abc680bf911e97ea25f13",
    "entries": 3,
    "regular_bytes": 7768,
    "indexes": 3,
    "input_bytes": 20353024,
    "packages": 8,
    "pending_release_confirmed": false,
    "receipt_release_confirmed": true,
    "index_attempts_used": 2,
    "origin_attempts_used": 4,
    "final_review_pending": false
  },
  "origin_proof_sha256": "faab51dce2a95073a251049a559ceddb027ff01a36f3219e5236ca0c1bd6cd52",
  "final_review_sha256": "a0c3698259da6ee9c532ffa91ba8999a471ec75d021a2024b59abcd507db73b9",
  "review_model": "gpt-6-astra",
  "review_effort": "medium",
  "review_mode": "read-only",
  "quotas": {
    "origin": 4,
    "index": 2,
    "version": 2,
    "loader": 4
  },
  "runtime_authority": false,
  "merge_ready": true,
  "ci_passed": 3640,
  "ci_skipped": 2,
  "ci_warnings": 11,
  "ci_seconds": 721.51,
  "merged": true,
  "merge_commit": "19e0be24cb0a60bac1f540f08a27d82082895e9d",
  "merged_at": "2026-10-04T03:51:06Z",
  "merge_tree_verified": true
}
```
