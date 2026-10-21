---
name: conduit-python-sdk
description: >-
  Work on the Conduit Python SDK in python/, the client for the gateway's
  OpenAI, Anthropic and Responses APIs plus its admin API. Use when touching
  python/src/conduit, the conduit-cli command or the pytest suite. Triggers on
  the Python client, AsyncClient, AdminClient, namespace objects, exceptions,
  SDK types or the Python distribution.
metadata:
  category: development
  suggest_for:
    filename:
      - '*.py'
      - '*.toml'
---

# Conduit Python SDK

The SDK lives in `python/` and is a separate distribution from the Go
gateway.

## Distribution facts

`python/pyproject.toml`:

| Field | Value |
| --- | --- |
| distribution name | `conduit-ai-gateway` |
| import package | `conduit` |
| version | `0.1.0` |
| requires-python | `>=3.9` |
| runtime dependency | `httpx>=0.27` |
| dev extra | `pytest>=7.4` |
| console script | `conduit-cli = "conduit.cli:main"` |
| build backend | hatchling |
| wheel packages | `["src/conduit"]` |
| pytest | `testpaths = ["tests"]`, `pythonpath = ["src"]` |

**The distribution is `conduit-ai-gateway`. The console script is
`conduit-cli`.** Neither is `conduit`: `conduit` is the Go server binary, and
`conduit` as an import name is the Python package. Do not rename either.

The package ships a `py.typed` marker, so type checkers use the inline
annotations.

```bash
cd python
python3 -m pip install -e ".[dev]"
python3 -m pytest tests/ -q
```

CI runs exactly that on Python 3.9, 3.12 and 3.13.

## The layout

```
python/
├── pyproject.toml
├── README.md
├── src/conduit/
│   ├── __init__.py       the public surface and __all__
│   ├── _version.py       __version__ = "0.1.0"
│   ├── client.py         Client and the sync namespaces
│   ├── async_client.py   AsyncClient and the async namespaces
│   ├── admin.py          AdminClient and AsyncAdminClient
│   ├── exceptions.py     the exception hierarchy
│   ├── models.py         the response dataclasses
│   ├── types.py          type aliases and TypedDict request params
│   ├── _common.py        internal helpers, not public API
│   ├── _sse.py           internal SSE parsing, not public API
│   ├── cli.py            the conduit-cli command
│   └── py.typed
└── tests/
    ├── conftest.py
    ├── test_client.py
    ├── test_async_client.py
    ├── test_admin.py
    ├── test_cli.py
    ├── test_errors.py
    └── fixtures/
```

`_common.py` and `_sse.py` are underscore-prefixed on purpose. Their docstrings
say they are internal. Do not export them from `__init__.py`.

## The sync client

`client.py` holds `Client` and the namespace classes.

```python
client = Client(base_url="http://localhost:9812", api_key="sk-...")
```

Constructor:

```python
def __init__(
    self,
    base_url: str = DEFAULT_BASE_URL,
    api_key: Optional[str] = None,
    timeout: float = 120.0,
    *,
    default_headers: Optional[Mapping[str, str]] = None,
    extra_query: Optional[Mapping[str, Any]] = None,
    transport: Optional[httpx.BaseTransport] = None,
    user_agent: Optional[str] = None,
) -> None:
```

`DEFAULT_BASE_URL` is `"http://localhost:9812"`, defined in `_common.py`.

The base headers are `Accept: application/json` and
`User-Agent: conduit-python/{__version__}`. The auth header is added per
request:

```python
if self._api_key:
    headers.setdefault("Authorization", "Bearer {0}".format(self._api_key))
    if anthropic:
        headers.setdefault("x-api-key", self._api_key)
if anthropic:
    headers.setdefault("anthropic-version", ANTHROPIC_VERSION)
```

`ANTHROPIC_VERSION` is `"2023-06-01"`. The `anthropic` flag is on for the
Anthropic Messages route only, and it adds `x-api-key` and
`anthropic-version` alongside `Authorization`.

### The namespace objects

Four namespaces hang off the client:

```python
self.chat = ChatNamespace(self)
self.messages = MessagesNamespace(self)
self.responses = ResponsesNamespace(self)
self.models = ModelsNamespace(self)
```

| Attribute | Method | Route | Returns |
| --- | --- | --- | --- |
| `client.chat.completions` | `create(model, messages, *, stream=False, **kwargs)` | `POST /v1/chat/completions` | `ChatCompletion` or an iterator of `ChatCompletionChunk` |
| `client.messages` | `create(model, messages, *, max_tokens=1024, system=None, stream=False, **kwargs)` | `POST /v1/messages` | `AnthropicMessage` or an iterator of `StreamEvent` |
| `client.messages` | `count_tokens(model, messages, *, system=None, tools=None, **kwargs)` | `POST /v1/messages/count_tokens` | `int` |
| `client.responses` | `create(model, input, *, stream=False, **kwargs)` | `POST /v1/responses` | `ResponseObject` or an iterator of `StreamEvent` |
| `client.models` | `list()` | `GET /v1/models` | `ModelList` |

Plus `client.health()` -> `HealthStatus` on `GET /health`, `client.close()`,
and the `with` protocol (`__enter__` / `__exit__`).

`ChatNamespace` is a thin holder whose only job is to carry
`completions`. The nesting mirrors the OpenAI SDK so that code reads the
same way.

A minimal call:

```python
from conduit import Client

client = Client(base_url="http://localhost:9812", api_key="sk-...")
completion = client.chat.completions.create(
    model="glm-5.1",
    messages=[{"role": "user", "content": "hello"}],
)
print(completion.choices[0].message.content)
```

Streaming is the same call with `stream=True`, and the return value becomes
an iterator:

```python
for chunk in client.chat.completions.create(
    model="glm-5.1",
    messages=[{"role": "user", "content": "hello"}],
    stream=True,
):
    print(chunk.choices[0].delta.content or "", end="")
```

## The async client

`async_client.py` mirrors `client.py` exactly. The only differences:

- `transport: Optional[httpx.AsyncBaseTransport] = None` instead of
  `httpx.BaseTransport`.
- Every method is a coroutine.
- A streaming call returns an async iterator.

```python
from conduit import AsyncClient

async with AsyncClient(base_url="http://localhost:9812", api_key="sk-...") as client:
    completion = await client.chat.completions.create(
        model="glm-5.1",
        messages=[{"role": "user", "content": "hello"}],
    )
```

The async namespaces are `AsyncChatNamespace`, `AsyncCompletionsNamespace`,
`AsyncMessagesNamespace`, `AsyncResponsesNamespace` and
`AsyncModelsNamespace`. Same method names, same routes.

## The admin client

`admin.py` holds `AdminClient` and `AsyncAdminClient`.

```python
from conduit import AdminClient

admin = AdminClient(base_url="http://localhost:9812", password="...")
admin.login()   # optional, every method logs in lazily
```

Constructor:

```python
def __init__(
    self,
    base_url: str = DEFAULT_BASE_URL,
    password: Optional[str] = None,
    timeout: float = 30.0,
    *,
    default_headers: Optional[Mapping[str, str]] = None,
    extra_query: Optional[Mapping[str, Any]] = None,
    transport: Optional[httpx.BaseTransport] = None,
    user_agent: Optional[str] = None,
) -> None:
```

The admin timeout is **30.0**, not 120.0 like the LLM clients. Admin calls
are short.

Every route is prefixed with `/admin`.

`login(password=None)` posts to `POST /admin/login` with
`{"password": ...}`, reads the `token` field out of the response and caches
it. When no password is given it falls back to the constructor argument, and
when there is none it raises `ConduitError` telling you to pass `password=`
or set `CONDUIT_ADMIN_PASSWORD`.

Every other method calls `_ensure_token()` first, so the first admin call
logs in for you.

The wrapped methods:

| Method | HTTP | Route |
| --- | --- | --- |
| `login(password=None)` | POST | `/admin/login` |
| `list_keys(group="go")` | GET | `/admin/keys?group={group}` |
| `create_key(value, *, group="go", label="", weight=1, proxy_url="", cookie="", workspace_id="")` | POST | `/admin/keys` |
| `update_key(key_id, **fields)` | PATCH | `/admin/keys/{key_id}` |
| `toggle_key(key_id)` | POST | `/admin/keys/{key_id}/toggle` |
| `reset_key_cooldown(key_id)` | POST | `/admin/keys/{key_id}/reset` |
| `fetch_key_quota(key_id)` | GET | `/admin/keys/{key_id}/quota` |
| `delete_key(key_id)` | DELETE | `/admin/keys/{key_id}` |
| `list_tokens()` | GET | `/admin/tokens` |
| `create_token(name, *, description="", allowed_groups="", rate_limit=0, max_requests=0, expires_at=None)` | POST | `/admin/tokens` |
| `update_token(token_id, **fields)` | PATCH | `/admin/tokens/{token_id}` |
| `delete_token(token_id)` | DELETE | `/admin/tokens/{token_id}` |
| `list_models()` | GET | `/admin/models` |
| `sync_models()` | POST | `/admin/models/sync` |
| `rebuild_models(confirmation="REBUILD")` | POST | `/admin/models/rebuild` |
| `upsert_model(model_id, *, protocol="chat", upstream="go", group="", name=None, real_model=None, context_len=None, status=None, priority=None, tags=None, pricing=None, **extra)` | POST | `/admin/models` |
| `update_model(model_id, **fields)` | PATCH | `/admin/models/{model_id}` |
| `toggle_model(model_id)` | POST | `/admin/models/{model_id}/toggle` |
| `delete_model(model_id)` | DELETE | `/admin/models/{model_id}` |
| `list_model_mappings()` | GET | `/admin/model-mappings` |
| `upsert_model_mapping(source_model, target_model)` | POST | `/admin/model-mappings` |
| `delete_model_mapping(source_model)` | DELETE | `/admin/model-mappings/{source_model}` |
| `stats()` | GET | `/admin/stats` |
| `usage_logs(page=1, page_size=25)` | GET | `/admin/usage?page=...&page_size=...` |
| `health()` | GET | `/admin/health` |
| `version()` | GET | `/admin/version` |
| `backup()` | GET | `/admin/backup` |
| `restore(backup)` | POST | `/admin/restore` |

`AsyncAdminClient` has the same methods, all coroutines.

Note the two path encoders. `_path_id` escapes the whole value, so it is
used for ids. `_path_tail` keeps slashes, so it is used for
`delete_model_mapping`, where the source model id may contain them.

## The exception hierarchy

`exceptions.py`:

```
ConduitError
├── APIError
│   └── APIStatusError
│       ├── BadRequestError      400
│       ├── AuthenticationError  401
│       ├── PermissionDeniedError 403
│       ├── NotFoundError        404
│       ├── RateLimitError       429
│       └── ServerError          5xx
└── APIConnectionError
    └── APITimeoutError
```

`ConduitError.__init__` takes `message` positionally, then the keyword-only
`error_type`, `status_code`, `body`, `headers` and `request_id`.
`APIStatusError` adds `response`. `RateLimitError` adds `retry_after`.

The status mapping lives in `_common.build_api_error`, and transport failures
go through `_common.wrap_transport_error`: a timeout becomes
`APITimeoutError`, anything else becomes `APIConnectionError`.

Catch `ConduitError` for everything. Catch `APIStatusError` when you care
about the HTTP status, and `APITimeoutError` or `APIConnectionError` for
transport problems.

The error envelope the gateway returns is parsed by `_common.parse_error_payload`,
which handles both `{"error": {"type": ..., "message": ...}}` and
`{"error": "..."}`.

## Types and models

`types.py` holds the aliases and the request `TypedDict`s:

- `JSONValue`, `JSONObject`, `JSONArray`
- `Headers`, `QueryParams`, `RequestBody`
- `MessageSequence`
- `ChatMessageParam`, `AnthropicMessageParam`, `ResponseContentParam`, `ResponseInputParam`, `ToolParam`

All the `TypedDict`s are `total=False`.

`models.py` holds plain dataclasses with a `from_dict` classmethod that
ignores unknown keys: `ChatMessage`, `ChatChoice`, `ChatUsage`,
`ChatCompletion`, `ChatChunkChoice`, `ChatCompletionChunk`, `MessageContent`,
`MessageUsage`, `AnthropicMessage`, `StreamEvent`, `ResponseContent`,
`ResponseOutputItem`, `ResponseUsage`, `ResponseObject`, `Model`,
`ModelList`, `TokenCount`, `HealthStatus`, `AdminKey`, `AdminToken`,
`AdminModelRoute`, `ModelMapping`, `UsageLog`, `UsageLogPage`.

`AnthropicStreamEvent` is an alias for `StreamEvent`, not a separate class.

Conveniences worth knowing: `ChatCompletion.text` and `AnthropicMessage.text`
return the concatenated text blocks, and `ModelList` implements `__iter__` and
`__len__`.

`StreamEvent` uses `from_payload(event, payload)` rather than `from_dict`.

## The `conduit-cli` command

```bash
conduit-cli --help
```

Global flags, before the subcommand:

| Flag | Default |
| --- | --- |
| `--base-url` | `$CONDUIT_BASE_URL`, else `http://localhost:9812` |
| `--api-key` | `$CONDUIT_API_KEY` |
| `--timeout` | `120.0` |
| `--admin-password` | `$CONDUIT_ADMIN_PASSWORD` |
| `--version` | prints `conduit-cli {version}` |

Subcommands:

| Command | Arguments | Flags |
| --- | --- | --- |
| `version` | none | none |
| `models` | none | none |
| `health` | none | none |
| `chat` | `model`, `prompt` | `--stream`, `--json`, `--system` |
| `ask` | `model`, `prompt` | `--max-tokens` (default 1024), `--system`, `--stream`, `--json` |
| `admin` | sub-subcommand | `keys`, `tokens`, `stats` |

Exit codes: 0 on success, 1 on a `ConduitError` (the message goes to stderr
as `error: {msg}`), 2 when no command or no admin subcommand was given, 130
on `KeyboardInterrupt`.

One inconsistency to know about: `build_parser()` sets `prog="conduit"` and
its docstring says "the `conduit` command", but the installed script is
`conduit-cli`. The help text says `conduit`. The `models` subcommand also
checks a `--json` flag that it never registers, so that branch is dead. Both
are cosmetic, but do not copy them into new code.

## The tests

```bash
cd python && python3 -m pytest tests/ -q
```

`tests/conftest.py` holds the shared helpers:

| Helper | Purpose |
| --- | --- |
| `fixture_path(name)` | the path of a file under `tests/fixtures/`. |
| `load_text(name)` | read a fixture as text. |
| `load_json(name)` | read a fixture as JSON. |
| `json_response(name, status_code=200)` | an `httpx.Response` from a JSON fixture. |
| `sse_response(name)` | an `httpx.Response` from an SSE fixture. |
| `streaming_response(parts)` | an `httpx.Response` built from raw byte chunks. |
| `recorder` | a pytest fixture that records the requests the client made. |

The tests never touch the network. They pass a `httpx.MockTransport` through
the `transport` constructor argument and assert on what the client sent and
what it parsed.

The async tests drive `asyncio.run(scenario())` directly. There is no
`pytest-asyncio` dependency, so do not add `@pytest.mark.asyncio` markers.

`tests/fixtures/` holds the recorded payloads: `chat_completion.json`,
`chat_stream.txt`, `message.json`, `messages_stream.txt`, `response.json`,
`responses_stream.txt`, `count_tokens.json`, `models.json`, `health.json`,
`admin_login.json`, `admin_keys.json`, `admin_models.json`,
`admin_stats.json`, `admin_tokens.json`, `admin_usage.json`, plus the error
fixtures `error_401.json`, `error_403.json`, `error_404.json`,
`error_429.json` and `error_500.json`.

Where a test goes:

| Change | Test file |
| --- | --- |
| sync client behaviour | `test_client.py` |
| async client behaviour | `test_async_client.py` |
| admin client behaviour | `test_admin.py` |
| the CLI | `test_cli.py` |
| the exception hierarchy and mapping | `test_errors.py` |

## Conventions

- `from __future__ import annotations` at the top of every module. It is what
  makes the 3.9 floor work with modern annotations.
- Type hints everywhere. The package ships `py.typed`.
- No runtime validation dependency. `models.py` uses plain dataclasses.
- The internal helpers live behind an underscore and stay out of
  `__init__.py`.

## Traps

- The distribution is `conduit-ai-gateway`, the script is `conduit-cli`, the
  import is `conduit`. Mixing them up breaks packaging.
- `httpx` is the only runtime dependency. Do not add one without changing
  `pyproject.toml`.
- The admin client uses a 30 second timeout, the LLM clients 120. Do not
  copy one to the other.
- The Anthropic route adds `x-api-key` and `anthropic-version` on top of the
  bearer token. The Chat and Responses routes send only `Authorization`.
- The SDK does not manage the gateway token. It is whatever the caller
  passes as `api_key`.
- Python 3.9 is the floor and CI tests it. No `match` statement, no runtime
  `X | Y` type syntax, no `asyncio.timeout`.
- The async tests use `asyncio.run`. There is no `pytest-asyncio`.
- `agentic-crew` depends on this SDK by its distribution name
  `conduit-ai-gateway`. When you change the packaging here, check that
  project too.

## Verify a change

```bash
cd python
python3 -m pip install -e ".[dev]"
python3 -m pytest tests/ -q
```

Then, from the repository root, make sure the Go side still passes, because
the SDK and the gateway share the wire format:

```bash
export PATH="/opt/homebrew/bin:$PATH"
go build ./... && go test ./...
./scripts/smoke.sh
```
