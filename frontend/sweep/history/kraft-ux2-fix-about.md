# kraft/ux2-fix-about

Kraft-9d8b2.42 (R76) item 4, and Kraft-9d8b2.18: About draws the run directory, the process and the search index.

`GET /api/health` gains `uptime_s` (whole seconds since the app started, `time.monotonic()` taken in `startup`; test `test_health_reports_how_long_the_process_has_been_up`, docs row in `reference/7.http-api.md`). `run_dir`, `pid` and `index` were already sent; the page now draws them as AreaAbout does: Run directory, Process (`pid 41822 · up 3d 4h`, the uptime through `format.elapsed`) and Search index (`214 documents · scanned 2m ago`, or `not scanned yet`, with `· N scan errors` in the warning colour). The design's "2m behind git" is not something the index knows, so the row says when it last scanned. A server that does not send a field leaves its row out; the copied diagnostics gain the same three lines.

Sweep: baseline shot from a build of origin/main (`SWEEP_DIST`, built at aaf2b6431; the commits since are docs only), `e2e-shots` deleted first (Kraft-9d8b2.32), `SWEEP_PORT=4389`, then `node sweep/wave.mjs ux2-fix-about`: 5/5 rules pass, 16 cells, 0 regressions. The six existing `about/*` cells changed by the three rows (the fixture's health now carries the fields; its `last_scan_at` stays null so those cells do not move with the wall clock). New cells `about-instance/{scanned,scan-errors}@1280`, with the page clock frozen at NG_NOW.

`kraft-ux2-fix-about.png`: before (main), after, and the design's `AreaAbout.dc.html`. The design also draws the version, the instance rows and the links as bordered cards; that is item 5, not this PR.
