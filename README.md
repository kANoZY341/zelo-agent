# Zelo Core v0.4

A small, modular foundation for Zelo Agent. It currently provides a FastAPI
application, durable task execution, approvals, a localhost-only Ollama model
gateway, and a small native read-only tool runtime.

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
# {"name":"Zelo Agent","version":"0.4.0","status":"online"}

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
clone, application startup creates the configured workspace root if missing;
create the other reserved directories only when needed. Keep credentials out of
source control.

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

## Native Tool Runtime

v0.4 adds a Zelo-owned, typed runtime for explicitly registered tools:

```text
Task (tool_call) -> ToolGateway -> ToolRegistry -> ToolPolicy -> tool -> typed result
                                                     -> task and audit persistence
```

The initial safe-read registry contains `system_info`, `repo_status`,
`list_workspace_files`, and `read_workspace_file`. Each tool has a stable name,
description, Pydantic input and result model, risk level, approval requirement,
and bounded execution timeout. `GET /tools` is authenticated and exposes only
these safe metadata and input schemas.

The policy supports `safe_read`, `sensitive_read`, `write`, `external`, and
`destructive` risk levels. Only `safe_read` tools execute in v0.4; all other
levels fail closed. Workspace tools accept only relative paths, reject
traversal and symlink escapes, stay under `workspaces/`, do not read contents
while listing, and bound UTF-8 file reads by size. Runtime databases, secrets,
and files outside that root are unavailable.

`ModelGateway` and `ToolGateway` are separate. Models cannot register tools,
call Python functions, choose executables, or create shell commands. Arbitrary
shell, browser, cloud, URL, write, deletion, package-installation, and external
network tools are intentionally unavailable. A future Agent Runtime may decide
when a model requests a registered tool, but v0.4 does not implement an
autonomous model/tool loop.

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
