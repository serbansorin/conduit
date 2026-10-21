package api

import (
	"encoding/json"
	"io"
	"net/http"

	"github.com/gin-gonic/gin"
)

// countTokensRequest is the Anthropic token-counting payload. Only the fields
// that influence the count are modelled; everything else is ignored.
type countTokensRequest struct {
	Model    string            `json:"model"`
	Messages []json.RawMessage `json:"messages"`
	System   json.RawMessage   `json:"system"`
	Tools    json.RawMessage   `json:"tools"`
}

// countTokens handles POST /v1/messages/count_tokens.
//
// Claude Code calls this endpoint before every turn to decide whether the
// conversation still fits the context window. The count is produced locally:
// sending the prompt upstream just to measure it would double the cost of
// every request.
func countTokens(c *gin.Context) {
	body, err := io.ReadAll(c.Request.Body)
	if err != nil {
		writeOpenAIError(c, http.StatusBadRequest, "invalid_request_error", "failed to read request body")
		return
	}
	_ = c.Request.Body.Close()

	var req countTokensRequest
	if err := json.Unmarshal(body, &req); err != nil {
		writeOpenAIError(c, http.StatusBadRequest, "invalid_request_error", "request body must be a JSON object")
		return
	}
	if req.Model == "" {
		writeOpenAIError(c, http.StatusBadRequest, "invalid_request_error", "model is required")
		return
	}

	c.JSON(http.StatusOK, gin.H{"input_tokens": estimateAnthropicTokens(req)})
}

// charsPerToken is the average number of characters one token covers in
// mixed natural-language and code text.
const charsPerToken = 4

// messageOverhead is the per-message structural cost Anthropic charges
// (role marker plus message delimiters).
const messageOverhead = 4

// imageTokenCost approximates the cost of one image block. Anthropic bills
// images by pixel area; without dimensions the payload size is the best
// available proxy.
const imageTokenCost = 1600

// estimateAnthropicTokens approximates the input token count of a Messages
// request. The result is an estimate in the same spirit as Anthropic's own
// counter, which never returns an exact figure either.
func estimateAnthropicTokens(req countTokensRequest) int {
	total := 0

	total += textTokens(req.System)

	for _, raw := range req.Messages {
		var msg struct {
			Content json.RawMessage `json:"content"`
		}
		if err := json.Unmarshal(raw, &msg); err != nil {
			continue
		}
		total += messageOverhead
		total += contentTokens(msg.Content)
	}

	total += rawTokens(req.Tools)

	if total == 0 {
		return 0
	}
	return total
}

// contentTokens counts the tokens of a message body, which is either a plain
// string or a list of typed content blocks.
func contentTokens(raw json.RawMessage) int {
	if len(raw) == 0 {
		return 0
	}
	var asString string
	if err := json.Unmarshal(raw, &asString); err == nil {
		return textTokens(raw)
	}
	var blocks []struct {
		Type string `json:"type"`
		Text string `json:"text"`
	}
	if err := json.Unmarshal(raw, &blocks); err != nil {
		return rawTokens(raw)
	}
	total := 0
	for _, b := range blocks {
		switch b.Type {
		case "image", "image_url":
			total += imageTokenCost
		case "tool_result", "tool_use":
			total += textTokens(json.RawMessage(b.Text))
		default:
			total += textTokens(json.RawMessage(b.Text))
		}
	}
	return total
}

// textTokens counts the tokens of a raw JSON value that carries text.
func textTokens(raw json.RawMessage) int {
	if len(raw) == 0 {
		return 0
	}
	var asString string
	if err := json.Unmarshal(raw, &asString); err == nil {
		return stringTokens(asString)
	}
	var blocks []struct {
		Type string `json:"type"`
		Text string `json:"text"`
	}
	if err := json.Unmarshal(raw, &blocks); err == nil {
		total := 0
		for _, b := range blocks {
			total += stringTokens(b.Text)
		}
		return total
	}
	return rawTokens(raw)
}

// stringTokens converts a text string into an estimated token count.
func stringTokens(s string) int {
	if s == "" {
		return 0
	}
	n := len(s) / charsPerToken
	if n < 1 {
		n = 1
	}
	return n
}

// rawTokens estimates the token cost of an arbitrary JSON payload by its
// serialized size.
func rawTokens(raw json.RawMessage) int {
	if len(raw) == 0 {
		return 0
	}
	return stringTokens(string(raw))
}
