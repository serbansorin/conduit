---
name: conduit-agentic-crew
description: >-
  Work on the Conduit agent framework in agentic-crew/, the LangGraph crew that
  routes questions to specialist agents and answers them from per-agent Qdrant
  collections. Use when touching agentic-crew/src/agentic_crew, the agents/*.yaml
  definitions, the agentic-crew CLI or the FastAPI service n8n calls. Triggers
  on agent definition, specialist, orchestrator, RAG collection, Qdrant, crew
  CLI or the crew HTTP API.
metadata:
  category: development
  suggest_for:
    filename:
      - '*.py'
      - '*.yaml'
---

# Conduit Agentic Crew

`agentic-crew/` is a separate Python distribution that answers questions
through a crew of specialists. It talks to the Conduit gateway for inference
and to a local Qdrant store for retrieval.

## Layout

```
agentic-crew/
├── pyproject.toml
├── README.md
├── .env.example
├── agents/                 the specialists, one YAML file each
│   ├── getubetter-master.yaml
│   ├── getubetter-angular.yaml
│   ├── getubetter-ionic.yaml
│   ├── getubetter-laravel.yaml
│   ├── getubetter-october.yaml
│   └── teachers/
│       ├── README.md
│       └── _template.yaml
├── src/agentic_crew/
│   ├── models.py           the AgentDefinition contract
│   ├── config.py           Settings and load_settings()
│   ├── registry.py         AgentRegistry, loads the YAML files
│   ├── llm.py              ConduitChatModel, the LangChain chat model
│   ├── orchestrator.py     CrewOrchestrator, the LangGraph graph
│   ├── rag.py              retrieval and ingestion
│   ├── store.py            the Qdrant-backed vector store
│   ├── embeddings.py       the embedding backends
│   ├── server.py           the FastAPI service
│   └── cli.py              the agentic-crew command
├── tests/                  conftest.py, test_orchestrator.py, test_rag.py
├── tools/make_teacher.py   scaffold a new teacher agent
├── n8n/                    the n8n workflow definitions
└── data/vectors/           the local Qdrant storage
```

There is no `__init__.py` in `src/agentic_crew/`. It is an implicit namespace
package. Hatchling packs it with `packages = ["src/agentic_crew"]` and pytest
resolves it with `pythonpath = ["src"]`.

`rag.py`, `store.py`, `embeddings.py`, `orchestrator.py`, `server.py` and
`cli.py` were added late and are the moving part. `models.py`, `registry.py`
and the YAML files are the stable contract. When you change one of the newer
modules, re-read it before you rely on a name.

## The architecture

The orchestrator is the only hardcoded piece. Everything else is data, so a
new specialist is a file, not a code change.

`orchestrator.py` builds a LangGraph in `CrewOrchestrator._build_graph`:

```python
builder = StateGraph(CrewState)
builder.add_node("route", self._route)
builder.add_node("retrieve", self._retrieve)
builder.add_node("answer", self._answer)
builder.add_edge(START, "route")
builder.add_edge("route", "retrieve")
builder.add_edge("retrieve", "answer")
builder.add_edge("answer", END)
```

It is a strictly linear graph. There are no conditional edges and no loops.

- `route` picks the specialist and records the choice in `state["steps"]`.
- `retrieve` pulls the relevant chunks from the specialist's private
  collection. A retrieval failure is logged and the run continues with an
  empty context. It never breaks the run.
- `answer` builds the agent's model, builds the prompt with
  `definition.system_prompt(retrieved_context)` and calls it.

`CrewState` is a `TypedDict` with `question`, `agent`, `retrieved_context`,
`answer`, `steps` and `retrieved`.

Public API of `CrewOrchestrator`:

| Member | Purpose |
| --- | --- |
| `graph()` | the compiled LangGraph. |
| `list_agents()` | the catalogue, including the skills and the collection name. |
| `run(question, agent=None)` | returns an `OrchestratorResult`. |
| `stream(question, agent=None)` | yields the graph states as they change. |

Errors: `OrchestratorError`, `UnknownAgentError`, `NoAgentsError`,
`KnowledgeBaseUnavailable`.

The constructor takes `settings`, `registry`, and two optional factories,
`llm_factory` and `knowledge_base_factory`. Those two seams are how the tests
replace the model and the vector store.

### How a specialist is chosen

`CrewOrchestrator._choose_agent` falls back in this order:

1. An explicit `agent=` argument wins.
2. The router model is asked to pick one, preferring a structured output and
   falling back to free text. `_parse_choice` tolerates plain prose, markdown
   fences and JSON under the keys `agent`, `name`, `choice`, `selected` and
   `specialist`.
3. A keyword score, `_score_agent`: the agent name in the question scores
   highest, then a name part, then a capability, then a description token,
   then a role token.
4. The first registered agent, alphabetically.

The router prompt is built from `registry.catalogue()` and carries the
`CATALOGUE_MARKER = "AVAILABLE SPECIALISTS:"` marker.

### `delegates` is not wired up

`AgentDefinition.delegates` is parsed from the YAML and stored, but no runtime
code reads it. There is no agent-to-agent handoff node in the graph. Routing
to a specialist happens because the router model or the keyword scorer picks
it, not because the master declared it in `delegates`.

If you implement handoff, the graph is where it goes: add a node and a
conditional edge after `route`.

## The agent YAML schema

`models.py` `load_definition(path)` parses one file. The top-level keys:

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `name` | str | required | unique id, and the default collection name |
| `description` | str | `""` | when to pick this agent |
| `role` | str | `""` | short role label |
| `model` | str | `"glm-5.3"` | model id as the gateway knows it |
| `instructions` | str | `""` | the system prompt body |
| `temperature` | float | `0.2` | sampling temperature |
| `max_tokens` | int | `4096` | response cap |
| `capabilities` | list[str] | `[]` | routing tags |
| `skills` | list[map] | `[]` | skill directories |
| `rag` | map | `{}` | retrieval config |
| `tools` | list[str] | `[]` | tool names |
| `delegates` | list[str] | `[]` | other agents, currently unused |

Validation: the file must be a YAML mapping, `name` is required and must be
non-empty, `rag` must be a mapping, and every `skills[]` and `rag.sources[]`
entry must have a `path`. A file that fails is skipped, recorded in
`registry.errors`, and never takes the crew down.

A `skills[]` entry:

| Key | Default |
| --- | --- |
| `name` | the directory basename |
| `path` | required |
| `include` | `"**/*.md"` |
| `max_chars` | `12000` |

A `rag.sources[]` entry:

| Key | Default |
| --- | --- |
| `path` | required |
| `include` | `None` |
| `extensions` | `[".md", ".txt", ".pdf", ".html", ".rst"]` |

The `rag` mapping itself:

| Key | Default |
| --- | --- |
| `collection` | `None`, resolved to the agent name |
| `chunk_size` | `900` |
| `chunk_overlap` | `150` |
| `top_k` | `5` |

A real file, `agents/getubetter-master.yaml`:

```yaml
name: getubetter-master
description: "Master agent for the GetUBetter platform. Use for cross-cutting questions about the monorepo, or when it is unclear which sub-project owns the answer."
role: Master engineer for the GetUBetter healthcare platform
model: glm-5.3
temperature: 0.2
max_tokens: 8192

capabilities:
  - getubetter
  - master
  - architecture
  - fullstack
  - monorepo

delegates:
  - getubetter-angular
  - getubetter-ionic
  - getubetter-laravel
  - getubetter-october

skills:
  - name: project context
    path: ~/imc-dev/www/getubetter/.opencode/AGENTS.md
    include: "**/*"
    max_chars: 20000

rag:
  collection: getubetter-master
  chunk_size: 1000
  chunk_overlap: 150
  top_k: 6
  sources:
    - path: ~/imc-dev/www/getubetter/.opencode/AGENTS.md
    - path: ~/imc-dev/www/getubetter/.opencode/skills
      include: "**/*.md"
    - path: ~/imc-dev/www/getubetter/app.gap/.opencode/AGENTS.md
```

## Skills versus RAG

They are declared differently and they are not equivalent.

**RAG** is implemented. The `retrieve` node looks up the specialist's
collection, gets the top `top_k` chunks and passes them to
`system_prompt(retrieved_context)`, which appends them under "Reference
material retrieved from your knowledge base."

**Skills are not folded into the system prompt.** `system_prompt()` builds
the prompt from three things only: `role`, `instructions` and the retrieved
context:

```python
parts: List[str] = []
if self.role:
    parts.append(f"Role: {self.role}")
if self.instructions:
    parts.append(self.instructions.strip())
if retrieved_context.strip():
    parts.append(
        "Reference material retrieved from your knowledge base. "
        "Ground your answer in it and cite the source file when you use it.\n\n"
        + retrieved_context.strip()
    )
return "\n\n".join(parts).strip()
```

The `skills` list is only read in one place, `CrewOrchestrator.list_agents`,
which reports the skill names in the catalogue. `SkillSource.max_chars` and
`SkillSource.expand()` are defined but never consumed. No skill file is ever
read into a prompt.

If you need skills in the prompt, that is the change to make: read the
`include` glob under `SkillSource.expand()`, cap the result at `max_chars`
and append it in `system_prompt()`.

## The registry

`registry.py` holds one class, `AgentRegistry`.

| Member | Purpose |
| --- | --- |
| `AgentRegistry.load(agents_dir)` | classmethod. Walks the directory and loads every agent. |
| `get(name)` | the definition, or `None`. |
| `all()` | every definition, sorted by name. |
| `names()` | the sorted names. |
| `by_capability(capability)` | case-insensitive capability match. |
| `catalogue()` | the list of dicts the router prompt is built from. |
| `add(definition)` | add one at runtime. |
| `extend(definitions)` | add several at runtime. |
| `len()`, `in` | `__len__`, `__contains__`. |

`load` walks `root.rglob("*.y*ml")`, so it is recursive and it picks up
`*.yaml` and `*.yml` in every subdirectory. Files are processed in sorted
order. A duplicate `name` keeps the first definition and records an error. A
file that raises is skipped and recorded in `registry.errors`. A missing
directory warns and returns an empty registry.

Watch out: `agents/teachers/_template.yaml` matches the glob, so it is
loaded as a live agent named `teacher-course-code`. It is the scaffold for a
new teacher, not a template the registry skips.

## The per-agent collection

The collection name is `RagConfig.resolved_collection(agent_name)`:

```python
def resolved_collection(self, agent_name: str) -> str:
    """Return the collection name, defaulting to the agent's name."""
    return self.collection or agent_name
```

There is no prefix, slug or namespace. The name is the explicit
`rag.collection` or, failing that, the agent name verbatim.

All five shipped agents set `rag.collection` explicitly, so their collections
are `getubetter-master`, `getubetter-angular`, `getubetter-ionic`,
`getubetter-laravel` and `getubetter-october`.

Qdrant runs in embedded mode. `store.py` keeps one client per storage
directory and shares it, because embedded Qdrant locks the directory
exclusively. The storage path is `settings.store_path`, which is
`data_dir / "vectors"`, so `agentic-crew/data/vectors`. Collections are
created with a fixed vector size and cosine distance, the size coming from
`settings.embedding_dimensions`, which defaults to `384`.

Chunk ids are stable: a UUID5 hash of the agent name, the source and the
chunk index.

## The Conduit-backed chat model

`llm.py` holds `ConduitChatModel`, a `langchain_core.language_models.chat_models.BaseChatModel`.

Fields: `base_url`, `api_key`, `model`, `temperature`, `max_tokens`,
`timeout`. There is no custom `__init__`.

It calls the gateway through the `conduit` SDK, the same distribution that
lives in `python/`:

```python
from conduit import Client

return Client(
    base_url=self.base_url,
    api_key=self.api_key or None,
    timeout=self.timeout,
)
```

The request goes to the Anthropic Messages endpoint:

```python
payload: Dict[str, Any] = {
    "model": self.model,
    "max_tokens": self.max_tokens,
    "temperature": self.temperature,
    "messages": [_to_anthropic(m) for m in conversation],
}
if stop:
    payload["stop_sequences"] = list(stop)

client = self._client()
response = client.messages.create(**payload)
```

The SDK sets the `Authorization: Bearer` header and, because this is the
Anthropic namespace, the `x-api-key` and `anthropic-version` headers.

One thing to know: the conversion to Anthropic messages maps a `SystemMessage`
to a `user` role. The system prompt therefore travels as the first user
message, not in the Anthropic `system` field. `_split_system` pulls the
system text out of the message list and puts it back as a `SystemMessage`,
which then becomes a user message again.

`build_chat_model(base_url, api_key, model, temperature=0.2, max_tokens=4096)`
is the factory.

## Configuration

`config.py` holds the `Settings` dataclass and `load_settings(overrides)`.

| Env var | Field | Default |
| --- | --- | --- |
| `CONDUIT_BASE_URL` | `conduit_base_url` | `"http://localhost:9812"` |
| `CONDUIT_API_KEY` | `conduit_api_key` | `""` |
| `AGENTIC_CREW_AGENTS_DIR` | `agents_dir` | `"agents"` |
| `AGENTIC_CREW_DATA_DIR` | `data_dir` | `"data"` |
| `AGENTIC_CREW_DEFAULT_MODEL` | `default_model` | `"glm-5.3"` |
| `AGENTIC_CREW_EMBEDDING_MODEL` | `embedding_model` | `"BAAI/bge-small-en-v1.5"` |
| `AGENTIC_CREW_EMBEDDING_BACKEND` | `embedding_backend` | `"fastembed"` |
| `AGENTIC_CREW_EMBEDDING_BASE_URL` | `embedding_base_url` | `""` |
| `AGENTIC_CREW_EMBEDDING_API_KEY` | `embedding_api_key` | `""` |
| `AGENTIC_CREW_EMBEDDING_DIMENSIONS` | `embedding_dimensions` | `384` |
| `AGENTIC_CREW_HOST` | `host` | `"127.0.0.1"` |
| `AGENTIC_CREW_PORT` | `port` | `8787` |
| `AGENTIC_CREW_EXTRA_SKILLS` | `extra_skill_paths` | `[]`, split on `os.pathsep` |
| `AGENTIC_CREW_API_KEY` | read directly in `server.py` | `""` |

The agents directory is resolved in two steps. `load_settings` expands `~`
and leaves the rest relative to the working directory. Then
`Settings.resolved_agents_dir` calls `.resolve()` for an absolute path. The
registry and the server always use `resolved_agents_dir`.

`.env` is read from the working directory by a hand-rolled `_load_dotenv`.
Existing environment variables are not overridden.

`AGENTIC_CREW_API_KEY` is the bearer token for the HTTP service. When it is
empty the service falls back to `settings.conduit_api_key`. When both are
empty the service runs open and says so in the log at startup.

## The CLI

`pyproject.toml` declares the entry point:

```toml
[project.scripts]
agentic-crew = "agentic_crew.cli:main"
```

Six subcommands, plus a global `--verbose`:

| Command | What it does |
| --- | --- |
| `agents` | list the crew. `--json` for machine output. |
| `ask` | ask one question and print the answer. `--agent` forces a specialist. `--json` for machine output. |
| `run` | ask one question and watch the crew work. Same flags as `ask`. |
| `ingest` | build the knowledge bases. `--agent` limits it to one, `--force` rebuilds a collection that already has documents. |
| `serve` | run the HTTP service. `--host` and `--port`. |
| `doctor` | check the setup: the gateway, the token, the agents, the collections and the embeddings. `--json` for machine output. |

There is no `--agents-dir` flag. The agents directory comes from
`AGENTIC_CREW_AGENTS_DIR` or from the working directory.

## The HTTP API

`server.py` holds `create_app(settings=None) -> FastAPI` and
`run_server(settings=None)`. `SERVICE_NAME = "agentic-crew"`.

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `GET` | `/health` | none | liveness and the agent count. |
| `GET` | `/agents` | none | the agent catalogue. |
| `POST` | `/run` | bearer | answer a question. |
| `POST` | `/agents/{name}/ingest` | bearer | rebuild one agent's collection. |
| `GET` | `/` | none | an HTML page with an agent list and an ask form. |

`POST /run` body:

```json
{ "question": "...", "agent": "...", "session_id": "..." }
```

`question` is required with `min_length=1`. `agent` forces a specialist
instead of routing. `session_id` is opaque and echoed back.

The response is `OrchestratorResult.to_dict()` plus `session_id` and
`service`.

`POST /agents/{name}/ingest` body:

```json
{ "force": false }
```

`force` rebuilds the collection even when it already has documents.

Errors are structured. The handlers map `UnknownAgentError` to 404, a
`ValueError` to 400, an `OrchestratorError` to 502 and anything else to 500,
with a body of `{"error": code, "message": message, "status": n}`.

CORS allows the local n8n origins `http://localhost:5678` and
`http://127.0.0.1:5678`, plus any local port matched by
`_LOCAL_ORIGIN_REGEX`.

`n8n/ask-the-crew.json` and `n8n/refresh-knowledge-bases.json` are the
workflow definitions that call these endpoints.

## Verify a change

```bash
cd agentic-crew
python3 -m pip install -e ../python
python3 -m pip install -e ".[dev]"
python3 -m pytest tests/ -q
```

CI does exactly this in the `crew` job, on Python 3.12, and then runs
`python tools/make_teacher.py --help` to validate the tooling.

`tests/conftest.py` holds the shared fixtures. `tests/test_orchestrator.py`
and `tests/test_rag.py` cover the two halves.

## Traps

- `agents/teachers/_template.yaml` is loaded as a live agent named
  `teacher-course-code`. If you do not want that in the catalogue, rename it
  or exclude it from the glob.
- A skill file is never read into a prompt. `max_chars` and `expand()` are
  dead. Do not assume the skill text reached the model.
- `delegates` does not do anything yet. The master agent will answer a
  specialist question itself unless the router picks the specialist.
- The Anthropic `system` field is never used. The system prompt travels as
  the first user message.
- The `agents` directory is resolved relative to the working directory. Run
  the CLI and the server from `agentic-crew/`, or set
  `AGENTIC_CREW_AGENTS_DIR`.
- The crew depends on the local SDK through the `conduit-ai-gateway`
  distribution name. Install `../python` first, as CI does.
- Embedded Qdrant locks the storage directory. Two processes on the same
  `data/vectors` will conflict.
- `rag.sources[].extensions` defaults to `[".md", ".txt", ".pdf", ".html",
  ".rst"]`. A `.pptx` extension is not readable and raises.

## The licence

`agentic-crew/pyproject.toml` declares `AGPL-3.0`, matching the root
project.
