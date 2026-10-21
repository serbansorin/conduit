// Package session resolves the upstream session identifier that the OpenCode
// Go backend requires on every request.
//
// The upstream answers requests that carry no session header with
// "MissingSessionID" and refuses to route them. It accepts several header
// spellings and uses the value to pin a conversation to the same backend
// instance, which is what makes prompt caching work across turns.
//
// Resolution order:
//
//  1. A session header supplied by the client (any recognised spelling) is
//     forwarded verbatim, preserving the client's own conversation id.
//  2. An explicit X-Conduit-Session header always wins over anything else.
//  3. Otherwise an id is derived from the conversation seed, so every turn of
//     the same conversation maps to the same upstream session.
package session

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"net/http"
	"strings"
)

// Header is the canonical header the upstream recognises for session routing.
const Header = "X-Opencode-Session"

// OverrideHeader is the gateway-specific header a client can set to pin its
// own session id explicitly. It takes precedence over a derived id but not
// over a client-supplied upstream session header.
const OverrideHeader = "X-Conduit-Session"

// clientSessionHeaders lists the header spellings the upstream accepts from a
// client. If the inbound request carries any of them we forward the value
// untouched rather than inventing one, so the client's own conversation id
// survives the hop.
var clientSessionHeaders = []string{
	"X-Opencode-Session",
	"X-Session-Id",
	"Session-Id",
	"X-Conversation-Id",
	"Conversation-Id",
}

// FromClient returns the session id carried by the inbound request headers and
// reports whether one was found.
func FromClient(h http.Header) (string, bool) {
	for _, name := range clientSessionHeaders {
		if v := strings.TrimSpace(h.Get(name)); v != "" {
			return v, true
		}
	}
	return "", false
}

// Resolve determines the session id to send upstream.
//
// scope identifies the caller (typically the gateway token id) so that two
// different tenants never collide on the same derived session id. body is the
// request payload, used to derive a stable conversation seed.
func Resolve(clientHeaders http.Header, body []byte, scope string) string {
	if v := strings.TrimSpace(clientHeaders.Get(OverrideHeader)); v != "" {
		return v
	}
	if v, ok := FromClient(clientHeaders); ok {
		return v
	}
	return derive(conversationSeed(body), scope)
}

// derive builds a deterministic session id from the conversation seed and the
// caller scope. The same conversation always yields the same id, which is
// what lets the upstream reuse its prompt cache between turns.
func derive(seed, scope string) string {
	sum := sha256.Sum256([]byte(scope + "|" + seed))
	return "conduit-" + hex.EncodeToString(sum[:16])
}

// Apply sets the session header on the outbound request.
//
// dst is the outbound header set, clientHeaders the inbound request headers,
// body the request payload and scope the caller scope. When the client already
// supplied a session id the corresponding header is left untouched so the
// upstream sees exactly what the client sent.
func Apply(dst, clientHeaders http.Header, body []byte, scope string) {
	if v, ok := FromClient(clientHeaders); ok {
		// Mirror the client's value onto the canonical header as well, so
		// the upstream always sees a session no matter which spelling it
		// prefers to look at.
		dst.Set(Header, v)
		return
	}
	dst.Set(Header, Resolve(clientHeaders, body, scope))
}

// conversationSeed extracts a stable identifier for the conversation from a
// request body. It looks at the first user message across the three supported
// wire formats and returns a truncated digest.
//
// The seed is intentionally derived from the *first* user message rather than
// the whole body: every turn of a conversation replays the same history, so
// the first message is the one part that stays constant while later turns
// append new content.
func conversationSeed(body []byte) string {
	if len(body) == 0 {
		return "empty"
	}
	var payload map[string]any
	if err := json.Unmarshal(body, &payload); err != nil {
		return "unparsed"
	}
	if s := seedFromMessages(payload["messages"]); s != "" {
		return s
	}
	if s := seedFromMessages(payload["input"]); s != "" {
		return s
	}
	if s, ok := payload["input"].(string); ok && strings.TrimSpace(s) != "" {
		return digest(s)
	}
	return "empty"
}

// seedFromMessages walks a messages array and returns the digest of the first
// user-authored text it finds.
func seedFromMessages(raw any) string {
	items, ok := raw.([]any)
	if !ok {
		return ""
	}
	for _, item := range items {
		msg, ok := item.(map[string]any)
		if !ok {
			continue
		}
		role, _ := msg["role"].(string)
		if role != "user" && role != "" {
			continue
		}
		if text := contentText(msg["content"]); text != "" {
			return digest(text)
		}
	}
	return ""
}

// contentText flattens the several shapes a message body can take into plain
// text: a bare string, or an array of typed content blocks.
func contentText(raw any) string {
	switch v := raw.(type) {
	case string:
		return strings.TrimSpace(v)
	case []any:
		var b strings.Builder
		for _, block := range v {
			bm, ok := block.(map[string]any)
			if !ok {
				continue
			}
			if t, ok := bm["text"].(string); ok {
				b.WriteString(t)
				b.WriteByte('\n')
			}
		}
		return strings.TrimSpace(b.String())
	default:
		return ""
	}
}

// digest returns a short, stable hash of the seed text.
func digest(text string) string {
	sum := sha256.Sum256([]byte(text))
	return hex.EncodeToString(sum[:16])
}
