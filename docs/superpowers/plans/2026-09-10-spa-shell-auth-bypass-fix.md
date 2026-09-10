# SPA shell auth bypass fix — plan

Bead: Kraft-qntj
Spec: docs/superpowers/specs/2026-09-10-spa-shell-auth-bypass-design.md

## Steps

1. Write the failing test first: unauthenticated `GET /` with no
   `Sec-Fetch-Dest` header on a server requiring auth (password set,
   non-loopback bind or spoofed remote peer) currently returns `401`;
   assert it should return `200` with the SPA shell body. Put it alongside
   the existing auth tests (likely `tests/test_api.py` or wherever
   `_authenticate`/`_requires_auth` is already covered — check first).
2. Run it, confirm it fails for the reason in the spec (401, not some
   unrelated error).
3. In `src/kraft/api.py`, widen `_authenticate`'s bypass condition to add
   `(request.method == "GET" and not _is_api_path(request.url.path))`.
4. Delete the now-unreachable inline `Sec-Fetch-Dest` / manual
   `FileResponse` block in `_authenticate` that the widened bypass
   replaces.
5. Run the new test — passes. Run the full existing auth test file(s) —
   still pass (mutating verb still 401s, `/api/<unknown>` still 404s).
6. `just lint`.

## Out of scope

- `_spa_navigation` middleware — untouched, not the bug.
- Anything under `/api/*` — untouched, still fully gated.
