# Maintainer notes

Detail a first contribution rarely needs. [Contributing](../CONTRIBUTING.md) is the guide.

## Kraft's own collection

`kraft-default` (`config/`'s `library.yaml` and
`chains/`) ships inside Kraft and takes Kraft's own version, so a change to
`config/` needs no version raise. `addons/` holds the add-ons that build on it,
and `.kraft/collection.json` lists them. 

An installed instance sees an update
only when an add-on's `version` changes, so a change to an add-on under
`addons/` raises that add-on's `version` in the same pull request: patch for a fix, minor for something new, major for a
removed or renamed chain or task. When the change uses something the last
release can't read, raise `requires.kraft` too.

`dev/check_addon_versions.py` (in `just ci-checks` and CI's lint job) fails when
an add-on changed against the pull request's base and its version isn't
higher. 

Two pull requests that raise one add-on to the same version both pass
alone, and the second fails on `main`: merge `main` into the second and raise
it again before merging it.
`tests/addons/test_kraft_sdlc_collection.py` fails when a change to `config/`
breaks an add-on built on it. In this repository, `kraft-superpowers` chains are
refused their superpowers skills: `.claude/hooks/kraft-worker-plugins.sh` turns
superpowers off in Kraft workers.

## A pull request that changes a screen

A pull request that changes a screen does three things beyond the change
itself:

- Run an impeccable polish pass on each changed screen.
- Attach before and after screenshots of each changed screen, at 1280px and
  390px wide, in light and dark. Take them from a seeded `just dev` instance
  (`just dev-seed` fills it), the "before" from `main` and the "after" from your
  branch.
- Push the pictures to a separate `kraft/<branch>-screenshots` branch, not to the
  pull request's own branch, and link them in the pull request body.

A starter issue (`good first issue` or `help wanted`) says which screenshots it
needs instead: none, or one before and after at 1280px in light. The
maintainer does the polish pass.

A screen that also appears in a README or docs screenshot (`.github/assets/`)
is retaken with `just docs-screenshots` too; see the screen row under
[Writing the docs](writing-docs.md#which-page-a-change-updates).

In a cloud session, pushing that branch needs the user's approval in the session
that does the pushing. Ask for it; don't assume it.

`kraft/pr-686-screenshots` is the example. It holds two files at its root, no
folders and no code: `before-skipped-task.png` and `after-skipped-task.png`,
named for the state and the screen. Pull request 686's body links them in a
Before and After table, by their full `raw.githubusercontent.com` URLs pinned to
the branch's commit. That pull request shows one screen at one width and one
theme; with the four combinations, put the width and the theme in each file name
as well.

## The model price table

`src/kraft/prices.json` is what Kraft prices a *running* agent session with,
until the agent reports its own cost when it exits, and a finished one whose
agent reported none (Codex, Cursor, Amp). It is a snapshot of
models.dev's Anthropic and OpenAI listings, with Anthropic cache writes priced
at the one-hour tier Claude Code uses (2x input). 

Nothing refreshes it
automatically: when a provider's prices change, or a harness starts running a
model the file doesn't list, run `just refresh-prices`, review the diff, and
commit it. 

A model missing from the file gets no estimate, so its running
sessions count as unpriced towards the budget until they exit, and a finished
one that reported no cost counts $0 toward the item and daily caps (with a
`spend_unpriced` warning) and stops a `budget_usd`.
