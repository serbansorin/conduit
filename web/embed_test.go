package web

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestAdminHandlerDisablesStaticAssetCaching(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/admin/js/app.js?v=test", nil)
	rec := httptest.NewRecorder()

	AdminHandler().ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want %d", rec.Code, http.StatusOK)
	}
	if got := rec.Header().Get("Content-Type"); !strings.Contains(got, "application/javascript") {
		t.Fatalf("Content-Type = %q, want javascript", got)
	}
	if got := rec.Header().Get("Cache-Control"); !strings.Contains(got, "no-store") {
		t.Fatalf("Cache-Control = %q, want no-store", got)
	}
	if got := rec.Header().Get("Surrogate-Control"); got != "no-store" {
		t.Fatalf("Surrogate-Control = %q, want no-store", got)
	}
}

func TestAdminHandlerDisablesFallbackCaching(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/admin/usage", nil)
	rec := httptest.NewRecorder()

	AdminHandler().ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want %d", rec.Code, http.StatusOK)
	}
	if got := rec.Header().Get("Content-Type"); !strings.Contains(got, "text/html") {
		t.Fatalf("Content-Type = %q, want html", got)
	}
	if got := rec.Header().Get("Cache-Control"); !strings.Contains(got, "no-store") {
		t.Fatalf("Cache-Control = %q, want no-store", got)
	}
}

// TestVendorAssetsAreEmbedded proves the panel's third-party libraries ship
// with the binary. The admin panel must render without reaching out to a CDN,
// otherwise the desktop application breaks when the machine is offline and a
// self-hosted gateway inherits someone else's supply chain.
func TestVendorAssetsAreEmbedded(t *testing.T) {
	for _, name := range []string{"vendor/vue.global.prod.js", "vendor/chart.umd.js"} {
		if _, err := content.ReadFile(name); err != nil {
			t.Fatalf("embedded asset %s is missing: %v", name, err)
		}
	}
}

// TestAdminHandlerServesVendorAssets proves the vendored files are reachable
// under the admin path the panel loads them from.
func TestAdminHandlerServesVendorAssets(t *testing.T) {
	handler := AdminHandler()
	for _, path := range []string{"/admin/vendor/vue.global.prod.js", "/admin/vendor/chart.umd.js"} {
		req := httptest.NewRequest(http.MethodGet, path, nil)
		rec := httptest.NewRecorder()
		handler.ServeHTTP(rec, req)

		if rec.Code != http.StatusOK {
			t.Fatalf("%s: status = %d, want 200", path, rec.Code)
		}
		if ct := rec.Header().Get("Content-Type"); ct != "application/javascript; charset=utf-8" {
			t.Fatalf("%s: content type = %q, want a JavaScript type", path, ct)
		}
		if rec.Body.Len() < 1000 {
			t.Fatalf("%s: body is only %d bytes, expected the vendored library", path, rec.Body.Len())
		}
	}
}
