# Kraft UX wave — W11 build brief

Picked in `Kraft UX Options.dc.html` (design project): **1e** item gate card, **1c** board row, **2b** chains editor, **2c** analytics, **2e** phone board row, **3a** timeline scope, **3c** documents, plus **H** document titles, **I** peek actions, **J** escalating items. Sections A–J, commit order A B C I J F G H D E. Nocturne tokens throughout; no new colours. Each item lists the mock id, the rules, and the sweep cells that must look right afterwards. Same loop as W0–W10 (`sweep/WAVES.md` "The loop"), one branch `wave/W11`, one commit per section, `node sweep/wave.mjs W11` (add W11 to `waves.json` — screens: item, item-*, el-*, composer, board, board-peek, settings-chains, analytics, flow-gate-*, flow-pause-steer-resume, flow-peek-open-close, flow-board-selection-archive; rules: no-flag offscreen ".", no-flag clipped-v ".", no-flag target<44 "@390", no-console "^(?!login/)", flow-completes "^flow-", no-new-flags).

---

## A · Item hero + gate card (1e)

Hero (all states):
1. One meta line, 12px, `--color-neutral-500`, single-line ellipsis: `repo · template · bead · <ShortId> · +N submodules · from spec`. Tags ("from spec", "needs you") become words in this line; the "needs you" status moves to the right run.
2. Right run on the same row, `flex:none`, nowrap: status chip (`needs you` / `running` / `paused` / …) · `node · 5/8 · 6m` · cycle pill (`fix · cycle 1`) when present.
3. Title 22px/1.2, 2-line clamp. Description is a link "description" after the title; click expands the rendered markdown under the title (same "more" component as today, just closed by default and triggered from the link). Progress bar (running items) stays a full-width row under the title.
4. Remove the standing node block on the right (big "review" + pill + "node 5 of 8"); its content is the right run in rule 2.

Gate card (gate states: spec_approval, plan_approval, code_review, human_review_approval):
5. Card header row: ⚑ + "approve to continue" (15px/500) and, on the second line, `code_review · waiting 15h 23m · 2 findings deferred · see Timeline`. Top-right of the header: `1 task · 95.4k tokens · $5.01` (the action bar's stats, 12px muted).
6. Button row: **Approve** (primary), **Reject** (secondary), **Read document** as a text link (same style as "see Timeline"), spacer, **More actions ▾** (secondary, flush right at the card's 16px inset).
7. No standing hint text in the card. "reject walks back to implement" is the Reject button's tooltip (`title` + `aria-describedby`); "continue without approving" is Skip's; "ask an agent to decide" is Escalate's; "not opened yet" is Open MR's when disabled.
8. **More actions** popover (reuse the existing item ⋯ menu component; right-aligned to the button; width 280px): Skip (skip-forward icon) · Escalate (robot icon) · ─ · Review changes · Open MR ↗ (dimmed until an MR exists) · Open worktree · ─ · Copy id · Copy link · Archive. One action per row, no descriptions.
9. The separate **action bar block is removed on gate states**. Judge-stop note and deferred-findings list stay inside the card as today (fit steps unchanged).

Other states — same shape, different buttons; the action bar block is removed everywhere and its contents split between the card's button row and More actions:
- running: **Pause** · More (Steer disabled with tooltip "steer unlocks once paused", Review changes, MR, Worktree, Copy id, Copy link, Archive).
- paused: **Resume** · **Steer** · More.
- capped / budget-stopped: **Steer & retry** (or **Raise budget**) · **Escalate** · More.
- question: **Answer** · More.
- escalating: the "escalation running" pill · More.
- escalated: **Reply** · **Dismiss** · More.
- done / archived: **Open MR ↗** · More.
- not started: **Start** · **Edit chain** · More (unchanged intake card below).
Stats (`N tasks · tokens · $`) sit top-right of the card in every state.

10. Phone (≤767): same card; button row wraps; More actions opens the existing action sheet.
11. Keyboard: More actions is a menu button (`aria-haspopup="menu"`, arrow keys, Escape returns focus). Composers (Reject, Steer, Answer, …) open inline below the button row as today.

Tests: item page renders no `.action-bar` on any state; gate card button set per state (table above); Reject has a `title`; More actions opens and lists the 8 entries; Open MR disabled without `mr_ref`.
Cells by eye: `item/gate-long@1280`, `item/gate@1100`, `item/gate@390`, `item/paused@1280`, `item/capped@1280`, `item/question@1280`, `item/escalated@1280`, `item/done@1280`, `composer/reject@1280`, `composer/steer@390`, `el-gate-card/*`, `el-action-bar/*` (these cells should now capture the card; update `elements.spec.ts` selectors so `el-action-bar` shoots the card's button row).

## B · Board row (1c)

1. Row grid at ≥1280: `22px minmax(0,1fr) 150px 110px auto`; at 1024–1279 drop the mini-chain column; ≤1023 as W2.
2. Title one line, ellipsis. Meta line one line, ellipsis: `repo · bead · template · 15h ago · <reason>`, where reason is the tail in accent-300 for needs-you rows (`approve code_review`, `verify hit its cap · 3 attempts`, `agent asks: <first 60 chars of the question>`, `spend cap reached`, `escalation waiting for your reply`) and neutral-300 otherwise (`Task 3/6 · <task title>` for running, `retry in 4m` for rate-limited).
3. One button per row, right column, sized to the state: Approve (needs-you gate), Steer & retry (capped), Raise budget (budget), Answer (question), Reply (escalated), Resume (paused). Running / waiting / done rows have no button (column empty, grid unchanged so titles align).
4. The inline action row (`approve · Approve · Reject… · Escalate…`) is removed. Reject, Escalate, Skip live in the peek and item page (already there).
5. Row height 63px at compact, 72px at comfortable. Whole row click opens the peek; the button click acts without opening the peek and shows the pending state on the row (button → spinner → row moves group).
6. Archive selection mode and the hover ring unchanged.

Tests: board renders exactly one button in needs-you rows and none in running rows; reason text per state.
Cells: `board/default@1280`, `board/long@1280`, `board/default@1100`, `board/many@1920`, `flow-gate-approve/*` (approve from board variant if the flow exists; else add `flow-board-approve`).

## C · Phone board row (2e)

1. ≤767: grid `22px minmax(0,1fr) auto`, 64px rows, title one line, meta one line `repo · 15h · <reason>`, node label right. No buttons. Bead id and template drop from the phone meta (present in the sheet).
2. Tap = navigate (unchanged); long-press = sheet (unchanged); the sheet's action set = A's button row + More actions for that state.
3. Group headers sticky under the facet row as today.

Cells: `board/default@390`, `board/long@390`, `board/many-scrolled@390`, `board-peek/gate@390`.

## D · Chains editor (2b)

1. Page header: template name (17px/500) · `valid` badge · `8 nodes · 4 gates` · spacer · **template ▾** dropdown (replaces the templates list column; contains every template + "New…" + "Duplicate" + "Delete") · **YAML** toggle button · Revert · Save.
2. Pill strip stays as built (⊕ inserts, drag to reorder, ⚑ / ⟳ / ⛨ marks).
3. The two columns below (templates list, "select a node", YAML pane) are removed. Selecting a pill opens **one editor card** under the strip: grid `minmax(0,1fr) 260px` — node form on the left (tasks chips + "+ task", gate after, fix loop, reject to, auto-escalate), the node's YAML fragment on the right (editable, "edits either side"); footer row: duplicate · remove · legend.
4. YAML toggle swaps the editor card for the full-file YAML with the diff-vs-saved tab (W78 behaviour), keeping the pill strip.
5. Nothing selected: the editor card shows the template summary (nodes count, gates, used by N items, path) instead of "select a node".
6. ≤1023: editor card stacks (form, then YAML fragment). ≤767: pill strip scrolls; template dropdown becomes a full-width select.

Tests: selecting a pill opens the card with that node; YAML toggle swaps; template dropdown switches template; no `.templates-list` column rendered.
Cells: `settings-chains/default@1280`, `settings-chains/editor@1280`, `settings-chains/editor@390`, `settings-chains/long@1920`, `flow-settings-save-roundtrip/04–06`.

## E · Analytics (2c)

1. Header row: "Analytics" · `completed work items · last 8 weeks` · spacer · range ▾ · repo ▾ · template ▾ (unchanged controls).
2. Three headline tiles, 30px numbers, hairline dividers between, no boxes: **Completed** (`+8 vs previous · $7.08 each`), **Lead time** (`median create → merge · 25% waiting on you`), **Cost** (`88 fix cycles · 6 capped · 9 rejected gates`). Delta text uses `--color-neutral-500`; a negative trend uses the same colour (no red/green).
3. The six removed tiles (human wait, unplanned touches, MR→CI, fix cycles, rejected gates, per-item cost) live in captions above or in the table footers: "Why items stopped" table footer gets `unplanned touches 1.25 per item · open MR → green CI 20m median`.
4. Throughput chart and the three tables unchanged. Empty state per W4.7.
5. ≤767: tiles stack, one per row, 26px numbers.

Tests: exactly three `.kpi` tiles; footer line present; totals consistency (W8.4) still asserted.
Cells: `analytics/default@1280`, `analytics/long@1920`, `analytics/default@390`, `analytics/empty@1280`, `analytics/default@1280~light`.

## F · Timeline scope (3a)

1. Timeline tab gets the same `this node · all` segmented control Tasks has, same position (top-right of the tab body), `this node` default. Selection follows the stage-graph pill; both tabs share one scope state.
2. `this node`: the selected node's events render flat (no group card), newest first, right pane opens on the first event. Tab count = this node's events (`Timeline · 5`).
3. Below the list, one folded row: `▸ 7 earlier nodes · 265 events · 12:58 – 20:42` with a "show all" link; clicking either switches the control to `all`, which renders today's grouped view with the selected node's group open.
4. Nodes with no events yet (future nodes) are not listed under `this node`; under `all` they appear as today.
5. Existing filters (all / gates / tasks) stay inside the right pane.

Tests: default scope is this-node; count follows scope; folded row shows the correct remainder; switching a pill rescopes.
Cells: `item-timeline/default@1280`, `item-timeline/long@1280`, `item-timeline/capped@1280`, `item-timeline/long-scrolled@390`.

## G · Documents (3c)

1. Same `this node · all` control as F, plus a filter input (`filter documents…`) at the top; filtering searches title, path, node and hook, across all nodes regardless of scope.
2. Sections, in order: **Gate document** (pinned when `pending_gate`/`gate_artifact` exists; accent background; always visible in both scopes) → **`<node>` · written by this node** → folded rows, one per other node with documents: `▸ verify · 3 reviews · 9 sessions` and the count; click expands that node inline. Under `all`, every node section is expanded in chain order.
3. Row = icon · title (2-line clamp) · subtitle `node · hook · time` (+ `<ShortId>` of the session for summaries) · one small kind label (`spec` / `plan` / `review` / `session` / `escalation`). No path in the row: the path lives in the detail pane header and in the row's `title` tooltip. "attached at intake" is a 📎 in the subtitle with tooltip.
4. Tab count follows scope.
5. Selection and reload restore work as today (W6).

Tests: gate doc pinned; this-node section lists only that node's docs; folded rows count correctly; filter matches across nodes.
Cells: `item-documents/default@1280`, `item-documents/long@1280`, `item-documents/running-long@1280`, `el-inspector/gate-documents-long@1280`, `item-documents/long@390`.

## H · Document titles — no more shas

Root cause: `title` for a session summary is taken from the first `# ` heading; summaries written by some hooks (`on.review.local.run`, `on.mr.describe`, security reviews) have none, so the indexer falls back to the session id.

1. Frontend (`docTitle()` in one place, used by the Documents list, detail pane, doc modal, search results, board peek): if `title` is empty or matches `/^[0-9a-f]{8}…?[0-9a-f]{4,}$/` or equals the session id → derive from `content`: first non-empty line that is not a heading marker, front-matter or a blockquote, stripped of markdown, cut at the first sentence end or 90 chars. Prefix by kind when the line is generic: `Session · ` only if the derived line is under 12 chars. Never show a bare id as a title anywhere.
2. Backend (bead, not this wave — file `Kraft-<new>`): the summary prompt for every agent hook ends with "Start the file with a one-line `# ` title that states what was done". The indexer then stores a real title and rule 1 becomes a fallback only.
3. Search results and the board peek use the same `docTitle()`.

Tests: `docTitle()` on a heading-less body returns the first sentence; on a body starting with front-matter skips it; on a real title returns it unchanged; a sha never appears as `.doc-title` text in Documents (assert with the long fixture, which has two heading-less summaries — add them to `fixtures.ts documentsFor`).
Cells: `item-documents/long@1280`, `search/page@1280`.

## I · Peek actions mirror the gate card (1e)

1. The peek's action card is the same component as the item page card (section A), rendered narrow: header (⚑ + "approve to continue" · `gate · waiting`) · stats line hidden · button row **primary · secondary · More actions ▾**. Per state: gate → Approve · Reject · More; capped → Steer & retry · Escalate · More; budget → Raise budget · Escalate · More; question → Answer · More; paused → Resume · Steer · More; escalated → Reply · Dismiss · More; running → Pause · More; done → Open MR ↗ · More.
2. More actions in the peek = the item-page menu plus **Open item →** as the first row. "Read document" is not in the peek (the gate document opens on the item page).
3. Composers (Reject note, Steer, Answer, Reply) open inline inside the peek below the button row, as they do on the item page; submit closes the composer and shows the pending state; the peek stays open.
4. Phone peek sheet: same card; More actions opens the action sheet.
5. Density rule, one sentence in the README: board row = primary only; peek = primary · secondary · More; item page = primary · secondary · Read document · More + composers.

Tests: peek renders the shared card component; per-state button sets as in the table; Open item is the first menu row.
Cells: `board-peek/gate@1280`, `board-peek/capped@1280`, `board-peek/escalated-long@1100`, `board-peek/gate@390`, `flow-peek-open-close/*`.

## J · Escalating items leave "Needs you"

1. `deriveState` display group: `escalating` (an escalation agent session is running) belongs to **Running**, not Needs you. It returns to Needs you as `escalated` when the agent posts its message, or as the underlying stop state if the agent exits without one.
2. Row (1c): icon = robot (accent), node label as usual, meta tail `escalation running · 3m` in neutral-300, no primary button. Peek card: the "escalation running" pill · More (Stop escalation inside More).
3. Sidebar badge and the "N need you" footer count exclude escalating items. Board group counts follow.
4. Sort inside Running: escalating items first (they are the ones most likely to come back).
5. Item page hero status chip reads `escalating` (accent-900, robot icon); gate card shows the running pill + More, as today's escalating state does.

Tests: deriveState/group mapping for escalating; badge count excludes escalating; row has robot icon and no button; sort order.
Cells: `board/default@1280` (fixture has one escalating item), `item/escalating@1280`, `board-peek/*` for the escalating item (add a case), `composer/escalating-pill@1280`.

---

Out of scope for W11: log pane, timeline right pane, settings pages other than Chains. Decisions made; anything not covered above follows the as-built behaviour. Questions → `e2e-shots/QUESTIONS.md`, skip that rule, continue.
