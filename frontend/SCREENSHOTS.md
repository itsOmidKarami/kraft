# Screenshots

Part of the web UI; [`README.md`](README.md) is the getting-started.

## Retaking the screenshots in `.github/assets/`

Every docs and README screenshot is taken by a script, so a retake is one
command and changes a picture only where its screen changed. All are Kraft's
default look: Ink, dark, a violet accent at the full colour amount.

```bash
just docs-screenshots              # the web UI, from the dashboard demo
just docs-screenshots -g storage   # one picture, by its test name
just vscode-screenshots            # the VS Code extension (vscode/media/, copied here)
just console-screenshot            # the console, from a seeded dev instance (below)
just readme-gif                    # the README's loop, from a fresh dev instance (below)
```

`just docs-screenshots` builds the demo, serves it on :4329 and runs
`e2e/docs-shots/shots.ts`, one Playwright test per picture. The demo's sample
data is fixed, so nothing needs seeding. It hides the demo's own pill and
things-to-try list and pauses its live motion. The sidebar's footer names the
release the demo was built from, the newest tag behind the commit; a clone
without tags says `vdev`, so build with `KRAFT_DEMO_VERSION=X.Y.Z` there.
`setup` answers the repo and item lists empty, which is what a new home shows.
A retake writes a picture only when it looks different from the one there, so
an unchanged screen leaves its file alone. `chain-extends` is of `team-default`,
a chain that extends `default`, which the demo lists on the Chains screen only:
its list entry and draft view are a real server's answers, recorded into
`e2e/contract/mocks/draftViews.json`.

`just console-screenshot` needs the dev instance filled first: `just api` in
one terminal, then `just dev-seed --rich`. It runs the console headless,
selects *Design the caching layer* so the peek shows its spec (writing a real
one over the fake agent's `fake spec body`), and renders Textual's SVG to a
PNG. The top line names the newest release tag; pass `--version` in a clone
without tags.

`just readme-gif` records `run.gif` and `run.mp4` from a fresh dev instance:
`just dev-reset`, `just dev` in one terminal, then the recipe. It files one item
in the composer, approves its spec, plan and local review gates and ends at the
final review. The fake agent takes 2 s a step there, so the chain graph shows
believable durations, and the loop plays at 6× with a badge saying so while the
agent works. Gate documents, the version and the update notice are swapped at
the API response, as the console retake does.

`just vscode-screenshots` drives the packaged extension in a real VS Code,
in a 1440×900 window; on Linux run it under `xvfb-run`. Every picture is saved
at its size on screen, so text reads at its real size.

| File | Size | Shows | Shown in |
|---|---|---|---|
| `board.png` | 1440×700 | the board, grouped by status: Needs you (a gate and six stops, each with its one button), then Running | docs home (cropped), first work item, web UI › Board |
| `run.gif`, `run.mp4` | 1280×800, 1600×1000 | one item filed, through its gates to the final review, about 22 s | README (the GIF) |
| `gate.png` | 1440×700 | the review page of the item at `final_review`, its review brief rendered | README, the real chain, review a change |
| `item.png` | 1440×700 | that item's page with `final_review` selected in the graph and the inspector | docs home (cropped), web UI › Item page |
| `review.png` | 1440×700 | that item's review page, a must-fix comment being written on lines Old 2 – new 2 | docs home (cropped), web UI › Review page, review a change |
| `search.png` | 1440×700 | Ctrl/⌘K with `caching` typed, over the board | not shown yet |
| `analytics.png` | 1440×660 | the Analytics page | README, web UI › Analytics |
| `chains.png` | 1440×700 | Templates › Chains on `default`, the `spec_approval` gate selected | web UI › Templates and Settings |
| `policy.png` | 1440×700 | Settings › Policy on its Limits tab | web UI › Templates and Settings |
| `setup.png` | 1440×700 | the setup wizard's first step, on a home with no repo | first run |
| `mobile.png` | 390×844 | the board in the phone layout | README, docs home (cropped), Kraft on a phone |
| `stops.png` | 1180×450 | the board's Needs you group alone: one row per kind of stop | why did my item stop |
| `stop-peek.png` | 1230×700 | a capped item's side panel beside the board | why did my item stop |
| `composer.png` | 1230×309 | the board's composer, filled in | file a work item |
| `storage.png` | 1230×685 | Settings › Storage down to the first worktree row | logs and disk space |
| `chain-extends.png` | 1230×700 | Templates › Chains on `team-default`, which extends `default`: `verification` overridden and selected, `security_scan` added | web UI › Templates and Settings, write your own chain |
| `fixloop.png` | 1230×700 | a running item's `verification` node in its own view, on its second fix-loop round | web UI › Fix loops and test scopes |
| `new-item-page.png` | 1230×602 | the full new-item page, a title typed | file a work item |
| `harnesses.png` | 1230×700 | Settings › Harnesses | web UI › Templates and Settings |
| `notifications.png` | 1230×700 | Settings › Notifications | web UI › Templates and Settings, notifications |
| `access.png` | 1230×700 | Settings › Access | web UI › Templates and Settings, remote access |
| `appearance.png` | 1230×700 | Settings › Appearance on the default look | web UI › Templates and Settings |
| `console.png` | 1482×880 | `kraft console` on the board, its peek on a gate | console reference |
| `vscode-*.png` | various | copies of `vscode/media/screenshot-*.png` | Kraft for VS Code |

The docs home crops `board`, `item`, `review` and `mobile` in CSS
(`docsite/app/components/LandingShot.vue`), by a `crop:` box in pixels in
`docsite/content/index.md`. When a retake moves what a crop frames, fix the
box there. Look at each PNG before committing it, and update its alt text
wherever the table says it is shown when what it shows changed. A pull request
that changes only these PNGs counts as docs-only to CI. The README and docs
site link to them by file name, so keep the names.
