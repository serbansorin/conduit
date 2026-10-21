# agentic-crew

A file-based agent crew. A hardcoded **orchestrator** routes each question to
the right **specialist**, and every specialist owns a private knowledge base it
retrieves from.

The orchestrator is code. Everything else is a YAML file you can drop into
`agents/`.

---

## Why it is built this way

### The models come from Conduit, not from a provider

Every LLM call in this package goes through a [Conduit](../../README.md)
gateway. Nothing here talks to an OpenAI, Anthropic or OpenCode endpoint
directly. That means the crew inherits the gateway's key pooling, failover,
per-token rate limits and cost accounting for free — and you can point the
whole crew at a different model by changing one line in the agent YAML.

The gateway speaks the Anthropic Messages API, so `llm.py` adapts it to a
LangChain chat model. Everything above that is ordinary LangChain.

### LangGraph for orchestration, not CrewAI

The obvious alternative is CrewAI, whose `Agent` / `Task` / `Crew` vocabulary
maps neatly onto "a master agent and its workers". We deliberately did not use
it:

| Requirement | Why LangGraph fits |
| --- | --- |
| Specialists are discovered **at runtime** from a folder | A crew is normally defined in Python at import time. A graph node can consult a registry that is loaded from disk, so adding a specialist is a file drop, not a code change. |
| The master hands work to a specialist | A conditional edge is the whole mechanism. In CrewAI this needs a custom process subclass. |
| The master may answer directly, or delegate | That is a branch on the graph, with the master's own model choosing. |
| The roster changes while the service runs | The registry is re-read per request; the graph itself never changes. |
| State survives a crash | LangGraph checkpoints come for free. |

CrewAI would have needed a fair amount of its abstractions re-implemented to
support a filesystem registry with per-agent retrieval, at which point the
framework was in the way rather than helping.

### One vector store, one collection per agent

Each agent gets its own Qdrant collection inside a single embedded store. The
store is a directory on disk, not a server, so there is nothing to run and
nothing to secure. Qdrant's embedded mode is used through
`langchain-qdrant`.

Agents are isolated by collection, not by process: a teacher agent cannot
retrieve another teacher's lecture notes unless it is configured to.

### Local embeddings, cheap generation

Embeddings run locally through `fastembed` (ONNX, no PyTorch, no GPU). The
default model is `BAAI/bge-small-en-v1.5` at 384 dimensions.

Generation goes to whatever model the agent names in its YAML. Routing uses
the cheap default model; the specialist uses its own, which is where the
quality matters. This is the split that keeps a crew affordable: you pay for a
strong model only on the turn that actually needs one.

For a big course catalogue, `deepseek-v4.1-flash` is a sensible default for
both — cheap, and strong enough for tutoring questions grounded in retrieved
material.

---

## Layout

```
agentic-crew/
├── pyproject.toml
├── src/agentic_crew/
│   ├── config.py       settings from the environment
│   ├── models.py       the agent YAML schema
│   ├── registry.py     loads agents/ into a lookup
│   ├── llm.py          Conduit-backed LangChain chat model
│   ├── embeddings.py   local or API embeddings
│   ├── store.py        Qdrant vector store
│   ├── rag.py          per-agent knowledge base
│   ├── orchestrator.py the LangGraph master
│   ├── server.py       HTTP API, for n8n and other automation
│   └── cli.py          the agentic-crew command
├── agents/             ← your specialists live here
│   ├── getubetter-master.yaml
│   ├── getubetter-angular.yaml
│   ├── getubetter-ionic.yaml
│   ├── getubetter-laravel.yaml
│   ├── getubetter-october.yaml
│   └── teachers/       one file per course
└── data/               the vector store and ingested documents
```

---

## Writing an agent

An agent is a YAML file. This is a complete one:

```yaml
name: my-agent
description: "When to pick this agent. The router reads this."
role: Senior something
model: glm-5.3
temperature: 0.2
max_tokens: 8192

capabilities: [frontend, angular]

# Optional: this agent may hand work to others.
delegates:
  - another-agent

# Skill folders folded into the system prompt.
skills:
  - name: angular component
    path: ~/projects/app/.opencode/skills/angular-component
    include: "**/*.md"
    max_chars: 12000

# The private knowledge base.
rag:
  collection: my-agent
  chunk_size: 900
  chunk_overlap: 150
  top_k: 6
  sources:
    - path: ~/projects/app/.opencode/skills
      include: "**/*.md"
    - path: ~/documents/course
      extensions: [".pdf", ".md"]
```

Only `name` is required. `description` matters most — the router chooses
between agents using it.

### Skills versus RAG

They are two different mechanisms, on purpose:

- **Skills** are instructions. They are read as text and folded into the
  system prompt, so the agent always has them.
- **RAG** is knowledge. It is chunked, embedded and retrieved per question, so
  the agent only pays attention to what is relevant.

A skill is "how to write an Angular component". A PDF lecture note is "what
the professor said about filters in week six". The first belongs in the prompt;
the second does not.

---

## Usage

```bash
pip install -e .

export CONDUIT_BASE_URL="http://localhost:9812"
export CONDUIT_API_KEY="sk-..."

agentic-crew agents                  # list the crew
agentic-crew doctor                  # check the setup
agentic-crew ingest --agent my-agent # build the knowledge base
agentic-crew ask "how do I add a form field?" --agent my-agent
agentic-crew serve                   # the HTTP API
```

### The HTTP API

For n8n and anything else that speaks HTTP:

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health` | liveness and the agent count |
| `GET` | `/agents` | the agent catalogue |
| `POST` | `/run` | `{"question": "...", "agent": "optional"}` |
| `POST` | `/agents/{name}/ingest` | `{"force": false}` |

Set `AGENTIC_CREW_API_KEY` to require a bearer token on the mutating
endpoints.

### n8n

A typical flow is a **Webhook** node pointing at `POST /run`, with the
question taken from the incoming payload:

```json
{ "question": "{{$json.question}}", "agent": "{{$json.agent}}" }
```

To keep documents in step with a folder, a **Schedule Trigger** node calls
`POST /agents/{name}/ingest` on a cron. The ingest is idempotent, so running
it often costs nothing.

---

## The GetUBetter crew

`agents/getubetter-*.yaml` targets the healthcare platform in
`~/imc-dev/www/getubetter/`. It has three co-evolving sub-projects, and the
crew mirrors them:

| Agent | Covers | Knowledge base |
| --- | --- | --- |
| `getubetter-master` | cross-cutting questions about the monorepo | the root `AGENTS.md` and all four sub-project skill sets |
| `getubetter-angular` | components, services, routing, signals, forms | the Angular skills and the app's `AGENTS.md` |
| `getubetter-ionic` | Capacitor, native plugins, mobile builds | the mobile-facing skills |
| `getubetter-laravel` | the `api.backend` service and the Laravel side | the Laravel 13, scribe and domain skills |
| `getubetter-october` | the `Imc\Gap` plugin, models, form widgets | the October CMS skills and the plugin blueprint |

The master declares the other four as `delegates`, so a question about a form
widget reaches the October specialist instead of being answered from general
knowledge.

There are about 65 skill files across the four sub-projects. Ingesting them
gives the crew the project's own conventions, not just its documentation.

---

## The teacher crew

`agents/teachers/` holds one agent per course, each with its own collection
pointing at that course's material. A teacher agent is a good example of the
pattern: its instructions set the teaching style, and its collection holds the
slides and lecture notes it teaches from.

```yaml
name: teacher-signals
description: "Signals and systems. Use for anything about filters, spectra or sampling."
role: University lecturer in signals and systems
model: deepseek-v4.1-flash
temperature: 0.3

rag:
  collection: teacher-signals
  sources:
    - path: ~/university/evpc/signals
      extensions: [".pdf", ".md"]
```

Drop a PDF in the folder, run `agentic-crew ingest --agent teacher-signals`,
and the teacher can now answer from it and cite the page.

---

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `CONDUIT_BASE_URL` | `http://localhost:9812` | the gateway |
| `CONDUIT_API_KEY` | — | the gateway token |
| `AGENTIC_CREW_AGENTS_DIR` | `agents` | where the YAML files live |
| `AGENTIC_CREW_DATA_DIR` | `data` | the vector store |
| `AGENTIC_CREW_DEFAULT_MODEL` | `glm-5.3` | the routing model |
| `AGENTIC_CREW_EMBEDDING_BACKEND` | `fastembed` | `fastembed` or `openai` |
| `AGENTIC_CREW_EMBEDDING_MODEL` | `BAAI/bge-small-en-v1.5` | the embedding model |
| `AGENTIC_CREW_EMBEDDING_BASE_URL` | — | for an OpenAI-compatible embedding API |
| `AGENTIC_CREW_API_KEY` | — | bearer token for the HTTP API |
| `AGENTIC_CREW_HOST` / `AGENTIC_CREW_PORT` | `127.0.0.1` / `8787` | the HTTP service |
| `AGENTIC_CREW_EXTRA_SKILLS` | — | extra skill folders, `:`-separated |

---

## Licence

AGPL-3.0, the same as the rest of the project.
