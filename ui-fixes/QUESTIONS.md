# UI fixes: summary and open questions

Spec: `ui-fixes/CLAUDE_CODE_FIXES.md` (same folder).
Branch: `worktree-bridge-cse_01EeQB5nXRdgXJeNdW2ffuce`, one commit per item (`git log --oneline main..HEAD`). Not pushed.

## Summary

All 19 spec items are implemented: S1–S5, D1–D4, L1, H1, H2, H4, H5, H6, Q1, Q2, Q3 and Q5. The spec has no H3 or Q4.

Verification:
- Frontend unit tests pass (2450 of 2450, `just test-ui`).
- The backend tests touched pass (188).
- `tsc -b`, `just lint`, `just check-tests` and `just intent` are clean.
- Every item was clicked through by hand in a dev instance.

Design reference: `$MAIN/design/handoff_v4` is empty apart from a `.DS_Store`. The reference files are in `$MAIN/design/design/handoff_v4`. The H4 main-button behaviour came from `design/handoff_uxv2/reference/Kraft Prototype V2.dc.html`.

Changes outside `frontend/src/ng` (see Q2 and Q6 below):
- `frontend/src/types/settings.ts`: `Theme.editor`.
- `src/kraft/config.py`: `EDITORS` and `Theme.editor`.
- `src/kraft/api/routes/search.py`: `GET /editors`.
- `tests/api/test_retry_open_log.py` and `tests/api/test_settings.py`.
- `vscode/schemas/theme.schema.json`, regenerated with `just schemas`.

Two bugs were found and fixed in the D3 commit:
- **Esc closed the whole drawer:** pressing Esc in a menu opened inside a dialog also closed the dialog. Popover now handles Esc first.
- **Menus hidden under dialogs:** popovers rendered below dialogs. Popover z-index was raised from 50 to 65.

## Questions

1. **D1 scrim colour.** The spec asks for `rgba(0,0,0,.4)`. `frontend/src/ng/css.contract.test.ts` forbids colour literals anywhere but the generated `theme.css`, whose generator `dev/gen_theme.py` is outside the scope. The drawer uses the dialog backdrop's existing mix, `color-mix(in oklab, var(--side) 70%, transparent)`. Should a `--scrim` token be added to `dev/gen_theme.py` to get real black at 40%?

2. **D3 needs more than the one backend endpoint.** Storing the default editor on the server needed a home: `theme.yaml`'s model forbids unknown keys. An `editor` field was added to `Theme` in `src/kraft/config.py`, saved through the existing `PUT /theme`. The editor ids now live in one place, `config.EDITORS`, and `_EDITORS` in `search.py` is built from it. The VS Code schema was regenerated. Is extending `Theme` (theme.yaml) acceptable, or should the default editor live somewhere else?

3. **H4 click behaviour.** The spec says the menu opens on hover *and on click*. In the prototype a click runs the main action.
   - **Pointer:** hovering opens the menu over the button, so the click lands on the menu's first row (the main action). The behaviour barely differs.
   - **Keyboard and touch:** acting now takes two presses: open the menu, then pick the main row.
   - **Width:** the button grows to fit its widest menu row, about 158px for a running item instead of 118, so the menu fits at the same width without clipping or ellipsis.

   Is the two-press keyboard and touch path acceptable? Should a click act directly instead, as in the prototype?

4. **Q1 drops the item title.** Per the spec, a gate row reads `Review <gate_id>` with sub `id · repo`, so the item's title no longer shows in the row. It appears only in the footer hint ("⏎ reviews <title>"). The query still matches on the title. Should the title be in the row, for example in the sub?

5. **S5 "update" trigger.** The footer's "update" notice shows when:
   - the release feed has a newer version (`GET /update` `behind: true`), or
   - an installed update is waiting on a restart, or
   - an older release is installed under the running server (a rollback).

   About explains each case. Should it show only for a newer release on the feed?

6. **Shared `ui/Popover` changes.** These affect every popover in the app:
   - `notch`: an arrow toward the anchor (H2).
   - `dirty`: a stray outside press does not close a card holding typed text. This keeps the old Escalate and Complete dialogs' protection.
   - `over`: lays the popover over its anchor at the anchor's width (H4).
   - Esc is handled in the capture phase, so it closes the popover alone.
   - z-index raised from 50 to 65, above dialogs.

   Is changing the shared component acceptable, or should these stay local to the item header?

## Resolved

Answers applied as new commits on this branch (ID first):

| # | Answer | Commit |
|---|---|---|
| 1 | `--scrim` (black at 40%) added to `dev/gen_theme.py` as `:root { --scrim: rgb(0 0 0 / 40%); }`. `theme.css` was regenerated (one added line; the checked-in-output test passes). The drawer uses `var(--scrim)`, pinned in `css.contract.test.ts`. | `e08a5c79f` D1 |
| 2 | Default editor in `Theme` / `theme.yaml` kept. No change. | none |
| 3 | The label is the action (click, Enter, Space). ▾ is its own target inside the one bordered button and opens the menu (click, ↓, Enter, Space; focus alone opens nothing). Hover still opens the menu over the button at its width. The menu's ▴ sits over ▾, so a press there does not run the action (mutation-checked). Escape returns focus to ▾. The width stays about 158px. | `b27bd4f5b` H4 |
| 4 | A gate row reads `Review <gate_id>` with sub `<title> · <id> · <repo>`. The title is the part that shortens, with an ellipsis (`data-allow-ellipsis`), and is highlighted with the query. | `d6e4afe1e` Q1 |
| 5 | All three update triggers kept. No change. | none |
| 6 | Checked in the browser: with the ⋮ menu open, a toast overlapping it is the top element (toasts z 70, popover z 65, dialog z 60), so no fix was needed. That order is now pinned in `css.contract.test.ts`. | `c04f63285` D3 |

Follow-up to 6: toasts do overlap the item header (fixed top-right, as the header's main button is). A toast under the pointer used to close the main button's hover menu, because the pointer had "left" it. Fixed in an H4 follow-up commit: when the pointer leaves for the toast region, the toast counts as part of the hover area, and the 160ms close starts only when the pointer leaves both. Tested and mutation-checked in `MainButton.test.tsx`.

Verification after these commits:
- `just test-ui`: 207 files, 2454 tests passed.
- `tsc -b`: clean.
- `just lint`: ruff check and format clean (727 files).
- `just check-tests`: ok.
- Backend `tests/test_theme_contrast.py`, `tests/api/test_retry_open_log.py`, `tests/api/test_settings.py`: 117 passed.

## PR 502 review fixes

Applied as new commits on this branch (ID first):

| # | Fix | Commit |
|---|---|---|
| 1 | **H1:** clicking the title to rename now resets the `settled` flag. The reset had never landed in the first H1 commit, so a second rename ended by a click away sent nothing. The new test renames once with Enter, then again ending with a click away, and checks that both saves are sent; it fails without the fix. | `d12ea00c5` H1 |
| 2 | **D2:** in full screen the document is a centred 760px reading column (`.dv-drawer.is-full .dv-body > * { max-width: 760px; margin-inline: auto; }`), checked in the browser at 1600px. | `5d12c36f2` D2 |
| 3 | **D3** (this PR): on macOS an editor counts as available when its app is installed, even if its command-line tool is not on `PATH`. It is then launched with `open -a "<App>" <path>`. Ids map to apps: code → Visual Studio Code, cursor → Cursor, zed → Zed, obsidian → Obsidian. `POST /documents/{id}/open` and open-worktree use the same launcher. Tests cover the PATH and the app paths, both listing (`GET /editors`) and launching; the app rows fail when the fallback is removed. The HTTP API doc is updated. | `45f4414a4` D3 |

**Deviation in 3:** the app is detected by its bundle (`/Applications/<App>.app` or `~/Applications/<App>.app`), not with `open -Ra "<App>"`. `-R` means "reveal in Finder", and with `-a` and no file it would most likely reveal the app in a Finder window every time the editor list loads. The bundle check has no side effects but misses apps installed elsewhere, such as Setapp's folder. Say if `open -Ra` is wanted anyway.

Also fixed:
- **H4, a review finding** (`b4d072c5d`): a toast that times out under a still pointer gives no `mouseleave`, so the main button's hover menu could stay open until the pointer moved. The toast container is now watched while it holds the hover, and a toast leaving it starts the 160ms close. Tested and mutation-checked.
- **Q2, CI's playwright job** (`1c89e991c`): `e2e/search.spec.ts` located document rows by the name `<title> <kind>`. Q2 moved the kind to a tag after the title block, so neither locator matched. The test now anchors on the title and checks the tag on its own. This is in `frontend/e2e`, outside `frontend/src/ng`, because it is the e2e test of the Q2 change.

Verification after these commits:
- `just test-ui`: 207 files, 2459 tests passed.
- `tsc -b`: clean.
- `just lint`: ruff check and format clean.
- `just check-tests`: ok.
- Playwright e2e, run locally as CI's `e2e-ci` does: 22 passed.
- Backend `tests/api/test_retry_open_log.py`, `tests/api/test_settings.py`, `tests/api/test_deps.py`, `tests/test_theme_contrast.py`, `tests/test_config_schemas.py`: 248 passed.
