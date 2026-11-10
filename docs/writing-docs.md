# Writing the docs

How to write and preview the user-facing docs in `docsite/`. [Contributing](../CONTRIBUTING.md) covers the rest of a pull request.

## Previewing the docs site

```bash
just docs       # cd docsite && npm ci && npm run dev
```

The site is served at <http://localhost:3000/>. The dev server logs a few
warnings as it starts; the site works regardless.

`just docs` serves `main`'s pages alone. To see the site as it publishes, with
the latest release at <http://localhost:8000/>, each older major at
`/vN/` and `main` at <http://localhost:8000/next/>, run `just docs-site`.

## Page rules

`docsite/` is the published documentation site (a Nuxt/Docus app; `nuxt
generate`, deployed to GitHub Pages by `.github/workflows/docs.yml` on every
push to `main`). It isn't `docs/intent/`, which only a contributor reads.
If your change touches any of the sources in [Which page a change
updates](#which-page-a-change-updates), update the matching page in the same
pull request.


- Pages live under `docsite/content/`, one Markdown file per page, with MDC
  syntax for the odd embedded component.
- A page's route follows its folder, minus the number prefixes:
  `content/5.reference/03.configuration/03.policy.md` is served at
  `/reference/configuration/policy`. A folder's `index.md` is its landing
  page, and its `.navigation.yml` sets its sidebar title.
- A diagram is markup, not an image: a `::diagram-flow` (stages in order,
  with branches, loops and groups), `::diagram-graph` (things and the edges
  that leave each, such as the status state machine) or `::diagram-sequence`
  (numbered messages). Its content is the block's YAML, and its `caption`
  says in words what it shows.
- The components are `docsite/app/components/Diagram*.vue`; each one's comment
  has the fields, and `2.concepts/1.how-a-work-item-runs.md` is the example.
  They draw the site's one chain language: Plex Mono, `→` between stages, a
  gate (`gate: you approve`) as the violet chip, a loop as `↻`. Run
  `npx nuxt dev` in `docsite/` and look at it in both colour modes and at a
  phone's width.
- A nested MDC component block needs one more `:` per level of nesting
  (`::` then `:::` then `::::`), or the parser closes the wrong block.
- No walls of text: a paragraph or list item is at most 80 words and a
  table cell at most 40. `dev/check_docs_walls.py` fails the `lint` job on
  a longer one; run it on a page with
  `uv run python dev/check_docs_walls.py PAGE.md`.
- A page shows its `description` as the lede under the title, so the first
  paragraph must carry on from it, not say it again. Delete a paragraph that
  only restates it. `dev/check_docs_ledes.py` fails the `lint` job when
  65% or more of the content words in the paragraph's first sentence are
  already in the description. The same check fails a description with a
  backtick in it: the lede, the search result and the meta tags show it as
  one.
- A code block that lists commands to pick from, not to run in order, is
  fenced ` ```bash each `: every line gets its own copy button, where the
  block's one button would copy them all as a single paste.
- When you move or rename a page, or a heading that something links to, add
  an entry to `docsite/content/redirects.yml` in the same pull request. The
  file shows the two forms: an old page to its new address, and an old
  `/page#heading` to its new one. An installed copy of Kraft, a bookmark and
  a search result all keep opening the old address.
- `dev/check_docs_redirects.py` (the `lint` job) fails on a bad entry, and on
  any docs address that Kraft itself links to and that no longer resolves.
  It also fails a `](/page#heading)` link between pages that lands on no
  heading, or on an address the file forwards (write the target).
- Mark what is new. A page, section or CLI verb that is new since the latest
  release carries `_Added in next_` on its own line directly under its heading
  (a new page: under its title, which covers its sections), or at the end of
  its table cell (a row in "Verbs at a glance", a flag row).
- Write exactly that, never a version: the release stamps it into
  `_Added in 2.1_` ([RELEASING.md](../RELEASING.md#docs-added-in-notes)), and a
  new major strips every note. Never put it in heading text, which would leak
  into the table of contents, search titles and the sidebar. On the site it is
  a chip ("Unreleased", then "Added in 2.1"); in the Markdown, `llms-full.txt`
  and search it stays plain words.
- `dev/check_docs_added_in.py` (the `lint` job) compares `docsite/content`
  with the latest release tag. It fails on a new page, heading or verb with no
  note, and on a note in the wrong place or naming a release that doesn't
  exist. It warns on a `next` note on something the release already had.
- A heading isn't new if its page moved (`redirects.yml`), or if you say so
  under it. Use `<!-- renamed from: Old heading -->` for an old heading under a
  new name (`/page#Old heading` from another page, `/old/page` for a page split
  or renamed), and `<!-- not new: why -->` for new text about something that
  already shipped. The comment doesn't render, and the release deletes it.
- No comment in a shell block (`bash`, `sh`, `shell`, `zsh`, `console`): an
  interactive zsh, the macOS default, doesn't read `#` as one, so a
  pasted `cmd # note` fails with `command not found: #`. Put the remark in
  the sentence above the block. `dev/check_docs_shell.py` (the `lint` job)
  fails a line that starts with `#` or has one after whitespace outside
  quotes.
- When you add, move or remove a page, update its folder's `index.md`: the
  list under `## In this section` names the pages and sub-folders of that
  folder, in file name order. A sub-folder with no `index.md` of its own is
  listed as its pages, and a page deeper inside one may be listed too. Link a
  page of another section under another heading.
- `dev/check_docs_landings.py` (the `lint` job) fails when the list and the
  folder disagree. Number prefixes in one folder all have the same number of
  digits (`01.` to `10.`), because the sidebar sorts by name.

## Which page a change updates

A change to one of these sources updates its page in the same pull request.

| Source | Docs page |
|---|---|
| A `kraft` subcommand or flag (`src/kraft/cli/*.py`) | `docsite/content/5.reference/01.cli/` |
| The console (`src/kraft/console/`) | `docsite/content/5.reference/01.console.md` |
| A `library.yaml` component key, or a `policy.yaml` / `repos.yaml` / `access.yaml` / `intake.yaml` field (`src/kraft/templates/models.py`, `src/kraft/templates/library.py`, `src/kraft/config.py`, `src/kraft/policy.py`) | `docsite/content/5.reference/03.configuration/` |
| A chain template's node fields | `docsite/content/5.reference/04.chain-nodes/index.md` |
| How a subprocess task runs, or a result-file field (`src/kraft/adapters/subprocess.py`, `src/kraft/findings.py`, `src/kraft/usage.py`) | `docsite/content/5.reference/04.chain-nodes/2.subprocess-tasks.md`, `4.result-file.md` |
| The fix loop or its judge (`src/kraft/executor/walk.py`, `dispatch.py`) | `docsite/content/5.reference/04.chain-nodes/3.fix-loop.md` |
| A new default chain, or a change to the core vocabulary | `docsite/content/2.concepts/1.how-a-work-item-runs.md` (the shipped chains, node by node), `docsite/content/2.concepts/4.vocabulary.md` (a term) |
| Trigger behaviour (`src/kraft/triggers.py`) | `docsite/content/5.reference/07.triggers.md` |
| The permission gate (`src/kraft/harnesses/*.yaml`, `permission_rules.py`, `permission_hooks.py`, `grants.py`) | `docsite/content/5.reference/06.permissions.md` |
| A harness (`src/kraft/harnesses/*.yaml`, `harness.py`) | `docsite/content/5.reference/05.harnesses/` |
| An MCP tool (`src/kraft/mcp.py`) | `docsite/content/5.reference/09.mcp-tools.md` |
| A Kraft add-on's or collection's format, `addons.yaml`, `addons.lock`, or how an add-on loads, updates or is restored (`src/kraft/addons/`, `src/kraft/cli/addon.py`) | `docsite/content/5.reference/03.configuration/12.addons.md` (the facts), `docsite/content/3.guides/2.customize/4.share-chains-with-addons.md` (the steps), `docsite/content/5.reference/01.cli/6.addon.md` (a `kraft admin addon` verb), `docsite/content/4.troubleshooting/10.addon-problems.md` (a message a `kraft admin addon` verb prints, quoted as it prints it) and `docsite/content/4.troubleshooting/11.addon-validate-messages.md` (one `validate` prints), and `docsite/content/6.project/1.security.md` or `1.security-in-depth.md` when it changes what an add-on may carry |
| A repo library, the `.kraft/` folder a connected repo shares (`src/kraft/addons/repo_library.py`, `kraft repo library`) | `docsite/content/5.reference/03.configuration/13.repo-libraries.md` (the facts), `docsite/content/3.guides/2.customize/4.share-chains-with-addons.md` (the steps), `docsite/content/5.reference/01.cli/4.repo.md` (the verb), and `docsite/content/6.project/1.security.md` or `1.security-in-depth.md` when it changes what accepting trusts |
| A Claude Code plugin skill (`plugins/kraft/skills/`) | `docsite/content/3.guides/1.day-to-day/4.agent-integration.md` |
| A `KRAFT_*` environment variable, or a variable passed to workers (`src/kraft/worker/env.py`) | `docsite/content/5.reference/03.configuration/11.environment-variables.md` |
| A new event type (`events.append`), or the notification webhook (`src/kraft/notify.py`) | `docsite/content/5.reference/10.events.md` |
| A stop reason (`store.mark_needs_human`) or a `kraft admin doctor` check | the topic page under `docsite/content/4.troubleshooting/` whose rows stand with it: `01.failed-tasks-and-fix-loops.md`, `02.launch-and-setup-stops.md`, `03.caps-budgets-and-waits.md`, `04.forge-stops.md`, `05.worktree-and-git-stops.md` (a git state of the worktree; an in-place checkout reason goes on `docsite/content/3.guides/4.run/12.run-items-in-place.md`), `06.sandbox-and-kit-stops.md` (a sandbox, Kit or workspace-member reason, and a doctor sandbox row) or `07.os-sandbox-refusals.md`; a stop with numbered steps gets an entry under its table on the same page, and its words go in the first column. A doctor check goes on `08.doctor-failures.md`, under the heading named as doctor prints the row |
| `access.yaml` / remote-access behaviour | `docsite/content/3.guides/4.run/01.remote-access.md` (and `02.put-a-proxy-in-front-of-kraft.md` for the `Host` check and the reverse-proxy way), and `SECURITY.md` if it's security-relevant |
| A screen or its behaviour (`frontend/src/ng/`) | the pages that name it: see [Pages for a screen](#pages-for-a-screen) |

### Pages for a screen

A screen change updates the pages that show or name it:

- `docsite/content/1.get-started/2.first-work-item.md`: the board.
- `docsite/content/5.reference/00.web-ui/`: each screen (the board, an item's
  page, the review page, Templates and Settings, Analytics).
- `docsite/content/3.guides/1.day-to-day/`: `2.first-run.md` (the setup
  wizard), `3.file-a-work-item.md` (the composer), `5.review-a-change.md` (the
  review page) and `8.kraft-on-a-phone.md` (reaching the board from a phone; the phone
  layout is `docsite/content/5.reference/00.web-ui/9.on-a-phone.md`).
- `docsite/content/3.guides/4.run/01.remote-access.md` and
  `02.put-a-proxy-in-front-of-kraft.md`: Access.
- Any other page that names the screen or a button on it: `git grep` its label
  under `docsite/content/`.

If the screen is in a README or docs screenshot (`.github/assets/*.png`),
retake it with `just docs-screenshots` (the web UI), `just console-screenshot`
or `just vscode-screenshots`.
[`frontend/SCREENSHOTS.md`](../frontend/SCREENSHOTS.md#retaking-the-screenshots-in-githubassets)
has the details.

## Before you push

Run `npm ci && npx nuxt generate` in `docsite/` before you push. It fails on
a page that doesn't parse, but a link to a page or heading that no longer
exists renders instead of failing the build.

The docs workflow catches those: on a pull request it runs [lychee](https://lychee.cli.rs) over the built
pages and their Markdown, and fails on a broken internal link, a missing anchor or an external
link that doesn't answer. Nothing catches "this paragraph no longer describes the
code", and `docs/intent/`'s `enforced-by:` pinning doesn't either. Read the
page you're touching, not just the code.

## Voice and spelling

[Vale](https://vale.sh) checks the docs site, the README and the contributor docs
against the house style. `just vale` runs it, and so do the pre-commit hook and the `lint` job. The
rules live in `.vale/styles/Kraft/`:

- British spelling, -ise forms: colour, behaviour, labelled, organise. The rule
  lists the US forms to catch (`British.yml`); add one when it slips through.
- Contractions for a negation: doesn't, can't, isn't. Not it's or that's: a
  contraction can't end a clause.
- One word per term: re-run, sub-agent, signed in, ID, URL, Docker, Markdown,
  merge request. The pages about Kraft's own GitHub repo say pull request: the
  contributor docs, and the docs site's Contributing and Source map pages
  (`.vale.ini` turns the rule off there).
- No comma before the "and" or "or" that ends a list.
- `select`, not `click`. The present tense, not `will`. No sentence that
  starts with `So`, no spaces around a dash, no `very`.

`just vale` covers `docsite/content`, the README, the VS Code extension's
README (its Marketplace page), the Kraft, Kraft Lite and kraft-superpowers
READMEs and the contributor docs (`vale_paths` in the justfile, which CI's step
repeats), not `docs/intent/`. Inline code, fenced code and a component's YAML props are
skipped.

Where the text quotes something exactly, such as a label the app spells its
own way, turn that one rule off around it, with
`<!-- vale Kraft.Dashes = NO -->` before and
`<!-- vale Kraft.Dashes = YES -->` after.

A phrase quoted in many places goes
in `.vale.ini`'s `TokenIgnores`, as the review outcome "could not decide" does.
A heading reworded for the style is a renamed heading: give it a `renamed
from` note, and a redirect if a page links to it.

## Versions of the docs

The site publishes three kinds of version: the latest stable release's pages at
[`/`](https://kraft-sdlc.dev/), each older major frozen at `/vN/` (today `/v1/`,
from the last `v1` release), and `main`'s
at [`/next/`](https://kraft-sdlc.dev/next/). Write docs on `main`, in the same
pull request as the code. They appear at `/next/` when it merges and become the
default at the next stable release. There is nothing else to do. Link to
`/next/` for a feature that hasn't shipped in a release yet.

A section that is new since the last release says so with an "Added in"
note, which the release turns into the version that shipped it (see
[Page rules](#page-rules) above).

The `/next/` build also lists
those notes on an Unreleased page, linked from its version banner and marked
in the sidebar: `dev/gen_docs_unreleased.py` writes it at build time, so there
is no list to keep. A release stamps every note into a number, which leaves
that page nothing to list, so a released site has none.

`dev/docs_versions.py` picks the tags from the repo's tags, so nothing is
configured: `/` is the highest `vX.Y.Z` tag, and each lower major with a
`docsite/content` is built from its own highest such tag. When `v3.0.0` ships,
`2.x` becomes `/v2/` with no code change. Only `/` is indexed; `/vN/` and
`/next/` carry a `noindex` tag and stay out of the sitemap.

Every version is built with `main`'s site code (`docsite/app`,
`nuxt.config.ts`), so a theme or component fix reaches the release docs
without a release, while their text stays what the release shipped. 

The flip
side is that a docs-only fix to a released page, such as a typo, waits for the
next release too. 

A frozen major keeps the words of the components that hold
them: `dev/build_docs_site.sh` builds it with its own tag's `StageStrip.vue`,
so the landing page still names the stages the way that release did. If a
page of a frozen major ever shows text from a newer component, add the
component to that list.

Each version also has the dashboard demo of its own code beside its pages:
the web UI on sample data, with no server (`frontend/demo/`). `main`'s is at
`/next/demo/`, a release's at `/demo/` or `/vN/demo/`, built from that tag's
frontend with an `npm ci` of its lockfile, because the demo's mocks describe
that release's API. A tag from before the demo has none.

 Link to it as
`/demo/`, like a page: each build gives it its own base, and
`docsite/app/plugins/demo.ts` loads it from the server rather than the router.

The demo answers from the UI contract's mocks (`frontend/e2e/contract/mocks/`),
so a change to what it shows is a change to them. They are held to the server
by `tests/api/api_shapes.json`, which `just shapes` rewrites, and to the
vocabulary by `frontend/demo/sampleCoverage.test.ts`. When a response
changes, run `just shapes` and update the mock in the same PR; the `docs
nudge` workflow comments when `src/kraft/api/` or `src/kraft/vocab/` moved
without either.

If a frozen major stops building with `main`'s components, build it with its
own tag's site code instead:
`KRAFT_DOCS_OWN_SITE="v1" dev/build_docs_site.sh OUT_DIR` runs an `npm ci` of
that tag's lockfile and its own `nuxt generate`. 

The limits: it has the old
look and no version menu or banner (the tag's config knows nothing of them), a
tag that hard-codes its origin (`v1.4.0` does: `itsomidkarami.github.io`) puts
that origin in its canonical, `og:url` and sitemap URLs, and its pages are made
`noindex` by a tag added after the build. Verify it with
`KRAFT_DOCS_OWN_SITE=v1 just docs-site` before relying on it.
