# Media pure package-index selector acceptance gates

```json
{
  "head": "0beb5699fd70644e2ff9437423c300a3b63881ad",
  "tree": "eed877923a5cb8ea75a031710c760525bab9ad0f",
  "ci": {
    "conclusion": "success",
    "databaseId": 37170785956,
    "headSha": "0beb5699fd70644e2ff9437423c300a3b63881ad",
    "status": "completed",
    "url": "https://github.com/kANoZY341/zelo-agent/actions/runs/37170785956"
  },
  "ci_summary": "3580 passed, 2 skipped, 11 warnings in 723.29s (0:12:03)",
  "files": {
    "qualification/media_package_index.py": {
      "bytes": 11607,
      "sha256": "6593b34bf704baf22addbe8806509639459a3b3ef53a5a5b29e8610a90d25384"
    },
    "tests/test_media_package_index.py": {
      "bytes": 19090,
      "sha256": "3274086ef899e97e29226a6f7fb99d22da7f2acc6c9eb40021e823d0dbb42b15"
    },
    "docs/v0.15-media-package-index.md": {
      "bytes": 8710,
      "sha256": "9fe1e811e0457f5a817c6396a4e3463c6f00e32723410cfc77f6686449948a85"
    },
    "AGENTS.md": {
      "bytes": 7171,
      "sha256": "1f95ab45dc6d151eca48069226838796180d630a81dba1fee172716d1ff145f0"
    },
    "runtime/media-package-index-r1-source-review.md": {
      "bytes": 4603,
      "sha256": "c4656cf8095ae7e5b71028088bd84afd36d858ffcbd62611b6b44d1db6ac2141"
    },
    "runtime/media-package-index-r1-source-review.log": {
      "bytes": 178956,
      "sha256": "1c3e8b5c02e05edd0e803b7947ea08c2ed0deee54db309bc226ccb14ffabf518"
    },
    "runtime/media-package-index-r1-supplements.json": {
      "bytes": 1642,
      "sha256": "bf18d5705d6aca52a9239ec41ea837c9424d54bbb28a33523d5c969cc870dac7"
    },
    "runtime/media-package-index-r1-offline.log": {
      "bytes": 1389,
      "sha256": "3e3e4ec804fb2ce5e4c8c67e00510384944e06cfe54a3c5c148f68eed5601d82"
    },
    "runtime/media-package-index-r1-all-focused.log": {
      "bytes": 3151,
      "sha256": "92fdcb9c1cb8ed7ba30f9d01d794204451716c5cd53490ff6bd50d73bf03edec"
    },
    "runtime/media-package-index-r1-ci.json": {
      "bytes": 197,
      "sha256": "9a978ddf2e82e7714f2106cb229102f86a0e14ed7cf60db978398ee65e77b937"
    },
    "runtime/media-package-index-r1-standalone.log": {
      "bytes": 1261,
      "sha256": "d3d579c909515fe01949057b748723b3644b8040572c911eb2a55f302f2089d4"
    }
  },
  "source_review_accepted": true,
  "model_header_verified": "gpt-6-astra / medium / read-only",
  "selector_tests_passed": 164,
  "media_tests_passed": 1763,
  "standalone_passed": 4,
  "causal_controls": 7,
  "intended_fault_failures": 14,
  "real_index_reads": 0,
  "metadata_only": true,
  "package_member_origin_verified": false,
  "runtime_authority": false,
  "attempt_counts": {
    "origin": 3,
    "version": 2,
    "loader": 4
  },
  "normal_exact_head_merge_ready": true,
  "merge_commit": "070f820bb90e493d2287ad6b0b96fd33e0dc4db6",
  "merged_tree_matches_reviewed": true
}
```

Original runtime review SHA is bound above; archived review normalizes trailing whitespace.
Postmerge CI is tracked separately. No actual index was read in this stage.
