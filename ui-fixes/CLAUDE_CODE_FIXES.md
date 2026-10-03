You are in a git worktree of the Kraft repo. Implement the UI fixes below so
the app matches the design prototype. The full spec is in this prompt.

Setup
1. Run `git worktree list`. The first entry is the MAIN checkout; call it $MAIN.
   Use $MAIN/design/handoff_v4 (and $MAIN/design/** if needed) as the design
   reference. Read it, never write there.
2. Save this whole prompt to ./ui-fixes/CLAUDE_CODE_FIXES.md in THIS worktree
   and commit it first, so the spec travels with the branch.
3. Run the dev server on a different port than the default; another agent is
   using the default port in $MAIN.

Rules
- Scope: frontend/src/ng (+ the one backend endpoint in D3). Nothing else.
- One commit per item, ID first in the message ("S1: …").
- Update or add tests next to each change. Keep the existing a11y behaviour
  unless an item says otherwise. Run the unit tests after each item.
- Don't touch frontend/sweep/compare (another agent owns it).
- If an item conflicts with existing tests or behaviour in a way the spec does
  not settle, stop and list it under "Questions" in the final summary rather
  than guessing.

## Sidebar (shell/Sidebar.tsx, sidebarPref.ts, shell.css)
- S1 Unpinned hides the sidebar completely (0px, no icon rail). An 8px
  invisible strip on the left edge reveals it as a 210px overlay on hover; it
  hides 180ms after the pointer leaves. Keep the keyboard-focus reveal
  (:has(:focus-visible)) so it stays reachable.
- S2 Move the pin control to the top-right of the sidebar head, beside
  "Kraft · live". Panel-left icon with two states (open / close). Title:
  "Pin sidebar" / "Collapse sidebar". Keep aria-pressed.
- S3 First load (nothing stored) is pinned at every width; drop the 1280px
  rule. After that, the stored choice in localStorage `kraft.sidebar.v2` wins.
- S4 Unpin is not collapse. Unpinning (the button or ⌘\ / Ctrl+\) only changes
  the mode: if the pointer is over the sidebar it stays open, and hides when
  the pointer leaves (180ms). While unpinned, the pointer leaving is the ONLY
  way it closes; it stays open while the pointer is over it, including after
  choosing a row. Remove retract() on unpin, Esc-to-close, window-blur close,
  and row-click close.
- S5 Footer shows the version plus an "update" notice (dot + word) when one is
  available, linking to About. Move bind:port and the restart-pending /
  installed-older warnings to the About page.

## Doc viewer (item/DocViewer.tsx, item.css)
- D1 Replace the centred dialog with a full-height right drawer:
  min(560px, 92vw), scrim rgba(0,0,0,.4); click scrim or Esc closes. Header:
  title, path (mono), "written by …" stacked.
- D2 "⤢ full screen" / "⤡ exit full screen" toggle in the header; full screen
  = inset:0. Closing resets it.
- D3 One "Open in editor" button in the header, plus "Copy path". Remove the
  five editor buttons.
  - Backend: add GET /editors returning only editors whose executable is found
    on the server machine (reuse _EDITORS + shutil.which in
    src/kraft/api/routes/search.py), plus the configured default. Same
    local-client restriction as the open endpoint. Tests in tests/api.
  - The button opens the default editor; its ▾ menu lists the other available
    editors. None found: disabled, "No editor found on this machine".
  - Settings: add "Default editor" (Appearance, or a new Editor section),
    listing the available editors plus "System default". Persist server-side.
- D4 When opened from search, carry the query (?doc=<id>&q=<query>). Bar under
  the header: search icon, the query, "n of N" (or "matched in the title"),
  ↑/↓ to step through matches, "from search" on the right. Highlight matches in
  the body; scroll the current one into view with scrollTop (never
  scrollIntoView).

## Log viewer (item/panes/Log.tsx, item.css)
- L1 Full screen covers the viewport (position:fixed; inset:0), not a dialog.
  Header: crumb + task title, "log · N lines", the existing controls (source
  chips, follow, copy), "⤡ exit full screen". Body 13px / 1.7, timestamp in
  its own column (min-width 44px). Esc exits.

## Item header (item/Top.tsx, item/header/*)
- H1 Title rename: blur saves (same as Enter). Blank on blur restores the old
  title. Esc still cancels.
- H2 Escalate and Mark complete open as cards anchored under the main button,
  like Pause and Cancel (360px, arrow notch). Remove their modal dialogs.
- H4 Main button becomes one button with ▾ inside (min-width 118px). Menu opens
  on hover and on click, overlaying the button at the same width. Remove the
  separate chevron toggle.
- H5 Remove "View run log" from the ⋮ menu, plus onRunLog / runLog in
  ItemPage.tsx and its test. Do NOT build a replacement.
- H6 Diff line shows open review threads:
  "N files +A −D · K open threads · Review changes"; omit the middle when K=0.

## Search (shell/SearchOverlay.tsx, search.css)
- Q1 Needs-you rows for items waiting at a gate are action rows: "Review
  <gate_id>", gate diamond glyph, sub "id · repo". Enter opens that gate's
  review directly.
- Q2 Rows get an icon per kind (item: box, bead: circle-dot, page: its nav
  icon), an optional status tag, a "where" label, and ⏎ on the active row only.
- Q3 Highlight the query in titles as well as snippets.
- Q5 Filters button always at the right of the tab row, with the active-filter
  count. Filters render as chips (label + value) in a row below; keep
  "Filters narrow Documents only".

## Done when
- Every item is implemented and committed, unit tests pass, typecheck and lint
  are clean.
- Final summary: one line per ID (done / partial / skipped + why), files
  touched, and a "Questions" list.
