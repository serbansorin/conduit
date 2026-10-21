package main

import (
	"github.com/wailsapp/wails/v2/pkg/menu"
	"github.com/wailsapp/wails/v2/pkg/menu/keys"
	"github.com/wailsapp/wails/v2/pkg/runtime"
)

// buildMenu assembles the native application menu.
func (a *App) buildMenu() *menu.Menu {
	m := menu.NewMenu()

	appMenu := m.AddSubmenu(appTitle)
	appMenu.AddText("Open in Browser", cmd("o"), func(_ *menu.CallbackData) {
		a.OpenInBrowser()
	})
	appMenu.AddText("Open GitHub", cmd("u"), func(_ *menu.CallbackData) {
		a.OpenGitHub()
	})
	appMenu.AddSeparator()
	appMenu.AddText("Quit", cmd("q"), func(_ *menu.CallbackData) {
		runtime.Quit(a.ctx)
	})

	viewMenu := m.AddSubmenu("View")
	viewMenu.AddText("Reload Panel", cmd("r"), func(_ *menu.CallbackData) {
		runtime.WindowReload(a.ctx)
	})
	viewMenu.AddText("Toggle Fullscreen", nil, func(_ *menu.CallbackData) {
		if runtime.WindowIsFullscreen(a.ctx) {
			runtime.WindowUnfullscreen(a.ctx)
			return
		}
		runtime.WindowFullscreen(a.ctx)
	})

	return m
}

// cmd builds a Command accelerator.
func cmd(key string) *keys.Accelerator {
	return &keys.Accelerator{
		Key:       key,
		Modifiers: []keys.Modifier{keys.CmdOrCtrlKey},
	}
}
