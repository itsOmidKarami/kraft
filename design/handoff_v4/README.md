# Handoff: Kraft UI v4 — what shipped, and the rules it follows

Supersedes `handoff_v3`. v3 was a gap list against a build that diverged from the spec in ~50 places; v4 is written after 13 fix waves (W0–W13, `ui-waves`, 14 Sep 2026) landed and the sweep went from 905 flagged frames to 180, all of the remaining ones deliberate. **The shipped app is now the layout reference.** Every screen in `screens/` is a frame from the sweep, not a mock, so the PNGs and the code cannot disagree.

Use this document for three things:

1. **Rules** — the layout, overflow, scroll, breakpoint and interaction rules the code now follows. When adding a screen or a state, follow these and the sweep will stay green.
2. **Vocabulary** — the components that exist and must be reused (no second popover, no second id shortener).
3. **Regression gate** — how to run the sweep before merging UI work.

v2/v3 mocks remain in `design/archive/` for history; nothing in them overrides a v4 screen.

## Files

| File | Use it for |
|---|---|
| `screens/d01–d55`, `m01–m17` | **The reference.** Sweep frames at 1280 (desktop) and 390 (phone), plus 768 / 1100 / 1920 / 700-tall / light where a rule depends on it. |
| `Kraft UX Options.dc.html` | The design decisions behind W11 and W13 (gate card 1e, board row 1c, chains 2b, analytics 2c, phone row 2e, Tasks 3a, Documents 3c) with the rejected alternatives beside them. |
| `Kraft Sweep.dc.html` | The review surface for a sweep run: contact sheets, flags, triage. Point it at `e2e-shots/sweep/manifest.json`. |
| `frontend/sweep/briefs/` | Every wave brief (W0–W14), PUNCHLIST-v3 (the evidence list the waves closed) and QUESTIONS; `frontend/sweep/WAVES.md` holds the W0–W9 rules. Not copied here, so they cannot drift. The wording of a rule there is the wording in the code comments. |

Runtime: `frontend/sweep/` (harness), `frontend/sweep/HISTORY.md` (per-wave receipts), `frontend/sweep/briefs/` (every wave brief), `frontend/e2e-shots/sweep/` (current baseline).

## 1 · Item page (d12–d43)

**Hero** (`Header.tsx`): one 12px meta line `repo · template · bead · ShortId · +N submodules · from spec` (1-line ellipsis, `data-allow-ellipsis`); right run `status chip · node · n/N · duration · cycle pill · ⋯`; 22px title, 2-line clamp; `description` is a link that expands rendered markdown in place; progress bar full-width under the title on running items. No repo list in the hero (Config → Repos).

**Card** (`GateCard.tsx`, all states — the action bar no longer exists): header `⚑ title` + eyebrow (`code_review · waiting 1d 1h · 2 findings deferred · see Timeline`), stats top-right (`1 task · 449k tokens · $5.01`), judge-stop note and deferred findings as inner boxes, then one button row: primary · secondary · text links · spacer · **More actions ▾**. Per state:

| state | buttons | More actions adds |
|---|---|---|
| gate | Approve · Reject · Read document | Skip · Escalate |
| running | Pause | Steer (disabled, tooltip) |
| paused | Resume · Steer | — |
| capped / budget | Steer & retry / Raise budget · Escalate | — |
| question | Answer | — |
| escalating | pill `Auto-escalated · turn N` | — |
| escalated | Reply · Dismiss | — |
| done / archived | Open MR ↗ | — |
| not started | Start · Edit chain (intake card below) | — |

More actions always ends with `Review changes · Open MR · Open worktree · ─ · Copy id · Copy link · Archive`. Hints are tooltips (`title` + `aria-describedby`), never standing text. Composers open inline under the button row; ⌘/Ctrl-Enter submits, Esc cancels and restores focus; after submit the card shows a pending state until the store confirms.

**Split** (`index.tsx`): hero + card + graph are `flex:none`; the split is `flex:1; min-height:0`, floor 320px. Two independent scrollers (inspector, right pane); the page never scrolls. At 700px tall the card's inner boxes collapse to one line, then the description to one line — never the split. Under 1024 the split stacks with a List / Detail segmented control (d38).

**Inspector tabs**: Tasks (sessions of the scoped node, `this node | all` chips; plan list above when the node has one) · Changes (folder tree, this node / landed) · Documents (rows led by `hook · attempt`, cleaned title as the second line, kind tag; section header is the node) · Timeline (rounds only on the left; sessions are rows in the right pane — the event stream, one selectable row per session, findings inline, `waiting Nm` gaps) · Config (form, YAML on the right). Right pane header always: eyebrow · title 1 line · ShortPath · actions.

**Duration**: node run time frozen at session exit; waiting time is on the card. Never negative, never raw seconds.

## 2 · Board (d01–d09, m01–m03)

Row grid `22px minmax(0,1fr) 150px 110px auto` at ≥1280; drop the mini-chain at 1024–1279; `22px 1fr auto` on phone. Title 1 line; meta `repo · bead · template · ago · reason` with the reason as the accent tail (`approve code_review`, `verify hit its cap · 3 attempts`, `agent asks: …`, `paused at implement`). **One button per needs-you row**, sized to the state (Approve / Steer & retry / Raise budget / Answer / Reply / Resume); running and done rows have none. 63px rows compact, 72 comfortable; phone 64px, no buttons, long-press → sheet.

Facet bar: one row, chips `max-width 160px`, `+N more ▾` popover after 8; sort on the same row. Peek: fixed overlay `min(520px, 45vw)`, `--color-surface`, scrim under 1280; header `ShortId · state · Open →` / `repo · template`; the same card as the item page (buttons + More actions); last 4 log lines. Whole row click = peek, button click = act in place. Archive selection: toast with Undo. Sidebar state and open groups persist across reload.

## 3 · Settings, analytics, search (d44–d55)

Chains: header `name · valid · N nodes · N gates · template ▾ · YAML · Revert · Save`; pill strip; one editor card (form left, node YAML right). Plugins / Policy / Intake / Access: label column `minmax(120px, max-content)`, identifier columns `minmax(14ch, max-content)`, sticky Save footer with dirty state, single column under 1024 and at 1100 with the sidebar open. Analytics: three headline tiles (Completed · Lead time · Cost), throughput, three tables; the removed KPIs live in captions. Search: results 2-line clamp, snippets stripped of markdown, `N results` from the real count, lag note separate. `/settings` is an index page.

## 4 · Cross-cutting rules (the sweep checks these)

- **Breakpoints**: only `767 / 1023 / 1279` (+ one `max-height: 719`). `src/breakpoints.test.ts` fails on anything else.
- **Overflow**: 32-hex ids → `<ShortId>` (`c744…7b11`, full in `title`, click copies), never a crumb or a title. Paths → `<ShortPath>` (left ellipsis). List titles 2-line clamp, modal titles 3. Identifiers never truncate below 14ch — the value column yields. Ellipsis is allowed only on elements carrying `data-allow-ellipsis` (hero meta, board title/meta, peek header, document path); the check fails anything else that clips.
- **Light mode**: every palette defines surface + text ramp + border + chip + status tints together under `[data-mode="light"]`; `test_palette_contrast` asserts ≥4.5:1 for text, ≥3:1 for fills, both modes, every palette. `data-mode` is set before first paint.
- **Phone**: 44px floor on every control at ≤767; 16px inputs; chip rows scroll; every scroll container pads for the bottom nav + safe area; header = back + title only; sheets carry one action pair.
- **Focus**: `:focus-visible` ring on every interactive element; roving tabindex in tab strips and menus; programmatic focus scrolls the nearest container, never the window.
- **Toasts**: top-right on desktop, above the bottom nav on phone; never over the control just pressed.
- **Text**: no standing hint text; tooltips carry it. Reasons and questions render in full on the card, never `imp…`.

## 5 · Regression gate

```bash
cd frontend
node sweep/wave.mjs all --baseline     # once, on main after a merge
# … change UI …
node sweep/wave.mjs all                # re-shoot, pixel-diff, rules → e2e-shots/DIFF-all.md
```

`all` fails on any newly flagged cell (`nested-scroll` aside). A wave (`wave.mjs W<n>`) adds its own rules from `waves.json`: `offscreen`, `clipped-v`, `target<44` at 390, console errors (except login/ until `Kraft-yx79s`), a flow step that does not complete. ~8 min, one runner. How to run it: `frontend/sweep/README.md`. In CI: run on `changes: frontend/**`, regression-only, artifact `DIFF-all.md`.

Known accepted flags (do not "fix"): `nested-scroll` = 2 on item pages (the model); `input<16` on 15.x px phone inputs (check calibrates in W14); `login/` console 401 (backend `authenticated` field pending).

## 6 · Open beads

`Kraft-yx79s` login 401 · `Kraft-bj9pq` phone search hint gutter · `Kraft-k08aw` board-peek/running-long@390 setup cell · `Kraft-bws10` appearance e2e reload race.

## What comes next (not started)

Peek contents beyond the card; search result rows; settings polish; a week of real use on the new item page and Timeline before deciding. Decisions go through `Kraft UX Options.dc.html` (options side by side, one pick) → a `W<n>_BRIEF.md` → one Claude Code wave → sweep diff → merge.
