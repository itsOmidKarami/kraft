# kraft/ux2-fix-cards

Kraft-9d8b2.42 (R76), item 5: Settings pages draw their sections as the designs' bordered cards.

Checked against the designs first: AreaAccess cards every section; AreaAbout cards its version, its instance rows and its links; AreaAppearance does not (plain labelled sections), so it is unchanged.

**Access.** Reach is two radio cards (icon tile, title, address, what it means, a radio dot, a ring on the chosen one) with no heading but a screen reader's. Port, Allowed hosts, Password and Sessions are bordered cards: the title at the top left, the aside at the top right ("saved on change", "waits for a restart" on Port while a new port waits, "writes access.yaml", a "live" tag), a row's help in a column on the right inside the card, a pencil on the port and the password, a pill for each host and a dashed "+ add". "Not used on 127.0.0.1" is a dashed card. The restart notice gets the design's icon tile and Restart button icon; the current session's button says "Sign out here". The title and its line sit on one line. `Block` (`settings/parts.tsx`) takes `card`, `pill` and `hidden` for this; nothing else changes its look.

Sweep: baseline shot from a build of origin/main at 1ddcc58e4 (`SWEEP_DIST`), `e2e-shots` deleted first (Kraft-9d8b2.32), `SWEEP_PORT=4389`, then `node sweep/wave.mjs ux2-fix-cards`: 5/5 rules pass, 32 cells, 0 regressions, 2 flags cleared (the old page's small-text contrast on two cells). The existing `access/*` and `access-yaml/*` cells all changed. New cells `access-cards/{add-host,password-edit}@1280`.

`kraft-ux2-fix-cards-access.png`: before (main), after, and the design's `AreaAccess.dc.html`.

E2E: the access and notifications specs in `e2e/regression.spec.ts` pass on a fresh fixture server.
