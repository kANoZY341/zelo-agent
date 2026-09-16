# Zelo Core v0.1

A small, modular foundation for Zelo Agent. It currently provides a FastAPI
application, environment-backed settings, and root and health endpoints.

## Setup

Use Python 3.12 and run commands from the repository root (`~/zelo/core`):

```bash
# Only needed if .venv does not already exist:
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Configuration is optional. Copy `.env.example` to `.env` if you want a local
configuration file and do not already have one. `ZELO_APP_NAME` defaults to
`Zelo Agent`; environment variables override values in `.env`. The version is
defined in `app/__init__.py`.

## Run locally

```bash
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Stop with `Ctrl+C`. Keep the host set to `127.0.0.1`; this version is intended
only for local access. Interactive API documentation is at
<http://127.0.0.1:8000/docs> while the server is running.

```bash
curl http://127.0.0.1:8000/
# {"name":"Zelo Agent","version":"0.1.0","status":"online"}

curl http://127.0.0.1:8000/health
# {"status":"healthy"}
```

`/health` is a process liveness check. There are no external services or storage
dependencies to check in v0.1.

## Tests

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

Runtime dependencies are pinned to the verified environment. The development
requirements add pytest and HTTPX for endpoint tests.

## Structure and scope

- `app/main.py`: application factory and ASGI entry point.
- `app/api.py`: typed HTTP responses and routes.
- `app/config.py`: environment-backed settings.
- `tests/`: automated endpoint and configuration tests.
- `AGENTS.md`: permanent development and architecture rules.
- `memory/`, `secrets/`, `workspaces/`: reserved local directories, ignored by Git.

Existing contents of the reserved directories must be preserved. On a fresh
clone, create them only when needed. Keep credentials out of source control.

LLM integrations, agents, browser automation, databases, authentication, and
public hosting are outside v0.1. Future components should remain replaceable,
with provider access behind a model router and privileged agent actions behind
an approval layer.
