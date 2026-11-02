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
server-side. Workspace access requires a separate `ZELO_WORKSPACE_ACCESS_TOKEN`
secret of 32–256 characters. The browser signs in with HTTP Basic username `zelo`
and that separate secret. API routes also require the `X-Zelo-Workspace: 1`
request header; that public marker is not an authentication credential. Both
Workspace and Core remain bound to loopback.

## Run locally

Requirements: Node.js 20.9+ and npm.

```bash
cd ~/zelo/core/workspace
npm install

# Reuse the token already configured for Core without printing it.
set -a
source ../.env
set +a

# Set a separate random Workspace secret in this shell or the local .env file.
# For example, generate one with: python -c 'import secrets; print(secrets.token_urlsafe(40))'
export ZELO_WORKSPACE_ACCESS_TOKEN=REPLACE_WITH_YOUR_DISTINCT_RANDOM_SECRET
npm run dev
```

Open <http://127.0.0.1:3000> and enter username `zelo` with the distinct
Workspace secret when the browser prompts. The server refuses Workspace
access when the secret is unset or invalid. Do not reuse the Core operator
token or put either secret in client code or a URL.

Core must already be running on <http://127.0.0.1:8000>. Override the loopback
URL with `ZELO_CORE_URL` only when using another local port.

## Current scope

- Talk to Zelo text conversation.
- Real Core task projection inside the conversation and execution inspector.
- Polling of canonical conversation state while a Core task is active.
- Placeholder navigation for Company World and Control Desk.

Not in this slice: a second database, task mutation outside Core, visual agent
world, durable goals/campaigns, memory, voice or external channels.
