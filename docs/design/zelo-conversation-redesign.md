# Zelo conversation redesign

## Direction and scope

On 2026-10-02 the user asked to redo the full UI and interaction, rejecting the
Black Chamber refinement. Asked what should remain visible while talking to
Zelo, the user selected **Only Zelo and conversation**.

This preview uses one centered conversation with a shared text and voice input
area. History, results, and approvals open on demand and return to the thread.
There is no permanent secondary panel. The visual system uses charcoal, mint,
Manrope, and Space Grotesk, with a compact Z mark replacing the sculptural stage.
The user accepted this UI direction for now on 2026-10-02 and requested only a
refinement of the Z mark. Future design changes remain open. This is still a
local interaction preview awaiting production integration.

## Preview

From `workspace/`, run `npm run dev`, then open:

- http://127.0.0.1:3000/design-exploration/conversation
- http://127.0.0.1:3000/design-exploration/conversation?state=conversation

The route accepts `state=home|conversation|listening|thinking|speaking|result|approval|history`.
Use Talk to start the simulated voice flow, Send voice to submit its fixture
transcript, Interrupt to return to listening, and End voice to leave the session.
Typing and voice use the same thread. Textarea height grows with input; Enter
sends, Shift+Enter adds a line, and IME confirmation does not send.

Results appear as inline cards and open in a dialog. Approvals show a local
review dialog. History supports search, switching threads, and starting a new
conversation. Escape closes dialogs and restores the conversation. Cancelling
a reply or switching threads invalidates pending reply timers. Attachments are
cleared when switching conversations; only file names are displayed.

## Preview boundaries

All replies, transcripts, history fixtures, and approval decisions are local
preview state. No microphone capture, speech recognition, speech synthesis,
model, Core, worker, API, file upload, or real approval execution is connected.
The preview badge and voice labels identify the simulation. Session state resets
on reload. The existing production UI and all previous concept routes remain.

## Source and capture

New source files:

- `workspace/app/design-exploration/conversation/page.tsx`
- `workspace/components/design-exploration/conversation/ConversationPreview.tsx`
- `workspace/components/design-exploration/conversation/conversation.css`
- `workspace/components/design-exploration/conversation/capture.mjs`

The component reuses the existing Glyph and locally installed fonts without
changing their implementations or adding dependencies. Styles are scoped to `.z2`.

With the loopback dev server running, from `workspace/`:

```bash
npx eslint components/design-exploration/conversation/ConversationPreview.tsx app/design-exploration/conversation/page.tsx
npm run typecheck
PLAYWRIGHT_BROWSERS_PATH=.cache/ms-playwright node components/design-exploration/conversation/capture.mjs
```

The capture script writes 11 screenshots, a four-state comparison, and a JSON
report under `workspace/generated/zelo-conversation-redesign/`. Desktop captures
are 1440 x 900; mobile captures are 390 x 844. Generated outputs are ignored by Git.

## Validation

Scoped ESLint and the full Workspace typecheck passed. Browser checks passed for
text submission, composer growth, IME handling, reply cancellation, history search,
attachment isolation, immediate voice stop, voice submission and interruption,
result dialogs and Escape, preview approval, and starting a new conversation.
All 11 screenshots and the comparison were visually inspected. The captured
states had no root overflow, browser errors, or application API requests.
All 33 previously protected source files matched their recorded hashes.

Work remains uncommitted on `feat/zelo-definitive-ui-prototype`. Nothing was
pushed, merged, deployed, or integrated with Core. The temporary loopback server
is stopped after verification.

## 2026-10-02 — Z mark refinement

Replaced the font-based lowercase z with a custom SVG monogram: a bold angular
Z with cut terminals, mint fill, and a dark green badge. The same vector is used
on the welcome screen, header, assistant messages, and pending reply indicator.
Its box dimensions are unchanged. No conversation behavior changed.

Scoped ESLint passed. Desktop home and thinking states and the mobile conversation
rendered with the SVG in every mark position and no browser errors. The home,
mobile, and mark comparison captures were visually checked. New screenshots are
under `workspace/generated/zelo-conversation-redesign/mark/`. The comparison
shows the mark enlarged alongside the actual 32 px header and 25 px message sizes.
Verification reused the existing Workspace development server; this pass did not
start or stop it. Nothing was committed or pushed.
