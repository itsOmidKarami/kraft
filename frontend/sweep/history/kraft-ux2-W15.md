# ux2-W15 · Repos, Policy and Auto-intake

Branch `ux2/W15` pushed as `kraft/ux2-W15`, on `ae13f8416` (W12 #374, after W11, W14 A and W16). Backend half: #365 (merged).

**Sweep port: 4395** for the baseline and both end runs. The baseline is `node sweep/wave.mjs all --baseline` shot from a clean detached checkout of `ae13f8416` (1712 cells), copied to `e2e-shots/baseline/all` and `…/ux2-W15`, so nothing of this branch is in it.

## `node sweep/wave.mjs ux2-W15`

6/6 rules pass · 88 cells in scope · 83 changed (72 new, 11 `ng-shell` cells that opened Policy as a placeholder and now open it for real), 0 cleared, 0 regressions.

| Rule | Result |
|---|---|
| contrast = 0 on `^ng-` | pass |
| offscreen = 0 on `^ng-` | pass |
| clipped-v = 0 on `^ng-` | pass |
| no console errors on `^ng-(?!chains/review-stale\|policy/review-stale)` | pass |
| flow steps complete on `^flow-ng-(repo\|policy\|intake)-` | pass |
| no newly flagged cells | pass |

New cells: `ng-repos` 13, `ng-policy` 29, `ng-intake` 10, flows `ng-repo-connect`, `ng-repo-refused`, `ng-policy-edit-publish`, `ng-policy-stale`, `ng-intake-schedule` (20 step rows). The only flags on them are the browser's own `console×1` for the 409 in the stale cell and flow, which the rule exempts for cells and which a flow only needs to complete.

## `node sweep/wave.mjs all`

1/1 rule passes · 1784 cells · 72 new · 36 existing cells changed · 0 newly flagged. 35 of the 36 are `ng-` cells that used `/ng/settings/policy` as a convenient unbuilt page and now show the real Policy page (mocked): `ng-shell/{pinned,rail,revealed,placeholder}`, `ng-search/*`, the `flow-ng-sidebar-*` and `flow-ng-search-keyboard` rows. The `placeholder` cell moved to the not-found route, which stays a placeholder when every page is built. The 36th is shipped: `flow-log-maximize/03-wheel-up-pauses-follow@1280` (4.74%, a 1px shift of the log header); it moves between any two runs of `main` itself, since a clean checkout re-shot against its own baseline gives the same 4.74%. No shipped cell changed otherwise; no cell's clock text changed. `ng-chains/review-stale` moved by less than the 0.05% threshold (its stale box tint went from 8% to 5% so "Discard draft" clears 4.5:1).

## Exit test (real server)

A throwaway `kraft` (own `KRAFT_HOME` inside the worktree's `.dev/`, port 8771, the built SPA, fake agent, seeded with work items), driven through the pages by Playwright; each line asserted against the API and the files on disk. 15/15:

- Policy: edited two maxima, max active items and the `default:` loop block; one publish; `GET /policy` and `policy.yaml` have them; a running work item's frozen chain is byte-identical after.
- A second draft: another save (the shipped `PUT /policy`) moved `policy.yaml`; publishing the draft answered the 409 with the diff; Keep my version rebased and published (the other save's key reverted, as the brief's risk says).
- Auto-intake: add a schedule, enable, interval 30 s; the review's YAML diff had a block for `intake.yaml` and `policy.yaml`; one publish wrote both; the replaced poller recorded a check within a minute and the page listed it.
- Repos: a real throwaway git repo connected through the probe, enabled, published, in `GET /repos`; Disconnect on a repo with running items refused inline; the same removal typed into the YAML blocked Publish with the server's problem; an `allowed_tools` wider than the instance ceiling refused inline, nothing saved.

## Differences from the prototypes

Policy cells take a value that creates a problem into the draft and show it in red, as the prototype does (Repos alone refuses inline). Three finding severities (the loader accepts no `info`). "Set below policy" and Preview on a chain are the server's (#365). No "next check" countdown or empty-poll lines (the API has neither). Recent checks take each live `intake_checked` frame from the shared socket (`ng/live.ts`) and refetch every 30 s and on focus as the fallback. Repos' YAML tab is the entry's read-only fragment. YAML opens as the shared full-height editor (button reads "⇄ Page"), not the prototype's side pane.

## Shared files touched, additively

`ng/templates/ReviewPane.tsx` (optional `area`), `YamlView.tsx` (optional `file`), `panes/panes.css` (`.tpl-rv-stale` tint 8% → 5%), `ng/ui/Menu.tsx` + `ui.css` (item `dot` and `icon`), `shell/{Sidebar,useDraftCounts,crumbs,routes}`, `ng/App.tsx`, `ng/session.ts` (the one socket's `onLive` is now `ng/live.ts`'s dispatcher: `apply_changed` to the chip, other frames to `subscribeLive` listeners). `ReviewPane` and `YamlView` are W12's shapes with the optional `area`/`file` added; `AreaFrame` hands a page `yaml(file)` for W14. W10's tests pass unchanged except where this PR adds cases.

## Beads (under Kraft-9d8b2)

Kraft-xkmkq (shipped Policy offers `info`), Kraft-7jeur (next-check time), Kraft-ijjmu (empty polls), Kraft-tgiao (server-side probing `add_repo`), Kraft-9d8b2.14 (the live frame, closed here).
