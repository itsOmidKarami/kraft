# ux2-fix-stroke · a stroke role for the graph, and a danger hover that reads

Branch `kraft/ux2-fix-stroke`, on `8280bc3b3`. Beads Kraft-fyeet and Kraft-xjv0h.

**Sweep port: 4391.** Baseline: `node sweep/wave.mjs all --baseline` with the three UI files (`theme.css`, `graph.css`, `ui.css`) at `origin/main`, 1818 cells. After: the same command with the change.

## `node sweep/wave.mjs all`

175 cells changed · 0 flags cleared · 2 reported newly flagged, both `setup` timeouts that are flaky and not this change (below).

- `ng-chains/gate@1280` and `ng-chains/node-empty@1280` timed out on `locator.waitFor(".canvas, .tpl-note")` in the after run. `gate` passed on the re-shoot. `node-empty` failed 2 of 3 re-shoots with the change and 1 of 4 on unmodified `origin/main`, so it is a race in the cell, not the colours.
- Every changed cell is an `ng-*` screen or an `ng-*` flow step. No shipped-UI cell (`board`, `item`, `settings-*`, `composer`, ...) changed.
- No cell's hover state rests on `.btn-danger` (`ng-chains/remove` moves the pointer off it on purpose), so no cell changed for the danger button. The unit test pins it.

Why the changed cells are only the graph: the diff touches `--stroke` (new token), the graph's own rules in `graph.css` (`.glyph-box`, `.glyph-gate`, `.edge`, `.dot`, `.arc.tone-idle`, `.mark-start`, `.mark-end`, `.strip-edge`, `.gateview-line`) and `.btn-danger`'s hover/focus label. I looked at the cells with the most unexpected names (`ng-shell/long-crumb@1024`, `ng-library/node@1280`, `ng-harnesses/harness@1280`, `ng-draft-item/default@1280~light`, `flow-ng-search-keyboard/05-reopen-enter-goes@1280`): each draws a chain graph, or the harness lane, which reuses `NodeGlyph`.

Before and after, `ng-item/running@1280`: `running-dark-{before,after}.png`, `running-light-{before,after}.png` in this folder.

### Changed cells (175)

- `flow-ng-cancel`: `00-start@1280`, `01-toggle-focus-opens-panel@1280`, `02-arrows-to-cancel@1280`, `03-enter-opens-card@1280`, `04-reason@1280`
- `flow-ng-chain-add-node`: `04-create@1280`, `05-empty-node@1280`
- `flow-ng-chain-publish`: `03-publish@1280`
- `flow-ng-harness-never-blocks-publish`: `00-start@1280`, `01-no-problems@1280`, `02-set-never@1280`, `03-names-the-task@1280`, `04-publish-blocked@1280`, `05-set-back@1280`
- `flow-ng-item-draft-add-node`: `00-start@1280`, `01-seam@1280`, `02-pick@1280`, `03-create@1280`
- `flow-ng-item-draft-apply`: `00-start@1280`, `01-override@1280`, `03-apply@1280`, `04-changed-for-this-item@1280`
- `flow-ng-item-draft-leave`: `00-start@1280`, `02-stay@1280`
- `flow-ng-move-to-library`: `06-publish@1280`
- `flow-ng-node-keyboard`: `00-start@1280`, `01-tab-into-chain@1280`, `02-cmd-enter-node-view@1280`, `03-arrows-to-a-task@1280`, `04-enter-opens-pane@1280`, `05-escape-collapses@1280`, `06-escape-back-to-chain@1280`
- `flow-ng-pane-collapse`: `00-start@1280`, `01-collapse@1280`, `02-pick-node-stays-collapsed@1280`, `03-other-item-stays-collapsed@1280`, `04-rail-expands@1280`, `05-escape-collapses@1280`
- `flow-ng-peek-open`: `05-ctrl-enter-opens-the-item@1280`
- `flow-ng-retry-task`: `00-start@1280`, `01-open-node@1280`, `02-focus@1280`, `03-task-pane@1280`, `04-retry-confirm@1280`, `05-retry-sent@1280`
- `flow-ng-review-request-changes`: `00-start@1280`, `05-submit@1280`
- `flow-ng-search-keyboard`: `05-reopen-enter-goes@1280`
- `ng-chains`: `bottom-empty@1280`, `bottom-handler@1280`, `bottom@1280`, `bottom@1280~light`, `canvas@1280~light`, `canvas@1920`, `canvas@1920~light`, `change-base@1280`, `gate@1280`, `node-task@1280`, `node@1280`, `node@1280~light`, `node@1920`, `node@1920~light`, `pane-gate@1280~light`, `review-problems@1280`, `switcher@1280~light`, `task-menu@1280`
- `ng-draft-item`: `default@1280~light`
- `ng-gallery`: `default@1280`, `default@1280~light`, `default@1920`, `default@1920~light`, `full@1280`, `keyboard@1280`, `mono@1280`, `overlay@768`
- `ng-harnesses`: `empty@1280`, `floor-config@1280`, `floor-config@1280~light`, `floor@1280`, `floor@1280~light`, `harness-lane@1280`, `harness-lane@1280~light`, `harness@1280`, `harness@1280~light`, `problems@1280`, `problems@1280~light`, `review-problems@1280`, `review-problems@1280~light`
- `ng-item-draft`: `applied@1280`, `changes@1024`, `changes@1024~light`, `changes@1280`, `changes@1280~light`, `changes@1920`, `changes@1920~light`, `config-edit@1280`, `passed@1280`, `problems@1024`, `problems@1280`, `seam-menu@1280`
- `ng-item-gate`: `pane@1280`, `pane@1280~light`, `passed@1280`, `reject@1280`, `view@1024`, `view@1280`
- `ng-item-node`: `failed@1280`, `needs-you@1280`, `running@1024`, `running@1280`, `running@1280~light`
- `ng-item-step`: `parallel@1280`, `parallel@1280~light`
- `ng-item-task`: `log-long@1280`, `log@1280`, `log@1280~h700`, `output@1280`, `overview@1280`, `overview@1280~light`, `thread@1280`
- `ng-item`: `cancel-card@1280`, `capped-long@1280`, `capped@1280`, `chain-config-capped-long@1280`, `chain-config-capped-long@1280~h700`, `chain-config@1280`, `conflict-long@1280`, `conflict@1280`, `escalated-long@1280`, `escalated@1280`, `failed-long@1280`, `failed@1024`, `failed@1024~h700`, `failed@1024~light`, `failed@1280`, `failed@1280~h700`, `failed@1280~light`, `failed@1920`, `failed@1920~h700`, `failed@1920~light`, `kebab@1280`, `mr-closed-long@1280`, `mr-closed@1280`, `needs-gate-long@1280`, `needs-gate@1024`, `needs-gate@1024~h700`, `needs-gate@1024~light`, `needs-gate@1280`, `needs-gate@1280~h700`, `needs-gate@1280~light`, `needs-gate@1920`, `needs-gate@1920~h700`, `needs-gate@1920~light`, `needs-you-long@1280`, `needs-you@1280`, `panel@1280`, `paused-long@1280`, `paused@1280`, `running-long@1280`, `running@1024`, `running@1024~h700`, `running@1024~light`, `running@1280`, `running@1280~h700`, `running@1280~light`, `running@1920`, `running@1920~h700`, `running@1920~light`, `waiting-long@1280`, `waiting@1280`, `worker-lost-long@1280`, `worker-lost@1280`
- `ng-library`: `node@1280`, `node@1280~light`
- `ng-shell`: `long-crumb@1024`

## Unit tests

`just test-ui`: 2122 tests (was 1991), no Unhandled or TypeError. `just test tests/test_theme_contrast.py`: 39 passed. Mutated: `--stroke` set to `--border` in `dev/gen_theme.py` fails `test_every_combination_clears_its_floors` on 30 combinations; the danger hover label set back to `--bad` fails `ui.css .btn-danger hover/focus colours its label with --text`.
