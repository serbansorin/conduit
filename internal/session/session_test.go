package session

import (
	"net/http"
	"testing"
)

func TestFromClientDetectsRecognizedHeaders(t *testing.T) {
	cases := []struct {
		header string
		want   string
	}{
		{"X-Opencode-Session", "abc"},
		{"X-Session-Id", "abc"},
		{"Session-Id", "abc"},
		{"X-Conversation-Id", "abc"},
	}
	for _, tc := range cases {
		h := http.Header{}
		h.Set(tc.header, "abc")
		got, ok := FromClient(h)
		if !ok || got != tc.want {
			t.Fatalf("FromClient(%s) = %q,%v; want %q,true", tc.header, got, ok, tc.want)
		}
	}
}

func TestFromClientIgnoresUnrelatedHeaders(t *testing.T) {
	h := http.Header{}
	h.Set("X-Foo-Bar", "abc")
	if _, ok := FromClient(h); ok {
		t.Fatal("FromClient must not treat an unrelated header as a session id")
	}
}

func TestResolvePrefersClientSession(t *testing.T) {
	h := http.Header{}
	h.Set("X-Session-Id", "client-session")
	got := Resolve(h, []byte(`{"model":"m"}`), "t1")
	if got != "client-session" {
		t.Fatalf("Resolve = %q; want %q", got, "client-session")
	}
}

func TestResolvePrefersOverrideHeader(t *testing.T) {
	h := http.Header{}
	h.Set(OverrideHeader, "explicit")
	h.Set("X-Session-Id", "client-session")
	got := Resolve(h, []byte(`{"model":"m"}`), "t1")
	if got != "explicit" {
		t.Fatalf("Resolve = %q; want %q", got, "explicit")
	}
}

func TestResolveDerivesStableSessionPerConversation(t *testing.T) {
	body := []byte(`{"model":"m","messages":[{"role":"user","content":"hello there"}]}`)
	first := Resolve(http.Header{}, body, "t1")
	second := Resolve(http.Header{}, body, "t1")
	if first != second {
		t.Fatalf("same conversation produced different sessions: %q vs %q", first, second)
	}
	if first == "" || first == "empty" {
		t.Fatalf("Resolve returned %q; want a derived id", first)
	}
}

func TestResolveSeparatesConversations(t *testing.T) {
	a := Resolve(http.Header{}, []byte(`{"messages":[{"role":"user","content":"one"}]}`), "t1")
	b := Resolve(http.Header{}, []byte(`{"messages":[{"role":"user","content":"two"}]}`), "t1")
	if a == b {
		t.Fatalf("different conversations produced the same session %q", a)
	}
}

func TestResolveSeparatesCallers(t *testing.T) {
	body := []byte(`{"messages":[{"role":"user","content":"hello"}]}`)
	a := Resolve(http.Header{}, body, "t1")
	b := Resolve(http.Header{}, body, "t2")
	if a == b {
		t.Fatalf("different callers produced the same session %q", a)
	}
}

func TestResolveHandlesContentBlockArrays(t *testing.T) {
	body := []byte(`{"messages":[{"role":"user","content":[{"type":"text","text":"block text"}]}]}`)
	got := Resolve(http.Header{}, body, "t1")
	if got == "" || got == "empty" || got == "unparsed" {
		t.Fatalf("Resolve = %q; want a derived id from block content", got)
	}
}

func TestApplySetsHeaderWhenClientHasNone(t *testing.T) {
	dst := http.Header{}
	Apply(dst, http.Header{}, []byte(`{"messages":[{"role":"user","content":"hi"}]}`), "t1")
	if dst.Get(Header) == "" {
		t.Fatalf("%s was not set", Header)
	}
}

func TestApplyMirrorsClientSessionOnCanonicalHeader(t *testing.T) {
	client := http.Header{}
	client.Set("X-Session-Id", "client-session")
	dst := http.Header{}
	Apply(dst, client, []byte(`{}`), "t1")
	if got := dst.Get(Header); got != "client-session" {
		t.Fatalf("%s = %q; want %q", Header, got, "client-session")
	}
}
