# Zelo Core v0.5.0 — Agent Runtime

A small, modular foundation for Zelo Agent. It currently provides a FastAPI
application, durable task execution, approvals, a localhost-only Ollama model
gateway, native read-only tools, and a bounded Agent Runtime.

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
# Set ZELO_OPERATOR_TOKEN to a private random token (32–512 ASCII characters).
# Stop any process using this database before applying migrations.
python -m alembic upgrade head
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Stop with `Ctrl+C`. Keep the host set to `127.0.0.1`; this version is intended
only for local access. Interactive API documentation is at
<http://127.0.0.1:8000/docs> while the server is running.

```bash
curl http://127.0.0.1:8000/
# {"name":"Zelo Agent","version":"0.5.0","status":"online"}

curl http://127.0.0.1:8000/health
# {"status":"healthy"}
```

`/health` checks database and runner readiness. Model calls are available only through Ollama on `127.0.0.1`; there is no cloud,
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
cloud providers, downloads models, or silently falls back. `ai_prompt` calls require
operator approval and return text. Authenticated `agent_run` tasks use the separate
bounded proposal loop described below.

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
network tools are intentionally unavailable. The v0.5 Agent Runtime validates
model proposals and routes approved capabilities through this same gateway.

## Agent Runtime

The model proposes; Zelo decides; Zelo executes. `ModelGateway` owns provider
access and deterministic model routing. `ToolGateway` owns typed tool execution
and `ToolPolicy`. `AgentRuntime` owns one attempt's in-memory conversation,
structured proposals, budgets, and the handoff between these gateways. The task
engine owns persistence, cancellation, retry, and audit history.

```text
authenticated POST /tasks (agent_run)
  -> AgentRuntime -> ModelGateway -> local general model
  -> validate one action
     final     -> persist final result and complete the attempt
     tool_call -> ToolRegistry -> typed arguments -> ToolPolicy -> ToolGateway
               -> bounded, untrusted tool output -> next model step
```

Use the existing tasks API; no new endpoint is required:

```json
{
  "kind": "agent_run",
  "payload": {
    "prompt": "Check the current Zelo repository status and tell me what branch I am on.",
    "profile": "general"
  },
  "timeout_seconds": 120
}
```

`general` is the only allowed agent profile and is also the default. It selects
`local_general` (`qwen3:4b-instruct` by default). Agent runs never automatically
invoke the reasoning/DeepSeek profile. The selected model is persisted at submission
and checked on each model step; changing its alias mapping fails the attempt.

Every model response must be exactly one Pydantic-validated JSON action:

```json
{"type": "tool_call", "tool": "repo_status", "arguments": {}}
```

or:

```json
{"type": "final", "content": "Your current branch is ..."}
```

Extra fields, unknown action types, duplicate JSON keys, non-finite numbers,
multiple actions, prose, and markdown/code fences are rejected. No command is
extracted from arbitrary text. Tool metadata comes from `ToolRegistry`, filtered
by `ToolPolicy`, and every execution passes through `ToolGateway`. The current
four tools are `system_info`, `repo_status`, `list_workspace_files`, and
`read_workspace_file`. Only safe-read tools can execute without further operator
approval. Other risks and approval-required tools fail closed. A future approval
flow belongs at this policy/gateway boundary; an agent never bypasses it.

| Setting | Default | Bound |
| --- | --- | --- |
| `ZELO_MAX_AGENT_STEPS` | 6 | 1–20 model calls, including parse retries |
| `ZELO_MAX_AGENT_TOOL_CALLS` | 4 | 0–16 executed tool calls |
| `ZELO_AGENT_TIMEOUT_SECONDS` | 120 | 0.01–600 seconds per attempt |
| `ZELO_MODEL_TIMEOUT_SECONDS` | 60 | 0.01–600 seconds per model call |
| `ZELO_MAX_MODEL_PARSE_RETRIES` | 1 | 0–2 retries total per attempt |
| `ZELO_MAX_AGENT_TOOL_RESULT_CHARS` | 8192 | 256–32768 serialized result characters |

The earlier of the task timeout and configured agent timeout ends the run. Tools
use their registered timeout (currently five seconds). The final model step must
produce a final answer; a tool proposal at that step fails without executing it.
Ollama action generation requests JSON output, disables thinking, and caps
generation at 2048 tokens. Actions are additionally limited to 32768 characters;
Zelo validates the complete Pydantic action schema before any execution. Final
answers are limited to 16384 characters. There is no infinite retry or automatic model
download. A timeout stops the loop and discards late results. Existing synchronous
safe-read tools run in a thread; Python cannot forcibly terminate that thread, so
an already-running read may finish after timeout. No further model or tool step
is scheduled for that attempt.

Tool output has a distinct `tool` conversation role and a Zelo-generated envelope:
`type=tool_output`, `untrusted=true`, tool name, bounded `result_json`, and a
`truncated` flag. The serialized result is escaped as data, never interpolated
into the system prompt. A truncated prefix may be incomplete JSON. Envelope and
escaping overhead are outside the content budget but remain bounded. The system
prompt tells the model to treat file contents as evidence rather than instructions.
This does not guarantee that a model ignores every injected instruction; registry,
typed argument, policy, and workspace checks enforce permissions regardless of
what it proposes. There is no arbitrary shell, direct Python callable access,
browser access, cloud fallback, autonomous write, deletion, or package installation.

Results contain `final_response`, `alias`, `actual_model`, `model_steps`,
`tool_calls`, unique `tools_used` in first-use order, per-step token `usage`, and
`completed_at`. Private model thinking fields and internal conversation transcripts
are not persisted. The task stores the original request and final result as before.
Audit events record `agent_started`, `agent_model_step`, `agent_tool_requested`,
`agent_tool_completed`, `agent_parse_failed`, `agent_limit_reached`, and a terminal
`agent_completed` or `agent_failed`, linked to the attempt. They omit full prompts,
model output, tool arguments, file contents, and raw exception details. Completion
and failure events commit atomically with the attempt outcome.

Provider, protocol, tool, policy, and budget failures use existing failed attempts.
`POST /tasks/{id}/retry` starts a new bounded execution with fresh conversation and
counters; it preserves previous attempts and does not resume a hidden reasoning
loop. Process restart marks interrupted runs without replaying them.

Migration `0004_agent_runtime` only extends the task-kind CHECK constraint. It
uses the existing atomic SQLite/Alembic transaction and foreign-key integrity
checks; no conversation table or other persistence schema is added.

Automated tests use fake providers and require no Ollama. To run the real local
smoke test with an already-installed `qwen3:4b-instruct`:

```bash
.venv/bin/python -m scripts.smoke_agent
```

It creates an isolated temporary database, starts Zelo on an ephemeral
`127.0.0.1` port, submits the repository-status request above through HTTP, checks
the persisted result and audit events, then stops the server and cleans up its
temporary database. It never downloads a model or invokes DeepSeek.

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
