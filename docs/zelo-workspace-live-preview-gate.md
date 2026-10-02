# Zelo Workspace — first live-preview gate

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

The original acceptance sequence below is preserved as historical reference, not
current scheduled work.


## Observed state, 2 October 2026

This is a parallel UI delivery checkpoint, not a change to the canonical
v0.15/v0.16/v0.17/v0.18 ordering or approval of a visual design.

The preserved `feat/zelo-definitive-ui-prototype` worktree contains the new
conversation redesign at `/design-exploration/conversation`. Its current design
note records the user's direction as **Only Zelo and conversation** and explicitly
marks the redesign as awaiting review. Black Chamber is a rejected historical
concept, not the current accepted production layout.

The new component uses local sample threads and simulated replies/voice/approvals.
It does not call Core; reload resets its state. The capture report contains
11 desktop/mobile states. During this inspection the preview route returned HTTP
200 on loopback port 3000, while the default Core health endpoint at loopback
port 8000 was unavailable. These are inspection observations, not ongoing monitors.

The UI worktree's committed base is `00039a4` (v0.12-era), with intentional
uncommitted prototype/design work. Do not reset, replace, or bulk-merge that
worktree. No UI file or running UI process was modified for this checkpoint.

## First usable beta

The new Talk interface does not depend on finishing every Browser/Computer/Media
worker, Company World, or v0.18 autonomy. Reuse the current verified Core and
its existing Workspace conversation proxy contracts rather than creating a
second chat database, identity system, task queue, or approval engine.

Acceptance sequence:

1. Obtain visual acceptance of the latest conversation layout; retain comparison
   previews. Implement integration in a separate worktree based on verified main,
   selectively carrying over accepted presentation components, not an old backend.
2. Use the configured Core instance after explicit database backup/migration checks.
   Do not start the old v0.12 backend just because the UI lives in that worktree.
   Keep the existing operator token server-side and both services loopback-bound.
3. Replace sample replies with actual conversation creation, turn submission,
   history loading, and persisted results through the current `/api/conversations`
   proxy routes and their existing Core contracts.
4. Prove continuity after refresh and restart, correct thread switching, no duplicate
   submissions, truthful offline/loading/failure states, and keyboard/mobile use.
5. Project one real bounded task's progress/result from Core. Never show a simulated
   approval as a real decision; hide or clearly disable unsupported preview actions.
6. Review/test the exact integration head and publish the beta through the normal
   PR/CI path. Do not report deployment merely because a prototype build passes.

Live voice requires a separately verified microphone/transcription/synthesis and
interruption path; the current voice animation is not proof of that integration.
Company World and deeper Control Desk work must not block the first real Talk beta.

## Existing components to reuse

On the current Core baseline, `workspace/lib/core.ts` attaches the operator token
server-side and restricts the configured upstream to loopback HTTP. Conversation,
turn, health, project, campaign, and voice proxy routes already exist. Their
presence is a reuse opportunity, not evidence that this new preview is connected.
