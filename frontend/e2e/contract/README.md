# The UI contract

What the web UI does, as a list of claims that CI checks. Each claim drives
the built SPA in a real browser with `/api/**` mocked from `mocks/`, so there
is no Kraft server and no Python. It covers the sidebar's pin and reveal, Esc
and focus order, the item header, gates and stops, review, the document
viewer, the phone's screens, and tooltips on icon-only controls.

A failing row is a regression, or a behaviour that changed on purpose. For
the second, change the row in the same pull request and say why. `[decided]`
in a row's name marks a behaviour that was chosen: changing it is a decision
to revisit, not a fix. The decisions come from the UX decision record (not
yet in the repo).

## Run it

```bash
just ui-contract                        # builds the SPA, serves it with `vite preview` on :4327
just ui-contract shell.spec.ts          # one file
just ui-contract -g "sidebar"           # the rows whose name matches
CONTRACT_PORT=4400 just ui-contract     # another port, when 4327 is taken
CONTRACT_DIST=dist just ui-contract     # serve a dist you already built (relative to frontend/)
```

Without `just`: `cd frontend && npx playwright test -c e2e/contract/playwright.config.ts`.

Results print as a list. A failure's trace and screenshot land in
`frontend/test-results/`; CI uploads that folder as `ui-contract-failures`.

## What is here

| File | What it holds |
|---|---|
| `shell.spec.ts`, `item.spec.ts`, `review.spec.ts` | the rows: one plain-language claim per test, named for the behaviour |
| `phone.spec.ts` | every phone screen that needs seeded data is reached by tapping from the board, and a resize swaps the phone and desktop apps in place |
| `icons.spec.ts`, `screens.ts` | the icon-only audit: on every screen in `screens.ts`, a control with no letter or digit needs an accessible name and a `data-tip` |
| `kit.ts` | `app()` (the SPA on its mocked API), `contract()` (one test per row) and the helpers rows share |
| `mocks/` | the mocked API (`mockApi.ts`) and the scenario it answers from (`fixtures.ts` and the files beside it) |

## Add a row

1. Put it in the spec that owns the area, in that file's `ROWS`, using
   `app()` and the helpers in `kit.ts`.
2. When the row needs data the base scenario lacks, add a seed beside the
   other `with*` helpers in `mocks/fixtures.ts` and pass it as `tweak`:
   `app(page, url, { tweak: withReviewer })`. Do not build data in the spec.
3. A new screen or state that can hold an icon-only control goes in
   `screens.ts`.
4. Prove the row: break the code it covers, see that row fail, restore the
   code. A row that cannot be made to fail pins nothing.
