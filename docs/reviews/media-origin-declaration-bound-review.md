**Cleared for offline implementation only. No blocking design finding for this correction.**

At clean HEAD `4cdc47be911af268e0a7b8eb3fd9bc6229e5c723`, the [all-row 1 GiB check](/home/zelo/zelo/core-v015-browser-renderer/qualification/media_origin.py:297) restricts metadata acceptance but provides no additional allocation/I/O protection: unselected sizes never drive resource consumption. Parsing remains bounded by input length, row count and the existing 12-digit grammar. Selected indexes retain their independent positive **128 MiB raw / 32 MiB compressed** limits.

The retained proof reports `media_origin_release_size`, exit `0`, cleanup confirmed and null evidence. Actual SHA256 rows contain unselected Contents sizes **1,455,981,187** and **2,182,275,917** bytes. All eight hashes listed in the refusal binding matched. This supports the diagnosed refusal; it establishes no successful operator acceptance.

Implement the explicit declaration range **0..999999999999**, preserving grammar, selection, hash/path/duplicate validation and all existing resource bounds. The proposed offline tests are appropriate; include exact selected-limit acceptance, limit-plus-one and zero refusal for raw, gzip and xz, plus the selected-bound causal fault. Retained-output replay must remain diagnostic.

**No third execution clearance.** Both refusals remain retained; quotas stay origin **2/4**, version **2/4**, loader **4/4**. Any third attempt requires a later new frozen HEAD and full exact-source review explicitly authorizing it. No automatic widening, retry, retroactive acceptance or downstream authority.

Review was sequential and read-only; no edits or tests ran. I cannot independently attest this session’s requested **gpt-6-astra Medium** configuration.
