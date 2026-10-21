// Command conduit runs the Conduit multi-key LLM gateway.
package main

import (
	"context"
	"flag"
	"fmt"
	"os"
	"runtime"

	"github.com/serbansorin/conduit/config"
	"github.com/serbansorin/conduit/server"
	"github.com/serbansorin/conduit/version"
)

func main() {
	if len(os.Args) > 1 {
		switch os.Args[1] {
		case "version", "-v", "--version":
			printVersion()
			return
		case "help", "-h", "--help":
			usage()
			return
		case "serve":
			serve(os.Args[2:])
			return
		}
	}
	serve(os.Args[1:])
}

// serve starts the gateway.
func serve(args []string) {
	fs := flag.NewFlagSet("conduit", flag.ContinueOnError)
	port := fs.Int("port", 0, "TCP port to listen on (0 = use PORT env, then 9812)")
	bind := fs.String("bind", "", "interface to bind (empty = all interfaces)")
	noSync := fs.Bool("no-model-sync", false, "skip the upstream model catalog sync at startup")
	if err := fs.Parse(args); err != nil {
		os.Exit(2)
	}

	config.Load()

	if _, err := server.Run(context.Background(), server.Options{
		Port:           *port,
		Bind:           *bind,
		SkipModelSync:  *noSync,
		AsyncModelSync: false,
	}); err != nil {
		fmt.Fprintf(os.Stderr, "conduit: %v\n", err)
		os.Exit(1)
	}
}

// printVersion writes the build version to stdout.
func printVersion() {
	fmt.Printf("conduit %s (%s %s/%s)\n", version.Version, runtime.Version(), runtime.GOOS, runtime.GOARCH)
}

// usage writes the command summary to stdout.
func usage() {
	fmt.Fprint(os.Stderr, `conduit — multi-key LLM gateway

Usage:
  conduit [serve] [flags]   start the gateway (default)
  conduit version           print the build version
  conduit help              show this message

Flags:
  --port int        TCP port to listen on (default: PORT env, then 9812)
  --bind string     interface to bind (default: all interfaces)
  --no-model-sync   skip the upstream model catalog sync at startup

Environment:
  ADMIN_PASSWORD, JWT_SECRET, DB_PATH, GO_BASE_URL, OLLAMA_BASE_URL,
  MODEL_MAPPINGS, MODEL_ALIASES, MODEL_FALLBACK, GROUP_MULTIPLIERS,
  UPSTREAM_TIMEOUT, PASSTHROUGH_MODE, PORT

`)
}
