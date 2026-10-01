# kraft/ux2-fix-notify

Kraft-9d8b2.42 (R76), item 1: Settings › Notifications rebuilt to AreaNotifications.

Before: one long page of Blocks (webhook rows, browser rows, a Preview) with the URL and link back saved on blur. After: two channel cards (status dot, event chips, last sent, Send a test); a card opens the shared `Inspector` pane on Config, the floor shows Overview and YAML; the header YAML button shows `notify.yaml` built from `GET /notify` (`# webhook_url: 0600, never shown`, never the URL). Switches and event toggles save on change; the URL and link back are typed in the pane and sent in one PUT by Save (Discard drops them; the server's 422 text shows inline). Browser alerts keep their per-browser permission states (W16 C.2) and, as the design has, their own event list, kept in `localStorage` (`kraft.browserNotify.events`, both events until changed; `maybeNotify` honours it).

Sweep: baseline shot from a build of origin/main (`SWEEP_DIST`), `e2e-shots` deleted first (Kraft-9d8b2.32), `SWEEP_PORT=4389`, then `node sweep/wave.mjs ux2-fix-notify`: 5/5 rules pass, 30 cells, 0 regressions. The wave's first run flagged the chips of the inactive browser card at 3.5:1 (the card dimmed them with opacity); an inactive card's chips are dashed now, not dimmed, and the 14 flags cleared.

Cells: the five W16 `ng-notifications/*` states (changed) and `ng-notifications-pane/{webhook-config,webhook-overview,webhook-dirty,browser-config,browser-denied,yaml,collapsed}@1280` (new; the baseline has a setup error for them, as main has no such pane).

`kraft-ux2-fix-notify.png`: before (main), after (Webhook open), and the design's `AreaNotifications.dc.html` rendered standalone in Chromium; the prototype's own pane does not render outside its host, so the design column shows the cards only.
