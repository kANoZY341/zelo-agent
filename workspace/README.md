# Zelo Workspace

The Zelo-owned workspace is a presentation and interaction layer over canonical
Zelo Core state. It does not own tasks, approvals, scheduling, memory or worker
authority.

The first slice proves the same path as the Early Zelo Chat live smoke test:

```text
browser
  -> local workspace server
  -> authenticated Zelo Core conversation API
  -> Zelo Mind
  -> optional canonical Core task
  -> projected task status/result
  -> browser
```

The browser never receives `ZELO_OPERATOR_TOKEN`; Next.js route handlers attach it
server-side. The local proxy also requires an `X-Zelo-Workspace: 1` request header,
and Core itself remains bound to loopback.

## Run locally

Requirements: Node.js 20.9+ and npm.

```bash
cd ~/zelo/core/workspace
npm install

# Reuse the token already configured for Core without printing it.
set -a
source ../.env
set +a

npm run dev
```

Open <http://127.0.0.1:3000>.

Core must already be running on <http://127.0.0.1:8000>. Override the loopback
URL with `ZELO_CORE_URL` only when using another local port.

## Current scope

- Talk to Zelo text conversation.
- Real Core task projection inside the conversation and execution inspector.
- Polling of canonical conversation state while a Core task is active.
- Placeholder navigation for Company World and Control Desk.

Not in this slice: a second database, task mutation outside Core, visual agent
world, durable goals/campaigns, memory, voice or external channels.

## Live conversation beta

Open `/talk` for the accepted charcoal/mint conversation UI connected to current
Core. `/` retains the original Workspace and Control Desk;
`/design-exploration/conversation` remains a labeled simulation for comparison.
Only Manrope and Space Grotesk font assets are imported by these routes.

The beta sends real text through the existing server proxies, restores the selected
Core conversation ID, and pages history and turns in windows of 50. Task details
show Core status/results; visible pending, running and awaiting-approval tasks
refresh automatically. Earlier messages and Latest messages navigate bounded turn
windows. Voice, attachments and approval execution are unavailable in this beta.

An uncertain turn keeps its exact request body and idempotency key in memory.
Retry original message restores that text; changing the draft cannot reuse the key.
Resolve an uncertain send before switching threads. Reloading during an uncertain
send loses this in-memory retry identity: inspect Core history before sending again.
Local storage holds only the active ID, never chat history or credentials.

Core's create-conversation endpoint does not support idempotency. The UI serializes
creation and never automatically retries an uncertain create. Check history and
reopen the created conversation, or explicitly start a new chat. Full create
idempotency requires a separately authorized Core contract change. Aborting or
leaving the UI does not cancel Core work. No new configuration is required.
