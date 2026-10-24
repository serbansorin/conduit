// Package server wires the Conduit gateway together and runs it.
//
// The bootstrap lives here rather than in main so the same wiring can be
// reused by the command line entry point and by the desktop application,
// which runs the gateway in-process.
package server

import (
	"context"
	"fmt"
	"log"
	"net"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/serbansorin/conduit/admin"
	"github.com/serbansorin/conduit/api"
	"github.com/serbansorin/conduit/config"
	"github.com/serbansorin/conduit/modelsync"
	"github.com/serbansorin/conduit/pool"
	"github.com/serbansorin/conduit/store"
	"github.com/serbansorin/conduit/upstream"
	"github.com/serbansorin/conduit/web"
)

// Options configures a gateway instance.
type Options struct {
	// Port is the TCP port to listen on. Zero picks a free port.
	Port int
	// UseRandomPort asks the OS for an unused port instead of using the
	// configured one. The desktop application sets it so several instances
	// never collide.
	UseRandomPort bool
	// Bind is the interface to listen on. Empty binds every interface.
	Bind string
	// AsyncModelSync runs the startup catalog sync in the background instead
	// of blocking startup. The desktop application sets this so the window
	// opens immediately.
	AsyncModelSync bool
	// SkipModelSync disables the catalog sync entirely.
	SkipModelSync bool
	// OnReady is called once the listener is accepting connections. The
	// desktop application uses it to learn the port before the UI starts.
	OnReady func(*Result)
}

// Result reports what a running gateway exposed.
type Result struct {
	// Addr is the address the gateway is listening on.
	Addr string
	// BaseURL is the URL clients should use.
	BaseURL string
	// AdminURL is the URL of the admin panel.
	AdminURL string
	// Token is the bootstrap gateway token, set only when one was created.
	Token string
}

// Run starts the gateway and blocks until ctx is cancelled or the process
// receives SIGINT/SIGTERM.
func Run(ctx context.Context, opts Options) (*Result, error) {
	if err := config.ValidateSecurity(); err != nil {
		return nil, fmt.Errorf("security check failed: %w", err)
	}
	if err := store.Init(); err != nil {
		return nil, fmt.Errorf("store init failed: %w", err)
	}

	bootstrapToken := ensureBootstrapToken()
	ensureBootstrapKey()
	loadModelRoutes()
	loadModelMappings()

	if !opts.SkipModelSync {
		syncModels(ctx, opts.AsyncModelSync)
	}

	if os.Getenv("GIN_MODE") == "" {
		gin.SetMode(gin.ReleaseMode)
	}

	picker := pool.NewPicker()
	root := api.NewRouter(picker)
	admin.MountWithPicker(root.Group("/admin"), picker)

	root.GET("/admin", gin.WrapH(web.AdminHandler()))
	root.NoRoute(func(c *gin.Context) {
		if strings.HasPrefix(c.Request.URL.Path, "/admin") {
			web.AdminHandler().ServeHTTP(c.Writer, c.Request)
			return
		}
		c.Writer.WriteHeader(http.StatusNotFound)
	})

	listener, err := listen(opts)
	if err != nil {
		return nil, err
	}

	srv := &http.Server{
		Handler:           root,
		ReadHeaderTimeout: 10 * time.Second,
		IdleTimeout:       120 * time.Second,
		MaxHeaderBytes:    1 << 20,
	}

	cfg := config.Get()
	log.Printf("conduit listening on %s", listener.Addr().String())
	log.Printf("  go  base url : %s", cfg.GoBaseURL)
	log.Printf("  ollama cloud: %s", cfg.OllamaBaseURL)
	log.Printf("  models       : %d registered", len(config.AllModels()))
	log.Printf("  mappings     : %d configured", len(config.AllModelMappings()))

	errCh := make(chan error, 1)
	go func() {
		if err := srv.Serve(listener); err != nil && err != http.ErrServerClosed {
			errCh <- err
		}
	}()

	baseURL := "http://" + listener.Addr().String()
	result := &Result{
		Addr:     listener.Addr().String(),
		BaseURL:  baseURL,
		AdminURL: baseURL + "/admin",
		Token:    bootstrapToken,
	}
	if opts.OnReady != nil {
		opts.OnReady(result)
	}

	select {
	case err := <-errCh:
		return result, err
	case <-ctx.Done():
	case <-shutdownSignal():
		log.Printf("shutting down server...")
	}
	shutdownCtx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	if err := srv.Shutdown(shutdownCtx); err != nil {
		log.Printf("server forced shutdown: %v", err)
	}
	upstream.CloseIdleConnections()
	log.Printf("server exited")
	return result, nil
}

// listen opens the TCP listener, picking a free port when none is given.
func listen(opts Options) (net.Listener, error) {
	port := opts.Port
	if port == 0 && !opts.UseRandomPort {
		if v := config.Get().Port; v != "" {
			if n, err := net.LookupPort("tcp", v); err == nil {
				port = n
			}
		}
	}
	if port == 0 && !opts.UseRandomPort {
		port = 9812
	}
	addr := net.JoinHostPort(opts.Bind, fmt.Sprintf("%d", port))
	listener, err := net.Listen("tcp", addr)
	if err != nil {
		return nil, fmt.Errorf("listen on %s: %w", addr, err)
	}
	return listener, nil
}

// shutdownSignal returns a channel that fires on SIGINT or SIGTERM.
func shutdownSignal() <-chan os.Signal {
	quit := make(chan os.Signal, 1)
	signal.Notify(quit, syscall.SIGINT, syscall.SIGTERM)
	return quit
}

// syncModels runs the catalog sync, optionally in the background.
func syncModels(ctx context.Context, async bool) {
	sync := func() {
		result, err := modelsync.Sync(ctx, modelsync.Options{})
		if err != nil {
			log.Printf("warn: model catalog sync failed: %v", err)
			return
		}
		log.Printf("synced model catalog: opencode=%d ollama=%d openrouter=%d matched=%d created=%d updated=%d warnings=%v",
			result.OpenCodeCount, result.OllamaCount, result.OpenRouterCount, result.MatchedCount, result.CreatedCount, result.UpdatedCount, result.Warnings)
		config.SeedFamilyAliases()
	}

	if async {
		go sync()
		go func() {
			modelsync.StartBackground(ctx, 6*time.Hour, modelsync.Options{}, func(result modelsync.Result, err error) {
				if err != nil {
					log.Printf("warn: background model catalog sync failed: %v", err)
					return
				}
				log.Printf("background model catalog synced: opencode=%d ollama=%d matched=%d created=%d updated=%d warnings=%v",
					result.OpenCodeCount, result.OllamaCount, result.MatchedCount, result.CreatedCount, result.UpdatedCount, result.Warnings)
				config.SeedFamilyAliases()
			})
		}()
		return
	}

	sync()
	modelsync.StartBackground(ctx, 6*time.Hour, modelsync.Options{}, func(result modelsync.Result, err error) {
		if err != nil {
			log.Printf("warn: background model catalog sync failed: %v", err)
			return
		}
		log.Printf("background model catalog synced: opencode=%d ollama=%d matched=%d created=%d updated=%d warnings=%v",
			result.OpenCodeCount, result.OllamaCount, result.MatchedCount, result.CreatedCount, result.UpdatedCount, result.Warnings)
		config.SeedFamilyAliases()
	})
}

// ensureBootstrapToken creates a default gateway token on first run and prints
// it to stdout so the operator can use it immediately.
func ensureBootstrapToken() string {
	ts, err := pool.AllTokens()
	if err != nil {
		log.Printf("warn: cannot load tokens: %v", err)
		return ""
	}
	if len(ts) > 0 {
		return ""
	}
	t, err := pool.CreateToken("bootstrap", "", 0, nil)
	if err != nil {
		log.Printf("warn: cannot create bootstrap token: %v", err)
		return ""
	}
	fmt.Println("==================================================")
	fmt.Println(" Bootstrap gateway token created:")
	fmt.Printf("   %s\n", t.Token)
	fmt.Println(" Use this as the api key in opencode / clients.")
	fmt.Println("==================================================")
	return t.Token
}

// ensureBootstrapKey auto-adds an OpenCode Go upstream key from the GO_API_KEY
// environment variable on first run. It is idempotent: if a key with that
// value already exists in the "go" group, nothing happens. Manual keys added
// through the admin panel are never touched.
func ensureBootstrapKey() {
	keyValue := strings.TrimSpace(config.Get().GoAPIKey)
	if keyValue == "" {
		return
	}
	keys, err := pool.AllByGroup("go")
	if err != nil {
		log.Printf("warn: cannot load keys: %v", err)
		return
	}
	for _, k := range keys {
		if k.Value == keyValue {
			return // already present
		}
	}
	k := &store.Key{
		Value:   keyValue,
		Group:   "go",
		Label:   "bootstrap (from GO_API_KEY)",
		Enabled: true,
		Weight:  1,
	}
	if err := store.DB().Create(k).Error; err != nil {
		log.Printf("warn: cannot create bootstrap key: %v", err)
		return
	}
	fmt.Println("==================================================")
	fmt.Println(" Bootstrap Go key added from GO_API_KEY:")
	fmt.Printf("   %s...%s\n", keyValue[:4], keyValue[len(keyValue)-4:])
	fmt.Println("==================================================")
}

// loadModelMappings loads persisted model rewrite rules, then overlays optional
// env/file rules so runtime config can override DB-managed defaults.
func loadModelMappings() {
	rows, err := store.LoadModelMappings()
	if err != nil {
		log.Printf("warn: cannot load model mappings: %v", err)
		config.LoadModelMappings()
		return
	}
	mappings := map[string]string{}
	for _, r := range rows {
		mappings[r.SourceModel] = r.TargetModel
	}
	if len(mappings) > 0 {
		config.RegisterModelMappings(mappings)
	}
	config.LoadModelMappings()
}

// loadModelRoutes loads model routes from DB. If the DB is empty it waits for
// the first modelsync.Sync() call to populate the catalog from upstream APIs.
func loadModelRoutes() {
	rows, err := store.LoadModelRoutes()
	if err != nil {
		log.Printf("warn: cannot load model routes: %v", err)
		return
	}
	if len(rows) == 0 {
		log.Printf("model routes DB is empty; waiting for first sync to populate from upstream APIs")
		return
	}
	var routes []config.ModelRoute
	for _, r := range rows {
		if r.Upstream != string(config.UpstreamGo) && r.Upstream != string(config.UpstreamOllama) {
			continue
		}
		if r.Group != "go" && r.Group != "ollama" {
			continue
		}
		routes = append(routes, store.ModelRouteFromRow(r))
	}
	config.ReplaceModels(routes)
	log.Printf("loaded %d model routes from DB", len(rows))
}
