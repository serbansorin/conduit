package main

import (
	"context"
	"fmt"
	"net/http"
	"net/url"
	"os"
	"strings"

	"github.com/wailsapp/wails/v2/pkg/runtime"
)

// App is the object bound to the frontend and used by the native menu.
type App struct {
	ctx        context.Context
	baseURL    string
	adminURL   string
	adminToken string
	cancel     context.CancelFunc
}

// newApp prepares the application directory, starts the gateway and signs in
// to the admin API so the panel opens already authenticated.
func newApp() (*App, error) {
	dir, err := appDataDir()
	if err != nil {
		return nil, err
	}
	if err := ensureDir(dir); err != nil {
		return nil, err
	}

	creds, err := loadOrCreateCredentials(dir)
	if err != nil {
		return nil, err
	}

	result, cancel, err := startGateway(dir, creds)
	if err != nil {
		return nil, err
	}

	token, err := adminLogin(result.BaseURL, creds.AdminPassword)
	if err != nil {
		cancel()
		return nil, err
	}

	return &App{
		baseURL:    result.BaseURL,
		adminURL:   result.AdminURL,
		adminToken: token,
		cancel:     cancel,
	}, nil
}

// ensureDir creates the application directory when it does not exist.
func ensureDir(dir string) error {
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return fmt.Errorf("create %s: %w", dir, err)
	}
	return nil
}

// startup records the runtime context the menu callbacks use.
func (a *App) startup(ctx context.Context) {
	a.ctx = ctx
}

// shutdown stops the gateway when the window closes.
func (a *App) shutdown(ctx context.Context) {
	if a.cancel != nil {
		a.cancel()
	}
}

// panelURL returns the admin panel address including the session token.
func (a *App) panelURL() string {
	return a.adminURL + "?token=" + url.QueryEscape(a.adminToken)
}

// servePanel answers the single request the window makes on load: a small
// page that hands the browser over to the admin panel running on the local
// gateway.
func (a *App) servePanel(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path != "/" && r.URL.Path != "/index.html" {
		http.NotFound(w, r)
		return
	}

	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")
	_, _ = w.Write([]byte(strings.ReplaceAll(redirectTemplate, "{{URL}}", a.panelURL())))
}

// OpenInBrowser opens the admin panel in the default browser.
func (a *App) OpenInBrowser() {
	runtime.BrowserOpenURL(a.ctx, a.adminURL)
}

// OpenGitHub opens the project repository in the default browser.
func (a *App) OpenGitHub() {
	runtime.BrowserOpenURL(a.ctx, "https://github.com/serbansorin/conduit")
}

// AdminURL returns the address of the admin panel.
func (a *App) AdminURL() string {
	return a.adminURL
}

// BaseURL returns the address of the local gateway.
func (a *App) BaseURL() string {
	return a.baseURL
}

// redirectTemplate is the page the window loads before it hands over to the
// admin panel. The placeholder is replaced with the escaped panel address.
const redirectTemplate = `<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>Conduit</title>
    <meta http-equiv="refresh" content="0; url={{URL}}" />
    <style>
      html, body { height: 100%; margin: 0; }
      body {
        background: #080b10;
        color: #8b98a5;
        display: grid;
        place-items: center;
        font: 14px -apple-system, system-ui, sans-serif;
      }
    </style>
  </head>
  <body>
    <div>Starting Conduit&hellip;</div>
    <script>location.replace({{URL}});</script>
  </body>
</html>
`
