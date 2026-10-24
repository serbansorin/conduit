---
name: agentic-crew
description: >-
  Use the agentic-crew agent framework from OpenCode. The crew is a
  collection of specialized AI agents (master, Angular, Ionic, Laravel,
  October CMS, and per-course teachers) each with a private knowledge base.
  Use when the user wants to ask a specialist, ingest documents, or build
  a new agent. Triggers on: crew, agent, specialist, teacher, ask the crew,
  ingest, knowledge base, RAG.
metadata:
  category: development
  suggest_for:
    filename:
      - '*.yaml'
      - '*.yml'
---
# Agentic Crew

A file-based agent crew. The orchestrator is code; every specialist is a
YAML file in `agentic-crew/agents/`. The crew runs as an HTTP service and
is also available via MCP.

## Starting the crew

```bash
cd agentic-crew
pip install -e ../python -e ".[dev]"
export CONDUIT_BASE_URL="http://localhost:9812"
export CONDUIT_API_KEY="sk-..."
agentic-crew serve
```

The service binds to `127.0.0.1:8787` by default.

## Asking a question

```bash
agentic-crew ask "how do I add a form field?" --agent getubetter-october
agentic-crew ask "what is the overall architecture?" --json
```

Or via HTTP:

```bash
curl -X POST http://127.0.0.1:8787/run \
  -H "Content-Type: application/json" \
  -d '{"question": "how do I add a form field?", "agent": "getubetter-october"}'
```

The response includes the answer, the agent that answered, the steps taken,
and the retrieved chunks.

## Ingesting documents

```bash
agentic-crew ingest --agent getubetter-angular
agentic-crew ingest --force
```

Or via HTTP:

```bash
curl -X POST http://127.0.0.1:8787/agents/getubetter-angular/ingest \
  -H "Content-Type: application/json" \
  -d '{"force": false}'
```

## Creating a new agent

```bash
cd agentic-crew
python3 tools/make_agent.py \
  --name my-agent \
  --role "Senior backend engineer" \
  --description "Use for anything in the api.backend service." \
  --capability backend --capability laravel \
  --skill ~/imc-dev/www/getubetter/api.backend/.opencode/skills/laravel-13 \
  --source ~/imc-dev/www/getubetter/api.backend
```

The agent is a YAML file. Drop it in `agents/` and it is picked up on the
next request — no restart needed.

## Skills versus RAG

- **Skills** are instructions folded into the system prompt. The agent always
  has them.
- **RAG** is knowledge retrieved per question. The agent only pays attention
  to what is relevant.

A skill is "how to write an Angular component". A PDF lecture note is "what
the professor said about filters in week six". The first belongs in the
prompt; the second does not.

## MCP integration

The crew exposes an MCP server at `agentic-crew/mcp_server/server.py`. To
use it from OpenCode, add it to your MCP configuration:

```json
{
  "mcpServers": {
    "agentic-crew": {
      "command": "python3",
      "args": ["-m", "mcp_server.server"],
      "cwd": "/path/to/agentic-crew",
      "env": {
        "AGENTIC_CREW_URL": "http://127.0.0.1:8787",
        "AGENTIC_CREW_API_KEY": "your-token"
      }
    }
  }
}
```

Tools exposed: `crew_list_agents`, `crew_ask`, `crew_ingest`, `crew_health`.

## n8n integration

Two importable workflows in `agentic-crew/n8n/`:
- `ask-the-crew.json` — webhook that answers a question through the crew
- `refresh-knowledge-bases.json` — cron that re-ingests every knowledge base

## The available agents

Run `agentic-crew agents` to list them. As of this writing: 5 GetUBetter
specialists, 28 UPB EPiC teacher agents, and 35 Erasmus partner-track
teachers.

## The GetUBetter crew

| Agent | Covers |
| --- | --- |
| `getubetter-master` | Cross-cutting questions, delegates to the others |
| `getubetter-angular` | Components, services, routing, signals, forms |
| `getubetter-ionic` | Capacitor, native plugins, mobile builds |
| `getubetter-laravel` | The api.backend service, Laravel 13 |
| `getubetter-october` | The Imc\Gap plugin, October CMS 3 |

## The teacher crew

One agent per course of the EPiC master (Electric Vehicle Propulsion and
Control) at POLITEHNICA Bucharest. Each teacher owns a private knowledge
base holding that course's material. See `agentic-crew/agents/teachers/CURRICULUM.md`.
