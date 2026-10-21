package main

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"time"

	"github.com/serbansorin/conduit/server"
)

const (
	// appTitle is the window title and the menu application name.
	appTitle = "Conduit"
	// appDirName is the directory created under the user's config directory.
	appDirName = "conduit"
	// credentialsFile holds the generated admin credentials.
	credentialsFile = "admin.json"
	// databaseFile is the SQLite database inside the application directory.
	databaseFile = "conduit.db"
	// startupTimeout bounds how long the app waits for the gateway to accept
	// connections.
	startupTimeout = 30 * time.Second
)

// credentials are the secrets the desktop application generates on first run.
//
// The gateway refuses to start with its default admin password or with a JWT
// secret shorter than 32 bytes, so the application has to supply both. They
// are generated once and reused afterwards, which keeps the admin session and
// the database consistent across restarts.
type credentials struct {
	AdminPassword string `json:"admin_password"`
	JWTSecret     string `json:"jwt_secret"`
}

// appDataDir returns the directory that holds the database and credentials.
func appDataDir() (string, error) {
	base, err := os.UserConfigDir()
	if err != nil {
		return "", fmt.Errorf("locate user config directory: %w", err)
	}
	return filepath.Join(base, appDirName), nil
}

// loadOrCreateCredentials reads the stored credentials, generating and
// persisting a fresh pair when they are missing or incomplete.
func loadOrCreateCredentials(dir string) (*credentials, error) {
	path := filepath.Join(dir, credentialsFile)

	if data, err := os.ReadFile(path); err == nil {
		var stored credentials
		if err := json.Unmarshal(data, &stored); err != nil {
			return nil, fmt.Errorf("parse %s: %w", path, err)
		}
		if stored.AdminPassword != "" && stored.JWTSecret != "" {
			return &stored, nil
		}
	}

	fresh := &credentials{
		AdminPassword: randomHex(16),
		JWTSecret:     randomHex(32),
	}
	blob, err := json.MarshalIndent(fresh, "", "  ")
	if err != nil {
		return nil, fmt.Errorf("encode credentials: %w", err)
	}
	if err := os.WriteFile(path, blob, 0o600); err != nil {
		return nil, fmt.Errorf("write %s: %w", path, err)
	}
	return fresh, nil
}

// randomHex returns a hex encoded random string of n bytes.
func randomHex(n int) string {
	buf := make([]byte, n)
	if _, err := rand.Read(buf); err != nil {
		// crypto/rand only fails when the system entropy source is broken.
		panic(fmt.Sprintf("conduit: no secure randomness available: %v", err))
	}
	return hex.EncodeToString(buf)
}

// startGateway brings the gateway up on a loopback port and returns its
// addresses together with a function that shuts it down.
func startGateway(dir string, creds *credentials) (*server.Result, context.CancelFunc, error) {
	os.Setenv("DB_PATH", filepath.Join(dir, databaseFile))
	os.Setenv("ADMIN_PASSWORD", creds.AdminPassword)
	os.Setenv("JWT_SECRET", creds.JWTSecret)

	ctx, cancel := context.WithCancel(context.Background())
	ready := make(chan *server.Result, 1)

	go func() {
		_, err := server.Run(ctx, server.Options{
			Bind:           "127.0.0.1",
			UseRandomPort:  true,
			AsyncModelSync: true,
			OnReady:        func(result *server.Result) { ready <- result },
		})
		if err != nil {
			fmt.Fprintf(os.Stderr, "conduit: gateway stopped: %v\n", err)
		}
	}()

	select {
	case result := <-ready:
		return result, cancel, nil
	case <-time.After(startupTimeout):
		cancel()
		return nil, nil, errors.New("the gateway did not start within " + startupTimeout.String())
	}
}

// adminLogin exchanges the admin password for a session token.
func adminLogin(baseURL, password string) (string, error) {
	body, err := json.Marshal(map[string]string{"password": password})
	if err != nil {
		return "", err
	}

	client := &http.Client{Timeout: 15 * time.Second}
	resp, err := client.Post(baseURL+"/admin/login", "application/json", bytes.NewReader(body))
	if err != nil {
		return "", fmt.Errorf("contact the admin API: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return "", fmt.Errorf("admin login failed with status %d", resp.StatusCode)
	}

	var payload struct {
		Token string `json:"token"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&payload); err != nil {
		return "", fmt.Errorf("decode admin login response: %w", err)
	}
	if payload.Token == "" {
		return "", errors.New("the admin API returned an empty token")
	}
	return payload.Token, nil
}
