# kraft/ux2-fix-about-cards

Kraft-9d8b2.42 (R76), item 5, the About half (Access went in #408; Appearance's design does not card its sections, so it is unchanged).

AreaAbout draws the version as a card tinted with the info colour (the version, its channel tag, the verdict at the right, the command with a Copy button, Release notes and Check now on one line, the channel), the instance as a labelled grid under a heading with Copy diagnostics at its right (Health and Search index with a status dot), and the links as a bordered list with icons. Search index's dot is green once it has scanned without errors and amber otherwise. The labels are capitalised as the design has them ("Run directory", "Process", "Search index").

Sweep: baseline shot from a build of origin/main at bffeca7a1 (`SWEEP_DIST`), `e2e-shots` deleted first (Kraft-9d8b2.32), `SWEEP_PORT=4389`, then `node sweep/wave.mjs ux2-fix-cards` (the wave now covers Access and About): 5/5 rules pass, 48 cells, 0 regressions. The 16 `about/*` and `about-instance/*` cells changed; the Access cells did not.

`kraft-ux2-fix-cards-about.png`: before (main), after, and the design's `AreaAbout.dc.html` rendered standalone in Chromium.
