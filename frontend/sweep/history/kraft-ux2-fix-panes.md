# kraft/ux2-fix-panes

Kraft-9d8b2.42 (R76), item 3: Repos, Auto-intake and Harnesses load with their side pane on its rail.

The designs start `paneOpen: false` (AreaRepos, AreaIntake, AreaHarnesses, AreaLibrary, AreaChains) and open the pane when something is picked (`componentDidUpdate`: a changed `sel` opens a closed pane). The build started all of them open. Aligned: Repos (`useState(!!param)`), Auto-intake (`false`), Harnesses (`useState(params.has("harness") || params.has("profile"))`). A URL that names a repo or a harness is a pick, so its pane opens; the list on its own loads with the rail.

Checked and not changed: Library draws no pane until a component is in the URL (and opens it then), so its first state already matches. Chains starts open and AreaChains loads closed; many tests and cells lean on it, so it is Kraft-9d8b2.44, not folded in.

Sweep: baseline shot from a build of origin/main at aaf2b6431 (`SWEEP_DIST`), `e2e-shots` deleted first (Kraft-9d8b2.32), `SWEEP_PORT=4389`, then `node sweep/wave.mjs ux2-fix-panes` (`sweep.spec.ts` and `interactions.spec.ts`): 5/5 rules pass, 114 cells, 0 regressions. Two existing cases opened the pane by loading the page and now expand it first (`harnesses/floor-config`, `intake/review`); the first run flagged them, which is how they were found. The other flows (`repo-connect`, `repo-refused`, `intake-schedule`, `harness-never-blocks-publish`, `harness-lanes-fit-the-pane`) complete, in the rule's scope this time (a flow is only in a wave's scope when its screen is listed).

New cells: `repos-first/list`, `intake-first/default`, `harnesses-first/floor`. `kraft-ux2-fix-panes-{repos,intake,harnesses}.png`: before (main), after, and the design rendered standalone (the prototype's own pane does not render outside its host, so the design column shows the page).

E2E: the auto-intake spec in `e2e/regression.spec.ts` edits the interval in the pane, so it expands the rail first (it timed out otherwise). The whole `e2e/` suite, 21 specs, passes on a fresh fixture server.
