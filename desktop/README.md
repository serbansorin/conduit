# Conduit Desktop

The Conduit admin panel as a native macOS application.

The desktop app runs the gateway in-process on a loopback port and opens a
native window pointed at the admin panel, already signed in. It uses the same
single-page app the server ships, so the desktop and the web deployment behave
identically.

## Requirements

- macOS 11 or later
- Go 1.25 or later
- [Wails CLI](https://wails.io) v2 (`go install github.com/wailsapp/wails/v2/cmd/wails@latest`)
- Node.js and npm

## Build

```bash
cd desktop
wails build
```

The bundle lands in `desktop/build/bin/conduit.app`.

For development with live reload:

```bash
cd desktop
wails dev
```

## How it works

1. The app creates `~/Library/Application Support/conduit/` and generates an
   admin password and a JWT secret on first launch. Both are stored in
   `admin.json` with owner-only permissions and reused afterwards, so the
   admin session and the database stay consistent across restarts.
2. The gateway starts on a random loopback port. The model catalogue syncs in
   the background so the window opens immediately.
3. The app signs in to the admin API and opens the panel with a one-time
   session token, so there is no password prompt.

The gateway never listens on a public interface: it binds to `127.0.0.1` only.

## Layout

```
desktop/
├── main.go        Wails entry point and window configuration
├── app.go         the bound application object and the panel handoff
├── gateway.go     credentials, gateway startup and admin login
├── menu.go        the native application menu
├── frontend/      loading screen copied into dist/ at build time
└── build/         icon and macOS bundle metadata
```

## Notes

- `desktop/` is a separate Go module. The Wails dependency stays out of the
  root module on purpose: the root module is also built as a Linux container
  image, and Wails needs desktop toolkit headers that would break that build.
- The menu offers *Open in Browser*, *Open GitHub*, *Reload Panel* and
  *Toggle Fullscreen*.
