**Approved for offline implementation only. No blocking design finding.**

The proposed `[A-Za-z0-9_+@.-]{1,128}` alphabet is safe within the current [bounded validator and exact index selection](/home/zelo/zelo/core-v015-browser-renderer/qualification/media_origin.py:242). Literal `@` receives no decoding or URI interpretation. Unused paths undergo validation and duplicate detection but never enter returned index records or drive I/O.

The retained stdout SHA256 matches the refusal binding: `914808b027087299fd6aaa1ac81a03d5e2a7f384f208a7b0a61250c00e7bde0a`. Its SHA256 section contains the 24 reported unused `@` icon declarations.

Implementation must retain:

- All unsafe-path, duplicate, ASCII, hash, size, depth and length refusals.
- Exact `INDEX_PATHS` selection and positive **128 MiB raw / 32 MiB compressed** limits.
- Proposed tests for discarded literal/multiple `@`, unsafe forms containing `@`, duplicate `@` paths, and selected-name substitutions failing required-index selection.
- Explicit policy binding of the revised alphabet.

Reviewed HEAD is `4cdc47be911af268e0a7b8eb3fd9bc6229e5c723` with the authorized size-correction working diff. The path proposal remains unimplemented; reported test passes were not rerun.

**No third-execution clearance.** New frozen HEAD and full exact-source review remain mandatory. Both failures remain retained; quotas remain origin **2/4**, version **2/4**, loader **4/4**. No automatic widening, retry, retroactive acceptance or downstream authority.

Review used sequential static reads, Git and SHA256 only. No edits or tests ran. I cannot independently attest the requested **gpt-6-astra Medium** session configuration.
