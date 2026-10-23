# web

The admin panel: a Vue 3 SPA served as plain ES modules. There is no build
step and no bundler.

- `embed.go` — `//go:embed` directive and `AdminHandler()`.
- `admin.html` — the single HTML file, all page markup inline.
- `js/app.js` — the entry point and the page router (`openPage`).
- `js/api.js` — the single API chokepoint (`fetch("/admin" + path)`).
- `js/pages/*.js` — one composable per page (dashboard, keys, tokens, models,
  mappings, usage, ops).
- `js/locales.js` — the i18n catalogue. English is the default; the Chinese
  catalogue is retained but unused.
- `js/icons.js` — the inline SVG icon map.
- `css/admin.css` — the design tokens and every component style.
- `vendor/` — Vue and Chart.js, vendored and embedded so the panel works
  offline.

Adding a file under `web/js/<new-subdir>/` needs a new pattern in
`web/embed.go`.
