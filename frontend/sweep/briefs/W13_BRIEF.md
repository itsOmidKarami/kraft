# W13 build brief — Documents rows, Timeline rounds

Decisions made in chat (2026-09-14): Documents rows led by **hook · attempt**; Timeline left pane grouped by **rounds → sessions**, right pane an **event stream of the selection**. Same loop as W11/W12; branch `wave/W13`, one commit per section, `node sweep/wave.mjs W13` with the entry below, full sweep, collect, append RUN-SUMMARY, stop. Reuse: ScopeChips (this node / all), ShortId, ShortPath, RowText, Tabs roving.

`waves.json` W13: screens `item-documents, item-timeline, el-inspector, el-right-pane, flow-inspector-tabs-selection, search`; rules `no-flag offscreen "."`, `no-flag clipped-v "."`, `no-console "^(?!login/)"`, `flow-completes "^flow-inspector-tabs-selection/"`, `no-new-flags`.

---

## A · Backend: run info on document rows (small)

1. `GET /work-items/{id}/documents` joins `worker_sessions` on `worker_session_id` and adds to each row: `attempt: int|null`, `round: int|null`, `session_status: str|null`. Null for artifacts and attachments. Type in `frontend/src/types/documents.ts` (`WorkItemDocument`).
2. Test in `tests/` for the join; fixture in `sweep/fixtures.ts` `documentsFor()` emits the three fields (attempt 1 default; the `long` variant gives the implement sessions rounds 0–2).

## B · Documents rows (`Inspector/Documents.tsx`)

1. Row, session_summary kind:
   - line 1 (13px, `--color-text`): `<hook_point> · <run label>` where run label is `turn N` if hook is `escalation`, `round N` if `round > 0` or the hook matches `/fix|judge/`, else `attempt N`. If A's fields are absent (older server) show just the hook.
   - line 2 (12px, muted, one line, ellipsis): the cleaned title (rule 3). Omitted when empty.
   - right: kind tag as today; time `21h ago` moves into line 1's tail.
   - Row `title` attr = path; session id is NOT row text (it is in the pane header via ShortId).
2. Row, artifact/attachment kind (spec, plan, review): unchanged shape (title first) but line 2 is `<hook_point> · <time>`; drop `node_id` from every row — the node is the section header in both scopes.
3. `cleanTitle(doc, item)`: strip a leading/trailing bead id in parentheses or after an em dash (`(Kraft-df4tc)`, `— Kraft-df4tc`, `Kraft-df4tc:`), strip the item title if the remainder contains it case-insensitively, collapse whitespace, trim ` —·:-`. Return `""` if what remains is under 3 chars. Unit tests: the four title shapes in the screenshot (`Chain-review diff review (Kraft-df4tc)` → `Chain-review diff review`; `Security review — Kraft-df4tc (chain review: hook configs and escalation logic)` → `Security review`; `Fix-loop judge: verify (round 4 decision)` unchanged; a title equal to the item title → `""`).
4. Pane header (Documents detail): eyebrow `<hook_point> · <run label> · <time>` (mono, muted) · `ShortId` of the session; title (cleaned, 1 line, ellipsis, full in `title`); path via ShortPath. For artifacts the eyebrow is `<kind> · <hook_point> · <time>`. Remove the body's `Status: … · repo … · path …` preamble if W12 has not already.
5. Filter box matches hook, run label, cleaned title, path.
6. Sort within a section: by session `created_at` desc when known (A), else `indexed_at` desc — the newest run on top.

## C · Timeline left pane: rounds → sessions (`Inspector/Timeline.tsx`, `timelineHelpers.ts`)

1. Build the model from events + sessions for the scoped node(s): `Round { n, startedAt, endedAt, sessions[], findings, verdict }`. Boundaries: `node_started` opens round 0 (label "round 1" if there is more than one, else no round header at all); each `fix_cycle_started` opens the next round; `judge_verdict` closes it with `verdict` + `reasoning`; `findings_measured` attaches its count and list. Sessions belong to the round whose interval contains `created_at`. Escalation sessions form their own group "escalation · turn N". Events that fit no session (`gate_requested/approved/rejected`, `work_item_*`, `task_progress`) are node-level rows placed chronologically between rounds.
2. Render (scope "this node"): `ROUNDS · 4` header with ScopeChips; then, newest first:
   - round header row (folded/unfolded, ▸/▾, 44px): `round 4` · `17:05 → 17:18 · 13m` · right: `judge: stop` / `judge: continue · 5 findings` / `running`. The current round starts unfolded; older ones folded.
   - inside: one row per session, 40px: hook (13px) · `done · 1m` (muted) · right: `17:18`. Findings row `3 findings · 1 critical` under the sessions that produced them.
   - node-level rows in the same list at their time: `gate_requested · code_review · 17:20`, `node_started · 16:02`.
3. Scope "all": nodes fold first (`verify · 4 rounds · 1h 16m`), rounds inside, as today's node grouping does.
4. Selection: clicking a session row selects that session (right pane = its stream, rule D); clicking a round header selects the round; clicking a node-level row selects that single event. URL state uses the existing `selection` param (`session:<id>`, `round:<node>:<n>`, `event:<seq>`). Existing `#tab=timeline&selection=` links for a bare event keep working.
5. Left rows never show the raw event type name (`worker_session_exited`); the hook + verb is the label. Keyboard: rows are buttons, arrow keys move, Enter selects, Left/Right fold/unfold a round header.

## D · Timeline right pane: event stream (`RightPane/Events.tsx`)

1. Header: for a round, `round 4 · 13m · 4 sessions · 3 findings → stop`; for a session, `on.test.run · round 4 · done · 1m` + `view log` button + ShortId; for a single event, its type and time.
2. Body: vertical rail (2px, `--color-border`), one row per row-worthy event:
   - a session is ONE row: hook in `--color-text`, then `created 17:05:50 · started +0s · exited +1m 02s · done` in muted mono; `view log` link at the right (existing behaviour).
   - `findings_measured`: header row `3 findings` then one line per finding: severity chip · `file:line` (ShortPath) · message (1 line, ellipsis, full in title). Existing `findingsOf` helper.
   - `judge_verdict`: `judge · stop` + reasoning as a quoted line.
   - gates: `gate_requested · code_review · artifact <ShortPath>` / `gate_approved` / `gate_rejected` + note.
   - `task_progress` runs collapse to one row per session: `Task 3 → 6 of 6`.
   - a gap > 2 min between consecutive rows renders a hairline row `waiting 4m` (muted, 11px, centered on the rail).
3. Timestamps: absolute `HH:MM:SS` on the first row of each minute, `+Ns` relative on the others (the existing `clock/elapsed` helpers).
4. Empty selection: pane shows the whole scoped node's stream (same rules), so the tab is useful before any click.
5. Phone (≤767): the left list is the tab body; selecting pushes the stream as the Detail view (W2's List/Detail toggle already exists).

## E · Tests & cells

- vitest: `roundsOf(events, sessions)` on the fixture item with 3 fix cycles (capped item) yields 4 rounds with correct boundaries and verdicts; escalation sessions group separately; `cleanTitle` cases; stream row collapsing (3 session events → 1 row; task_progress → 1 row); gap rows appear only over 2 min.
- Cells to look at: `item-documents/default@1280`, `item-documents/long@1280`, `item-documents/running-long@390`, `item-timeline/default@1280`, `item-timeline/capped@1280`, `item-timeline/long@1920`, `item-timeline/long@390`, `el-inspector/gate-timeline-long@1280`, `el-right-pane/gate-timeline-long@1280`, `flow-inspector-tabs-selection/*`.

Out of scope: Tasks tab, log pane, search results (they keep the raw `title` for now).
