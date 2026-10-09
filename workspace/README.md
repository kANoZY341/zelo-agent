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

## OpenDots Computer Panel read-only adaptation

The detached `components/AgentComputerPanel.tsx` consumes only Zelo's existing
Core-owned `/api/agents/[id]/computer/panel` read endpoint. Its Browser, Files,
Terminal and Activity tabs are projections, not new mutation controls.
The component is intentionally not mounted in production; see
`docs/v0.15-opendots-readonly-computer-panel-port.md` before future UI work.
