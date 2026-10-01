# ux2-fix-attachments · the item page shows the spec and plan attached at intake (Kraft-9d8b2.29)

Branch `kraft/ux2-fix-attachments`, on `1a3fa5802`. **Sweep port: 4394.**

Baseline: `node sweep/wave.mjs ux2-W5 --baseline` before the cell existed.

## `node sweep/wave.mjs ux2-W5`

7/7 rules pass · 1 changed (the new cell) · 0 cleared · 0 regressions. No existing item carries attachments in the mock, so no other cell moved.

New cell: `ng-item/attached@1280` (cases/ux2-fix-attachments.ts, rules in waves/ux2-fix-attachments.json): the chain pane's Overview with an `attached` row, `spec doc-search-cache.md` and `plan doc-search-cache.md` on separate lines, each a link that opens the DocViewer.
