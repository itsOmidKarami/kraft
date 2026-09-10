# SPA shell auth bypass fix — design

Bead: Kraft-qntj

## Problem

`_authenticate` (src/kraft/api.py) only lets an unauthenticated GET through to
the SPA shell when the request carries `Sec-Fetch-Dest: document`. Clients
that omit or strip that header (some privacy browsers, in-app webviews,
proxies) get a raw `401 {"detail":"authentication required"}` on `GET /`
instead of the login page, and can never log in.

Confirmed with curl against a running instance:

```
no header:           401
with sec-fetch-dest:  200
```

## Root cause

The SPA catch-all (`spa()`, api.py ~2979) already serves `index.html`
publicly for any non-`/api` GET — no auth, no data, same trust level already
granted to static assets in `_is_static_asset`. It's the only non-`/api`
route in the app. `_authenticate`'s bypass gates on the wrong signal (a
header) instead of the right one (method + path).

## Fix

In `_authenticate` (api.py ~387-410):

- Widen the bypass condition to: `not _requires_auth(...)` OR path in
  `_PUBLIC_PATHS` OR `_is_static_asset(...)` OR (`request.method == "GET"`
  AND `not _is_api_path(request.url.path)`).
- Delete the now-dead inline `Sec-Fetch-Dest` / manual `FileResponse` block —
  the widened bypass reaches `call_next` → `spa()`, which already does that
  job.
- No change to `_spa_navigation` (harmless perf fast-path, not the bug) or to
  any `/api/*` route (still fully gated) or to non-GET verbs (still fully
  gated).

## Testing

- New test: unauthenticated `GET /` with no `Sec-Fetch-Dest` header → `200`,
  body is the SPA shell (not the 401 JSON).
- Existing auth tests still pass: a mutating verb without a session still
  401s; `/api/<unknown>` still 404s, does not fall through to the shell.
