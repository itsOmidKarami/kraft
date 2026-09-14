# QUESTIONS — round 3 (W1, W6, W7/8)

## W1 · rule `no console errors on /~light/` (also W8 `no-console on ^login/`)

**Cell:** `login/default@1280~light` (and every `login/*`) — one console error left:
`Failed to load resource: the server responded with a status of 401 (Unauthorized)`.

**Why it cannot reach 0 from the frontend alone.** The login screen exists because the
server answers 401. Nothing public tells the SPA it has no session before it asks:
`/api/health` carries no auth state, and the session cookie is `HttpOnly`. Chromium logs
every non-2xx fetch as a console error, whatever the page does with the rejection.

**What changed in W1** (`main.tsx`): `/api/theme` is now the single session probe. On 401
the app mounts straight into Login and never calls bootstrap, the event socket or any
view fetch. Before: 10 console errors per login cell (4 of them the app's own
`console.error`), after: 1.

**Options**
1. Backend: a public `authenticated: bool` on `/api/health` (or a 200-always
   `/api/session`). The SPA checks it before any protected call → 0 errors.
   Needs a Python change, out of scope for these waves.
2. Accept one 401 on a locked instance and scope the rule to exclude the
   probe (a harness-side allowlist for exactly `401` on the first `/api/theme`).

**Recommendation:** option 1, filed as Kraft-yx79s; the rule stays failing until then
rather than being weakened.

---

# QUESTIONS — W11

## W11 · implicit rule `no newly flagged cells` vs A.1 / B.2 one-line ellipsis

**Cells:** every item page whose meta line does not fit — `item/gate-long@768…1440`,
`item-*/long@1100|1280`, `el-*/gate-*-long@1100|1280`, `composer/*-filled-long@1100|1280`
(124 cells after section A; the board rows of B.2 add their own). Flag: `ellipsis×1` on
`.detail-meta-part` (the item header's meta line) — and, after B, on `.board-row-title` /
`.board-row-meta`.

**The conflict.** The brief decides these lines are *one line, ellipsized* (A.1 meta line,
B.2 board title and meta line). `checks.ts` flags any `text-overflow: ellipsis` that
actually truncates, unless the element carries `data-allow-ellipsis`; W10.D said that
attribute is for the Documents path only, "do not use this attribute anywhere else".
Both are standing decisions, so the rule cannot pass without overriding one of them.

**Options**
1. Allow the attribute on these lines too (full text already on each line's `title`), with
   a `checks.spec` case per new use — the same resolution W10.D used for the path.
2. Keep W5's policy for them: wrap to two lines instead of ellipsizing, reversing A.1/B.2.

**Recommendation:** option 1 — the brief chose ellipsis for these lines on purpose and each
already carries its whole text in `title`.

**Decided (2026-09-14): option 1.** `data-allow-ellipsis` is allowed on exactly
`.detail-meta-part`, `.board-row-title`, `.board-row-meta`, the peek header id/meta line, and
the W10 Documents path — full text in `title`, one `checks.spec` case per use, the allowlist
in `sweep/README.md`. Everything else that ellipsizes still fails.
