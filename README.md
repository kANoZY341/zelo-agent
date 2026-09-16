# Zelo Core v0.3

A small, modular foundation for Zelo Agent. It currently provides a FastAPI
application, durable task execution, approvals, and a localhost-only Ollama
model gateway.

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
# {"name":"Zelo Agent","version":"0.3.0","status":"online"}

curl http://127.0.0.1:8000/health
# {"status":"healthy"}
```

`/health` is a process liveness check. Model calls are available only through Ollama on `127.0.0.1`; there is no cloud,
arbitrary URL, download, or fallback provider. `ai_prompt` tasks always require
an explicit approval. Logical profiles select the configured
`local_general`/`local_reasoning` aliases and bind the selection snapshot to the
approval.

## Tests

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

Runtime dependencies include the local HTTP client used by the Ollama adapter.
The development requirements add pytest and Ruff.

## Structure and scope

- `app/main.py`: application factory and ASGI entry point.
- `app/api.py`: typed HTTP responses and routes.
- `app/config.py`: environment-backed settings.
- `tests/`: automated endpoint and configuration tests.
- `AGENTS.md`: permanent development and architecture rules.
- `memory/`, `secrets/`, `workspaces/`: reserved local directories, ignored by Git.

Existing contents of the reserved directories must be preserved. On a fresh
clone, create them only when needed. Keep credentials out of source control.

Provider access is typed and replaceable behind `app.gateway.ModelProvider`.
The authenticated `GET /models` endpoint reports local alias availability.
Prompts and hidden reasoning are not written to audit events; only model result
text and non-secret usage/timing metadata are persisted.

## Local Model Gateway

`ai_prompt` tasks use the following path:

```text
Task -> ModelGateway -> ModelRouter/profile policy -> ModelProvider -> Ollama
```

The router is deterministic: `general` selects `local_general` and
`reasoning` selects `local_reasoning`. The default registry maps those aliases
to `qwen3:4b-instruct` and `deepseek-r1:7b`. The actual names can be changed
with `ZELO_OLLAMA_GENERAL_MODEL` and `ZELO_OLLAMA_REASONING_MODEL`; the
configuration version is included in the approval binding.

Ollama is the only provider in v0.3 and must remain on localhost. Configure its
HTTP endpoint with `ZELO_OLLAMA_URL` (default
`http://127.0.0.1:11434`). Zelo never accepts provider URLs from tasks, uses
cloud providers, downloads models, or silently falls back. Model calls require
operator approval, and models return text only without tool or action access.

List and pull models manually with Ollama; Zelo does not do either operation
automatically:

```bash
ollama list
ollama pull qwen3:4b-instruct
ollama pull deepseek-r1:7b
```

The `GET /models` endpoint is authenticated and reports configured aliases and
whether each actual model is installed. On hardware with roughly 8 GiB of
memory and no discrete GPU, local inference may be slow and concurrent model
execution is intentionally bounded to one attempt.

Future cloud providers can implement the same typed `ModelProvider` interface
behind `ModelGateway`; adding one must still pass an explicit product and
security review. v0.3 deliberately ships no cloud provider or API-key path.
