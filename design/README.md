# Design source

`Kraft Design.dc.html` is the canvas the redesign was built from — every screen
id the code comments refer to (2a, 3a/3b, 4a–4e, 5a–5e, 6a–6c, 1g, 1h, 1m, 1n)
is a `<div class="dv-opt" id="…">` in here, and the markup inside each one is
the layout the implementation matches.

It is a Claude Design export, so it needs that project's `_ds/` bundle and
`support.js` to *render*; those are not vendored here. Reading or grepping the
markup works without them, which is what the implementation actually needed.

The two companion documents live in the same project and are not duplicated
here: **Kraft Handoff Spec** (tokens, the one list pattern, the status
vocabulary, chain-bar rules, the implied API) and **Kraft Prototype** (the
clickable main flow).

Project: https://claude.ai/design/p/58c09506-65dc-4238-8258-75f7cdf569eb
