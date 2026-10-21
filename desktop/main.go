// Command conduit-desktop is the Conduit desktop application.
//
// It runs the Conduit gateway in-process on a loopback port and shows the
// admin panel in a native window. The panel is the same single-page app the
// server serves, so the desktop and the web deployment behave identically.
package main

import (
	"fmt"
	"net/http"
	"os"

	"github.com/wailsapp/wails/v2"
	"github.com/wailsapp/wails/v2/pkg/options"
	"github.com/wailsapp/wails/v2/pkg/options/assetserver"
)

func main() {
	app, err := newApp()
	if err != nil {
		fmt.Fprintf(os.Stderr, "conduit: %v\n", err)
		os.Exit(1)
	}

	err = wails.Run(&options.App{
		Title:            appTitle,
		Width:            1440,
		Height:           900,
		MinWidth:         1024,
		MinHeight:        700,
		BackgroundColour: &options.RGBA{R: 8, G: 11, B: 16, A: 255},
		AssetServer: &assetserver.Options{
			Handler: http.HandlerFunc(app.servePanel),
		},
		Menu:       app.buildMenu(),
		OnStartup:  app.startup,
		OnShutdown: app.shutdown,
		Bind:       []interface{}{app},
	})
	if err != nil {
		fmt.Fprintf(os.Stderr, "conduit: %v\n", err)
		os.Exit(1)
	}
}
