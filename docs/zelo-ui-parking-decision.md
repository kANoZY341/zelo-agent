# UI parking decision

## Latest priority — Core first; UI parked (2 October 2026)

Ahmad reports that the UI still needs major work and directs Core development to
continue now, with UI redesign/integration revisited at the end. This supersedes
the earlier U2-first delivery plan and provisional design acceptance.

Preserve UI PR #61 as a draft and do not merge it automatically, even if CI passes.
Preserve `feat/v0.15-live-conversation-ui` at `95f35cf` and the independent original
design worktree. Do not delete UI code, chats, backups or local beta data.
The active work is v0.15 Browser Worker qualification from API Worker merge
`8d33d56`. Continue the established Core roadmap; later UI rework requires fresh
acceptance. Do not spend this phase polishing, deploying or acceptance-testing UI.

## Work order

1. v0.15: finish bounded capabilities/workers, starting with Browser qualification.
2. v0.16: channels and presence through the canonical Core contracts.
3. v0.17: real specialist organizations under the existing hierarchy.
4. v0.18: evidence-based recovery, learning and advanced autonomy.
5. Revisit UI design and integration after Core-first work; revalidate against the
   then-current Core rather than merging the parked branch wholesale.

Basic operational interfaces and tests needed to verify Core may still be used.
This does not authorize resuming visual redesign, deleting prototypes or moving
production data. Optional worker dependencies and authority remain fail-closed.
