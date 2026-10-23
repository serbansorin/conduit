# protocol

The wire formats and their conversion through the intermediate
representation.

- `types.go` — `IRRequest`, `IRMessage`, `IRContent`, `IRTool`, `IRToolCall`,
  `IRResponse`, `IRChoice`, `IRUsage`, `IRStreamEvent`.
- `chat.go`, `messages.go`, `responses.go` — decode and encode for each
  protocol.
- `convert.go` — `ConvertRequest`, `ConvertResponse`, `StreamConverter`,
  `StreamConverterWithUsage`, `StreamConvertIncremental`.
- `stream_*.go` — SSE encoding per protocol.
- `roles.go`, `tools.go`, `tool_choice.go` — shared enums and helpers.
