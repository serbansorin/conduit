# conduit-ai-gateway

Python SDK for [Conduit](https://github.com/serbansorin/conduit), a multi-key LLM gateway that exposes the OpenAI Chat Completions, Anthropic Messages and OpenAI Responses APIs behind a single gateway token.

## Install

```bash
pip install conduit-ai-gateway
```

From a checkout:

```bash
pip install -e .
```

Requires Python 3.9+ and `httpx>=0.27`. Nothing else.

## Quick start

```python
from conduit import Client

client = Client(base_url="http://localhost:9812", api_key="sk-...")

completion = client.chat.completions.create(
    model="glm-5.1",
    messages=[{"role": "user", "content": "Write a haiku about gateways."}],
)

print(completion.choices[0].message.content)
print(completion.usage.total_tokens)
```

`api_key` may be omitted for the public endpoints (`/health`, `/v1/models`).

## Client

```python
Client(
    base_url="http://localhost:9812",
    api_key="sk-...",
    timeout=120.0,
    default_headers=None,   # added to every request
    extra_query=None,       # added to every request
    transport=None,         # httpx transport, useful for tests
)
```

`Client` is a context manager; call `close()` when you are done with it.

### Chat Completions

```python
completion = client.chat.completions.create(
    model="glm-5.1",
    messages=[
        {"role": "system", "content": "You are terse."},
        {"role": "user", "content": "hello"},
    ],
    temperature=0.2,
    max_tokens=256,
)

completion.id            # "chatcmpl-..."
completion.model         # "glm-5.1"
completion.created       # 1730000000
completion.choices[0].message.content
completion.choices[0].message.tool_calls
completion.usage.prompt_tokens
completion.usage.completion_tokens
completion.usage.total_tokens
completion.text          # convenience: first choice content
```

### Streaming

Chunks are yielded as they arrive; the body is never buffered.

```python
for chunk in client.chat.completions.create(
    model="glm-5.1",
    messages=[{"role": "user", "content": "hello"}],
    stream=True,
):
    for choice in chunk.choices:
        if choice.delta.content:
            print(choice.delta.content, end="", flush=True)
```

### Anthropic Messages

`client.messages.create` sends `anthropic-version: 2023-06-01` and `x-api-key`, so Claude Code and the Anthropic SDK can point at the gateway unchanged.

```python
message = client.messages.create(
    model="claude-sonnet-5",
    max_tokens=1024,
    system="You are terse.",
    messages=[{"role": "user", "content": "hello"}],
)

message.content[0].text
message.stop_reason            # "end_turn"
message.usage.input_tokens
message.usage.output_tokens
```

Anthropic-style streaming yields Server-Sent Events:

```python
for event in client.messages.create(
    model="claude-sonnet-5",
    max_tokens=1024,
    messages=[{"role": "user", "content": "hello"}],
    stream=True,
):
    if event.type == "content_block_delta":
        print(event.data["delta"].get("text", ""), end="", flush=True)
```

### Responses

```python
response = client.responses.create(model="glm-5.1", input="hello")

response.output             # list of output items
response.output_text        # convenience text
response.usage.total_tokens
```

Streaming yields the same `StreamEvent` objects (`event.type`, `event.data`).

### Token counting

The gateway computes the estimate locally, so the call is free.

```python
tokens = client.messages.count_tokens(
    model="claude-sonnet-5",
    messages=[{"role": "user", "content": "hello"}],
    system="You are terse.",
)
```

### Catalog and health

```python
catalog = client.models.list()
[catalog.data[0].id]                    # "glm-5.1"
[catalog.data[0].protocol]              # "chat"
[catalog.data[0].context_length]        # 200000

client.health().status                   # "ok"
```

## Async client

`AsyncClient` mirrors `Client` one-to-one.

```python
import asyncio
from conduit import AsyncClient

async def main():
    async with AsyncClient(base_url="http://localhost:9812", api_key="sk-...") as client:
        completion = await client.chat.completions.create(
            model="glm-5.1",
            messages=[{"role": "user", "content": "hello"}],
        )
        print(completion.choices[0].message.content)

        stream = await client.messages.create(
            model="claude-sonnet-5",
            max_tokens=1024,
            messages=[{"role": "user", "content": "hello"}],
            stream=True,
        )
        async for event in stream:
            print(event.type)

asyncio.run(main())
```

## Errors

Every failure raises a subclass of `ConduitError`. The gateway error envelope `{"error": {"type": ..., "message": ...}}` is parsed into the exception message; the admin API envelope `{"error": "..."}` is handled too.

| Exception | Status | Notes |
| --- | --- | --- |
| `AuthenticationError` | 401 | missing, invalid, expired or disabled token |
| `PermissionDeniedError` | 403 | token not allowed to use the model group |
| `NotFoundError` | 404 | unknown resource |
| `BadRequestError` | 400 | invalid request |
| `RateLimitError` | 429 | `.retry_after` holds the `Retry-After` value in seconds |
| `ServerError` | 5xx | gateway or upstream failure |
| `APIConnectionError` | - | the gateway could not be reached |
| `APITimeoutError` | - | the request timed out |

```python
from conduit import RateLimitError

try:
    client.chat.completions.create(model="glm-5.1", messages=messages)
except RateLimitError as exc:
    print(exc.error_type, exc.retry_after)
```

## Admin API

The admin API is password protected. The clients log in on first use and cache the JWT.

```python
from conduit import AdminClient

admin = AdminClient(base_url="http://localhost:9812", password="s3cret")
admin.login()          # returns the JWT
admin.token            # the cached JWT

admin.list_keys(group="go")
admin.create_key("sk-upstream", group="go", label="primary")
admin.update_key(1, weight=3)
admin.toggle_key(1)
admin.reset_key_cooldown(1)
admin.fetch_key_quota(1)
admin.delete_key(1)

admin.list_tokens()
admin.create_token("claude-code", allowed_groups="go", rate_limit=60)
admin.update_token(7, rate_limit=120)
admin.delete_token(7)

admin.list_models()
admin.upsert_model("glm-5.1", protocol="chat", upstream="go", name="GLM 5.1")
admin.update_model("glm-5.1", priority=20)
admin.toggle_model("glm-5.1")
admin.sync_models()
admin.rebuild_models()
admin.delete_model("glm-5.1")

admin.list_model_mappings()
admin.upsert_model_mapping("gpt-4", "glm-5.1")
admin.delete_model_mapping("gpt-4")

admin.stats()
admin.usage_logs(page=1, page_size=25)
admin.health()
admin.version()
```

`AsyncAdminClient` offers the same methods with `await`.

## CLI

The SDK installs a `conduit-cli` command. It is named `conduit-cli` rather
than `conduit` so it never collides with the gateway binary, which uses the
`conduit` name.

```bash
export CONDUIT_BASE_URL=http://localhost:9812
export CONDUIT_API_KEY=sk-...
export CONDUIT_ADMIN_PASSWORD=s3cret

conduit-cli models
conduit-cli chat glm-5.1 "hello" --stream
conduit-cli chat glm-5.1 "hello" --json
conduit-cli ask claude-sonnet-5 "hello"
conduit-cli health
conduit-cli admin keys
conduit-cli admin tokens
conduit-cli admin stats
```

Global flags: `--base-url`, `--api-key`, `--timeout`, `--admin-password`. The command exits non-zero and prints a readable message when the gateway reports an error.

## Development

```bash
pip install -e ".[dev]"
python -m pytest tests/ -q
```

The test suite uses `httpx.MockTransport` and the JSON fixtures under `tests/fixtures/`; no live gateway is required.
