# kraft/ux2-fix-yaml

Kraft-9d8b2.42 (R76), item 2: the header YAML button on Settings › Access and Appearance.

Both pages get what AreaAccess and AreaAppearance draw: a YAML button in the header that opens the shared `Inspector` pane on the file's text, read-only, built from the page's own state. `settings/YamlFrame.tsx` is the one frame for both: without an Overview the pane exists only while YAML is open (Access: the design's single YAML tab); with one it is always there, collapsed to its rail until opened, with Overview and YAML tabs (Appearance: the design's `paneOpen: false`). Access's YAML is `access.yaml` as the file says it (`GET /access`), the password never shown, and a comment line says what is running while a bind or port waits for a restart. Appearance's is every control's effective value, and its Overview lists them as the design does.

Sweep: baseline shot from a build of origin/main at aaf2b6431 (`SWEEP_DIST`), `e2e-shots` deleted first (Kraft-9d8b2.32), `SWEEP_PORT=4389`, then `node sweep/wave.mjs ux2-fix-yaml`: 5/5 rules pass, 51 cells, 0 regressions. The 29 existing `access/*` and `appearance/*` cells changed: the page now lives in the frame's scrolling body, and Appearance reserves its pane's 40px rail.

New cells: `access-yaml/{lan,restart-pending}@1280`, `appearance-yaml/{yaml,overview}@1280`.

`kraft-ux2-fix-yaml-appearance.png` and `kraft-ux2-fix-yaml-access.png`: before (main), after, and the design's `AreaAppearance.dc.html` / `AreaAccess.dc.html` rendered standalone in Chromium; the prototypes' own pane does not render outside their host, so the design columns show the page only.

E2E: `e2e/regression.spec.ts` passes against a fresh fixture server (the access and appearance specs read back from the API and a reload, not from the layout).
