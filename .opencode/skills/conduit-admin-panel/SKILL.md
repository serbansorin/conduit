---
name: conduit-admin-panel
description: >-
  Work on the Conduit admin panel in web/, the embedded Vue 3 panel with no
  build step. Use when touching web/js/, web/css/admin.css, web/admin.html,
  web/embed.go or the panel's API calls. Triggers on admin panel, page
  composable, openPage, i18n, locales, icons, design tokens, go:embed or
  vendored libraries.
metadata:
  category: development
  suggest_for:
    filename:
      - '*.js'
      - '*.css'
      - '*.html'
      - '*.go'
---

# Conduit Admin Panel

`web/` is the admin panel. It is served from the Go binary, so there is
**no build step, no bundler and no npm install**. Plain ES modules, one
stylesheet, one HTML file.

```
web/
├── admin.html          the whole markup, Vue templates inline
├── embed.go            the //go:embed directives and the http.Handler
├── css/admin.css       one stylesheet, ~4000 lines, design tokens on top
├── js/
│   ├── app.js          the entry module, the router, the global state
│   ├── api.js          the API client
│   ├── locales.js      the translation catalogue
│   ├── icons.js        the inline SVG icon map
│   └── pages/          one composable per page
└── vendor/
    ├── vue.global.prod.js
    └── chart.umd.js
```

## The rules

- **No build step.** There is no `package.json` in `web/`. Do not add one.
- **Vendored libraries only.** `web/vendor/` holds the third-party code and it
  is embedded in the binary. **Never add a CDN link.** The panel must render
  on a machine with no internet, and a self-hosted gateway must not inherit
  somebody else's supply chain. `web/embed_test.go` has
  `TestVendorAssetsAreEmbedded` and `TestAdminHandlerServesVendorAssets` to
  keep that honest.
- **English only.** The panel ships in English. The locale catalogue still
  carries the Chinese translations, but the default is English and there is no
  switcher in the UI.
- **Cache busting is manual.** The import specifiers and the `<link>` and
  `<script>` tags carry a `?v=YYYYMMDD` query. Bump it when you change a
  file, otherwise a browser keeps the old module.

## How the panel is served

`web/embed.go`:

```go
//go:embed admin.html css/*.css js/*.js js/pages/*.js vendor/*.js
var content embed.FS
```

One pattern covers everything today. **A new subdirectory under `web/js/`
needs a new pattern.** If you add `web/js/widgets/`, the directive becomes:

```go
//go:embed admin.html css/*.css js/*.js js/pages/*.js js/widgets/*.js vendor/*.js
```

A file in a directory the pattern does not cover is silently absent from the
binary. The panel then 404s on it and the SPA fallback serves `admin.html`,
which looks like a blank page rather than a missing asset.

`AdminHandler()` strips the `/admin` prefix and reads the file from the
embedded FS:

```go
path := strings.TrimPrefix(r.URL.Path, "/admin")
```

- `""` or `"/"` -> `admin.html`.
- A path starting with `"/"` -> the path without the slash.
- Anything that is not in the embedded FS -> the SPA fallback serves
  `admin.html` with 200, so client-side routes work on a hard refresh.

The content type is derived from the extension: `.css` gets
`text/css; charset=utf-8`, `.js` gets `application/javascript; charset=utf-8`,
`.html` gets `text/html; charset=utf-8`, everything else uses
`http.DetectContentType`.

Every response gets no-store headers, so the panel never serves a stale
asset:

```go
w.Header().Set("Cache-Control", "no-store, no-cache, must-revalidate, proxy-revalidate")
w.Header().Set("Pragma", "no-cache")
w.Header().Set("Expires", "0")
w.Header().Set("Surrogate-Control", "no-store")
```

On the Go side, `server/server.go` mounts the panel:

```go
root.GET("/admin", gin.WrapH(web.AdminHandler()))
root.NoRoute(func(c *gin.Context) {
	if strings.HasPrefix(c.Request.URL.Path, "/admin") {
		web.AdminHandler().ServeHTTP(c.Writer, c.Request)
		return
	}
	c.Writer.WriteHeader(http.StatusNotFound)
})
```

## The HTML

`web/admin.html` is a single file with the Vue templates inline. The head:

```html
<base href="/admin/" />
<link rel="stylesheet" href="css/admin.css?v=20260725a" />
<script src="vendor/vue.global.prod.js?v=3.5.44"></script>
<script src="vendor/chart.umd.js?v=4.5.1"></script>
```

Vue and Chart.js are global scripts loaded before the module, because
`web/js/app.js` reads `Vue` off the global object rather than importing it:

```js
const { createApp, reactive, ref, watch } = Vue;
```

The same pattern holds in every page module:

```js
const { ref, reactive } = Vue;
```

The mount point is `<div id="app" v-cloak>`. The `v-cloak` attribute plus the
inline rule `[v-cloak]{display:none!important}` stops a flash of the
unrendered template.

The page regions are guarded by `v-if` on the `page` ref. The sidebar is a
plain `<aside class="sidebar">` with `<a>` elements that call `openPage(...)`.
The active item is marked with `:class="{active:page==='dashboard'}"`.

## The router

`web/js/app.js` is the entry module. It imports the icon map, the locales,
the API client and every page composable, then creates the Vue app:

```js
createApp({
  setup() {
    ...
    return { /* everything the templates use */ };
  },
}).mount("#app");
```

`openPage(nextPage)` is the router. It sets `page.value` and loads the data
for the page it landed on:

```js
function openPage(nextPage) {
  if (nextPage !== "keys" && nextPage !== "ollama-keys") keys.stopQuotaTicker();
  page.value = nextPage;
  if (channels.some(ch => ch.page === nextPage)) channelOpen.value = true;
  if (nextPage === "dashboard") dashboard.load();
  else if (nextPage === "ops") ops.load();
  else if (nextPage === "usage") usage.load();
  else if (nextPage === "keys") keys.load("go");
  else if (nextPage === "ollama-keys") keys.load("ollama");
  else if (nextPage === "tokens") tokens.load();
  else if (nextPage === "models") models.load();
  else if (nextPage === "mappings") mappings.load();
}
```

The route table, page to composable to module:

| Page id | Composable | Module | Sidebar label key |
| --- | --- | --- | --- |
| `dashboard` | `useDashboard` | `js/pages/dashboard.js` | `nav.dashboard` |
| `ops` | `useOps` | `js/pages/ops.js` | `nav.ops` |
| `usage` | `useUsage` | `js/pages/usage.js` | `nav.usage` |
| `keys` | `useKeys` | `js/pages/keys.js` | `nav.opencode` |
| `ollama-keys` | `useKeys` | `js/pages/keys.js` | `nav.ollama` |
| `tokens` | `useTokens` | `js/pages/tokens.js` | `nav.tokens` |
| `models` | `useModels` | `js/pages/models.js` | `nav.models` |
| `mappings` | `useMappings` | `js/pages/mappings.js` | `nav.mappings` |

`keys` and `ollama-keys` are the same composable with a different group. The
group lives in the `channels` array:

```js
const channels = reactive([
  { id: 'opencode', page: 'keys', labelKey: 'nav.opencode' },
  { id: 'ollama', page: 'ollama-keys', labelKey: 'nav.ollama' },
]);
```

`openPage` is a plain function, not a router library. There is no URL
segment, no history entry and no deep link. The only URL state is the
`?token=` parameter, read once at startup.

To add a page:

1. Create `web/js/pages/<name>.js` exporting a composable.
2. Import it in `web/js/app.js`, call it, and spread its returns into the
   `setup()` return object.
3. Add a branch to `openPage`.
4. Add the sidebar entry in `web/admin.html`.
5. Add the `v-if="page==='<name>'"` block in `web/admin.html`.
6. Bump the `?v=` on the import in `web/js/app.js`.

## The page composable pattern

Every page module exports a single `use*` function. It takes the shared
helpers as arguments and returns a flat object of refs and functions.

The signatures:

```js
export function useDashboard(api, showToast, t) { ... }
export function useOps(api, showToast, t) { ... }
export function useUsage(api, showToast, t) { ... }
export function useKeys(api, showToast, t, showConfirm) { ... }
export function useTokens(api, showToast, t, showConfirm) { ... }
export function useModels(api, showToast, t, showConfirm) { ... }
export function useMappings(api, showToast, t, showConfirm) { ... }
```

- `api` is the API client from `createApi`.
- `showToast(msg, type)` shows a toast. `"error"` keeps it for 5 seconds,
  anything else for 3.
- `t(key, params)` translates a dotted key.
- `showConfirm(type, item, name)` opens the confirmation dialog. `type` is one
  of `logout`, `deleteKey`, `deleteToken`, `deleteModel`, `deleteMapping`.
  `item` is the callback to run when the user confirms, `name` is the label
  shown in the message.

The smallest complete example is `web/js/pages/mappings.js`:

```js
import { validateRequired } from "../api.js?v=20260619a";
const { ref, reactive } = Vue;

export function useMappings(api, showToast, t, showConfirm) {
  const mappings = ref([]);
  const showMappingModal = ref(false);
  const editingSource = ref("");

  const newMapping = reactive({
    source_model: "",
    target_model: "",
  });

  function openMappingModal() {
    editingSource.value = "";
    newMapping.source_model = "";
    newMapping.target_model = "";
    showMappingModal.value = true;
  }

  async function load() {
    try {
      const d = await api("/model-mappings", "GET", null, t);
      mappings.value = d.data || [];
    } catch (e) {
      showToast(e.message, "error");
    }
  }

  async function add() {
    if (!validateRequired(newMapping.source_model, t("mappings.sourceModel"), t, showToast))
      return;
    if (!validateRequired(newMapping.target_model, t("mappings.targetModel"), t, showToast))
      return;

    try {
      if (editingSource.value && editingSource.value !== newMapping.source_model) {
        await api("/model-mappings/" + encodeURIComponent(editingSource.value), "DELETE", null, t);
      }
      await api("/model-mappings", "POST", newMapping, t);
      showToast(editingSource.value ? t("mappings.updateBtn") : t("mappings.addBtn"));
      closeMappingModal();
      load();
    } catch (e) {
      showToast(e.message, "error");
    }
  }

  return {
    mappings,
    newMapping,
    editingSource,
    showMappingModal,
    openMappingModal,
    openMappingSettings,
    closeMappingModal,
    load,
    add,
    remove,
  };
}
```

Rules that fall out of the pattern:

- The composable never touches the DOM. It owns state and calls the API.
- Every API call is wrapped in `try`/`catch` and reports through
  `showToast(e.message, "error")`.
- A successful mutation reloads the list.
- The returned object is flat, and `app.js` re-exports each member under a
  template-friendly name.

## The API client

`web/js/api.js` exports `createApi`, `fmtApiError`, `validateRequired` and
`fmtTime`.

```js
export { createApi, fmtApiError, validateRequired, fmtTime };
```

`createApi(tokenRef)` returns `{ api, headers, fmtTime, validateRequired }`.
The **hardcoded `/admin` prefix** lives in the fetch call:

```js
async function api(path, method = "GET", body = null, t) {
  let r;
  try {
    const opts = { method, headers: headers() };
    if (body) opts.body = JSON.stringify(body);
    r = await fetch("/admin" + path, opts);
  } catch (e) {
    throw new Error(fmtApiError(e, t));
  }
  if (r.status === 401) {
    tokenRef.value = "";
    localStorage.removeItem("admin_token");
    throw new Error(t ? t("errors.unauthorized") : "Session expired");
  }
  const data = await r.json();
  if (!r.ok)
    throw new Error(data.error || (t ? t("errors.requestFailed") : "Request failed"));
  return data;
}
```

So a call is `api("/keys", "GET", null, t)` and it hits `/admin/keys`. Always
pass the path **without** the `/admin` prefix.

The auth header is set by `headers()`:

```js
function headers() {
  return {
    "Content-Type": "application/json",
    Authorization: "Bearer " + tokenRef.value,
  };
}
```

On a 401 the client clears the token ref and removes `admin_token` from
`localStorage`, then throws. The login screen reappears because the template
is guarded by `v-if="!token"`.

`fmtApiError(err, t)` turns a fetch `TypeError` into `t("errors.network")` and
anything else into the error message.

`validateRequired(value, label, t, showToast)` shows a toast and returns
`false` when the value is empty.

## The token and the `?token=` handoff

`web/js/app.js` reads the token at startup:

```js
const bootstrapToken = new URLSearchParams(location.search).get("token");
if (bootstrapToken) {
  localStorage.setItem("admin_token", bootstrapToken);
  const cleanUrl = location.pathname + location.hash;
  history.replaceState(null, "", cleanUrl);
}
const token = ref(localStorage.getItem("admin_token") || "");
```

The parameter is consumed once and stripped from the URL so it does not stay
in the browser history. It is how the desktop app hands over a session and
how an operator shares a signed-in link.

Login stores the JWT:

```js
const d = await api("/login", "POST", { password: password.value });
token.value = d.token;
localStorage.setItem("admin_token", d.token);
```

Logout clears both:

```js
function logout() {
  token.value = "";
  localStorage.removeItem("admin_token");
}
```

Other `localStorage` keys: `admin_theme` (the `dark` or `light` theme) and
`admin_locale` (written by the locale watcher, but the panel starts in
English regardless).

The theme is applied through a data attribute on the root element:

```js
document.documentElement.dataset.theme = theme.value;
```

`web/css/admin.css` keys every colour off that attribute, so `dark` and
`light` are two token blocks.

## i18n

`web/js/locales.js` exports one object:

```js
export const locales = {
  zh: { nav: {...}, errors: {...}, ... },
  en: { nav: {...}, errors: {...}, ... },
};
```

`zh` comes first in the file, `en` second. Both catalogues are complete and
kept in sync.

The lookup is the `t` function in `web/js/app.js`:

```js
function t(key, params) {
  const keys = key.split(".");
  let val = locales[locale.value];
  for (const k of keys) if (val) val = val[k];
  if (typeof val === "string" && params)
    for (const [k, v] of Object.entries(params))
      val = val.replace("{" + k + "}", v);
  return val || key;
}
```

The key is dotted, the parameters are `{name}` placeholders replaced on the
resolved string. When the key is missing, `t` returns the key itself, which
makes a missing translation visible in the UI instead of blank.

The top-level sections in both catalogues: `nav`, `errors`, `login`,
`dashboard`, `ops`, `usage`, `keys`, `tokens`, `models`, `mappings`, `theme`,
`about`, `common`, `capabilities`, `confirm`, `table`.

The panel is English only:

```js
// The panel ships in English. The locale catalogue still carries every
// translation, so switching this back to a stored value re-enables them.
const locale = ref("en");
```

To re-enable the Chinese catalogue, read the stored value back:

```js
const locale = ref(localStorage.getItem("admin_locale") || "en");
```

The locale watcher also sets the document language:

```js
watch(locale, (val) => {
  localStorage.setItem("admin_locale", val);
  document.documentElement.lang = val === "zh" ? "zh-CN" : "en";
});
```

When you add a string, add it to **both** catalogues. The panel is English
by default, so a key that only exists in `zh` renders as the raw key.

## Icons

`web/js/icons.js` exports one object, `icons`. It is a registry of inline SVG
strings built by a local helper:

```js
const lucide = (content, className = "") =>
  `<svg class="lucide ${className}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${content}</svg>`;

export const icons = {
  logo: lucide('<path d="m13 2-9 11h8l-1 9 9-12h-8z"/>', "lucide-zap"),
  dashboard: lucide('...', "lucide-layout-dashboard"),
  ...
};
```

Every icon is a Lucide path on the same 24px grid with a 1.8px stroke and
rounded caps. The second argument is the Lucide class name, so the CSS can
target it.

The map is rendered with Vue's `v-html`, because it holds markup:

```html
<span class="icon" v-html="icons.dashboard"></span>
```

`app.js` imports it and returns it from `setup()` as `icons`.

To add an icon, copy a Lucide path into the registry with the `lucide`
helper. Do not import an icon package.

## The design tokens

`web/css/admin.css` is one stylesheet, about 4000 lines. The token layer is
at the top, before any component rule. There are two blocks, dark then light.

The selector is the theme data attribute on the root element:

```css
[data-theme="dark"] { ... }
[data-theme="light"] { ... }
```

Dark tokens:

| Token | Value |
| --- | --- |
| `--bg` | `#16171a` |
| `--bg-elevated` | `#1c1d21` |
| `--sidebar` | `#1a1b1f` |
| `--card` | `rgba(38, 39, 44, 0.72)` |
| `--card-solid` | `#26272b` |
| `--border` | `rgba(255, 255, 255, 0.08)` |
| `--border-strong` | `rgba(255, 255, 255, 0.14)` |
| `--text` | `#f2f2f7` |
| `--muted` | `#98989f` |
| `--accent` | `#0a84ff` |
| `--accent-2` | `#5e5ce6` |
| `--green` | `#30d158` |
| `--red` | `#ff453a` |
| `--yellow` | `#ffd60a` |
| `--orange` | `#ff9f0a` |
| `--blue` | `#64d2ff` |
| `--radius` | `10px` |
| `--radius-lg` | `14px` |
| `--radius-sm` | `7px` |
| `--radius-control` | `8px` |
| `--topbar-bg` | `rgba(22, 23, 26, 0.72)` |
| `--hover` | `rgba(255, 255, 255, 0.06)` |
| `--shadow` | `0 1px 2px rgba(0, 0, 0, 0.28), 0 8px 24px rgba(0, 0, 0, 0.22)` |

Light tokens:

| Token | Value |
| --- | --- |
| `--bg` | `#f2f2f7` |
| `--bg-elevated` | `#ffffff` |
| `--sidebar` | `rgba(242, 242, 247, 0.72)` |
| `--card` | `rgba(255, 255, 255, 0.86)` |
| `--card-solid` | `#ffffff` |
| `--border` | `rgba(60, 60, 67, 0.16)` |
| `--border-strong` | `rgba(60, 60, 67, 0.28)` |
| `--text` | `#1c1c1e` |
| `--muted` | `#6e6e73` |
| `--accent` | `#007aff` |
| `--accent-2` | `#5856d6` |
| `--green` | `#34c759` |
| `--red` | `#ff3b30` |
| `--yellow` | `#ffcc00` |
| `--orange` | `#ff9500` |
| `--blue` | `#30b0c7` |

Shared by both themes:

| Token | Value |
| --- | --- |
| `--font-display` | `-apple-system, BlinkMacSystemFont, "SF Pro Display", "Segoe UI", sans-serif` |
| `--font-body` | `-apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", sans-serif` |
| `--font-mono` | `ui-monospace, "SF Mono", Menlo, monospace` |
| `--accent-tint` | `color-mix(in srgb, var(--accent) 18%, transparent)` |
| `--accent-tint-soft` | `color-mix(in srgb, var(--accent) 12%, transparent)` |
| `--accent-ring` | `color-mix(in srgb, var(--accent) 35%, transparent)` |
| `--input-bg` | dark `rgba(255, 255, 255, 0.07)`, light `#ffffff` |
| `--row-hover` | dark `rgba(255, 255, 255, 0.04)`, light `rgba(0, 0, 0, 0.03)` |
| `--scrollbar` | dark `rgba(152, 152, 159, 0.32)`, light `rgba(60, 60, 67, 0.28)` |
| `--select-chevron` | an inline SVG data URI |

Note `--accent-2` and `--accent2` are both defined with the same value. The
hyphenated one is the current name.

`--score` is a per-element token used by the health ring: the template sets
it inline with `:style="{'--score': healthScore(stats)}"`.

**Never hardcode a colour.** Use the token. A raw hex in a component rule
breaks the light theme.

The file is organised by section, each with a banner comment: `Document`,
`App shell`, `Top bar`, `Split shell`, `Main region`, `Cards`, `Stat tiles`,
`Tables`, `Buttons`, `Forms`, `Toggle (iOS switch)`, `Badges & pills`,
`Login`, `Chart`, `Toast`, `Modal / confirm`, `Dropdown menu`, `Checkbox
group`, `Scrollbars`, then the page-specific blocks: `Page hero`,
`Health score`, `Rich stat tiles`, `Charts`, `Event list`, `Quota`,
`Filters`, `Usage records`, `Models`, `Model route editor`, `Destructive
rebuild dialog`, `Numeric alignment`, `Motion`, `Responsive`.

Put a new component rule in the matching section, not at the end of the
file.

## Charts

Chart.js is the vendored global `Chart`. The panel creates charts against
`<canvas>` elements with fixed ids, for example `chartTraffic`,
`chartProtocol`, `chartModel`, `chartOpsThroughput`, `chartErrors`,
`chartLatency`.

A chart instance is created in the page composable and destroyed when the
page is left. Do not create one per render.

## Traps

- Adding a file under a **new** directory in `web/js/` without updating the
  `//go:embed` pattern in `web/embed.go` produces a panel that silently
  falls back to `admin.html`.
- Adding a CDN `<script>` or `<link>` breaks the offline case and the
  embedded-vendor test.
- `api()` already prepends `/admin`. Passing `"/admin/keys"` hits
  `/admin/admin/keys`.
- Every new translatable string goes into both `zh` and `en`.
- Bump the `?v=` query on any file you change, otherwise browsers keep the
  old copy. The panel is served no-store, but the module URL is what the
  browser caches.
- Vue is a global, not an import. `const { ref, reactive } = Vue;` at the
  top of a page module.
- `app.js` returns one flat object from `setup()`. A new page member must be
  added there, otherwise the template cannot see it.
- The panel is served from `/admin`, and `<base href="/admin/">` is what makes
  the relative asset paths work. Keep that base tag.
- The API client prefixes `/admin`, so an admin **API** route and a panel
  **page** share the same prefix space. A new API route must not collide
  with a page path.

## Verify a change

```bash
export PATH="/opt/homebrew/bin:$PATH"
go build ./...
go test ./web/...       # the embed tests
gofmt -l .
```

Then start the gateway and open `http://localhost:9812/admin` in a browser.
Log in with the admin password and walk the pages you touched. Check the
browser console: a missing module, a missing translation key or a broken
`v-html` shows up there first.

```bash
./scripts/smoke.sh
```

The smoke script exercises the admin API (`POST /admin/login`,
`GET /admin/backup`, `GET /admin/usage.csv`, `GET /admin/keys`), not the
panel itself.
