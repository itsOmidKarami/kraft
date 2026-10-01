# kraft/ux2-fix-inter

Kraft-9d8b2.35 (R72): /ng loads the Inter webfont it asks for.

`ng/boot.tsx` imports `@fontsource-variable/inter/wght.css` (what the shipped `nocturne.css` imported). The package registers the family as "Inter Variable", but /ng's CSS said plain `Inter`, and its `body` said `system-ui`, so an import alone would have changed nothing. `theme/base.css` now defines `--font-sans: "Inter Variable", Inter, system-ui, sans-serif`, the body uses it, and the seven rules that named `Inter, system-ui, sans-serif` (board.css, review.css) use the token.

Built bundle: `npm run build` puts the seven `inter-*-wght-normal-*.woff2` files in `dist/assets`, and the /ng boot CSS chunk carries the seven `@font-face` rules and the latin woff2.

Sweep: `node sweep/wave.mjs all`, baseline shot on origin/main (39713f3b4) after deleting e2e-shots (Kraft-9d8b2.32). 1843 cells: 674 changed, 0 cleared, 0 newly flagged, rule "no newly flagged cells" passes. 675 of the 676 `ng-*` cells differ byte for byte (text shifts: Inter's widths differ from the system font, so some lines wrap elsewhere). 116 shipped cells differ byte for byte too, by at most 0.004% of pixels (run-to-run noise: the shipped UI already loaded Inter); the sweep counts none of them as changed.

Before / after of `ng-item/running@1280` (dark), the title, description and meta line; left is main, right is this branch: `kraft-ux2-fix-inter-ng-item-running.png`.

No unit test: a font import has no behaviour a jsdom test can see. `ng/css.contract.test.ts` and `css.collision.test.ts` pass (no `@font-face` is written in /ng; it comes from the package).
