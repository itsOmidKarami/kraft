# Releasing

How a maintainer cuts a Kraft release. Pull request authors need only
[CONTRIBUTING.md](CONTRIBUTING.md#pull-requests-and-release-labels): the label
and the `## Changelog` section. Never edit `CHANGELOG.md` by hand: the release
writes it, and each pull request's `## Changelog` section is its release note.

Kraft's version is the git tag. `setuptools-scm` derives it at build time, so
there is no `version =` line to bump.

The job runs in the `release` GitHub environment, whose deployment branches are
limited to `main`; the PyPI trusted publisher names that environment. Both are
settings outside the repo: recreate them if the repository is ever moved.

## Cut a pre-release

Same button, with **pre** set to `alpha`, `beta` or `rc`. It cuts the version
the labels plan as `vX.Y.ZaN`, `bN` or `rcN`, numbered past the ones that
exist. It is a GitHub pre-release: only `rc` also goes to PyPI, and none of
them touch homebrew, the stamp PR, the Marketplace or Open VSX. Users pick one
up with `kraft admin update --channel rc|beta|alpha`. The next stable release's
notes still cover everything since the last stable tag.

A pre-release's `.vsix` is attached to its GitHub Release, stamped
`X.Y.Z-rc.N` (`-alpha.N`, `-beta.N`): semver, so VS Code orders it after the
last release and before `X.Y.Z`. Someone who side-loads an rc is therefore
offered the real `X.Y.Z` when it ships. It used to be packed as plain `X.Y.Z`,
which a side-loaded rc then claimed to be, so the release was never offered.
`vsce package --pre-release` would not have helped: it flags a listing as a
pre-release without changing the version number, and no pre-release is
published to a listing. The count is its own number (`rc.10`, not `rc10`)
because semver compares text, and `rc10` would sort below `rc9`.

## Cut a release

Actions → **release** → **Run workflow** on `main`. Tick **dry run** first to see
the version and notes on the run's summary page without releasing anything.
Leave **sha** empty to release main's tip, or give a commit on main to release
an earlier point. That is useful while the tip's tests are still running. It
cannot be a commit behind the newest release.

`.github/workflows/release.yml` collects every pull request merged since the
previous `vX.Y.Z` tag and bumps by the largest label among them: two
`release::minor` and three `release::patch` is a minor release. If all of them
are `release::none`, there is no version to bump and it releases nothing. The
notes are those pull requests' `## Changelog` sections, grouped into breaking
changes, new features and fixes.

Put `notes::highlight` on the release's headline change, and rarely on more
than one or two pull requests per release. A minor or patch pull request's
entry moves into a **Highlights** section at the top of the notes. A major one
is listed under Highlights and still under Breaking changes, so a breaking
release always says what breaks. Its `release::` label still counts toward the
bump. Before you add it, make sure the `## Changelog` section reads as a
headline, since it now opens the release notes. You can add the label after
merge, then dry-run again to check.

The release stops and names the pull request in three cases:

- It has `notes::highlight` and `release::none`. There is no entry to highlight,
  so remove one of the two labels.
- It has `notes::highlight` and no `release::` label. Add one that ships, not
  `release::none`.
- It has a near miss such as `Notes::Highlight`. Fix the label; `notes::highlight`
  is the only `notes::` label.

It refuses to run until `test` has passed on the commit being released. Then it
builds the wheel, smoke-tests it (installed on the floor Python, 3.12, since the
one wheel serves every supported version), pushes the tag, creates the GitHub Release
with the wheel attached, and publishes to PyPI. A stable release also dispatches
the `docs` workflow, so the docs site's default version switches to the new
tag. If the run shows a "docs rebuild not dispatched" warning, run **docs** by
hand from the Actions tab on `main`. The tag is created locally
before the build (setuptools-scm reads the version from it) and pushed only
after the smoke test passes, so a failed build leaves nothing behind.

## What a stable run publishes

In this order. A step that fails stops the ones after it, and what already
published stays published: the tag exists, so a rerun finds nothing new to
release and you finish the rest by hand. Secrets are repository secrets or
secrets of the `release` environment.

1. **GitHub Release** `vX.Y.Z`, with the wheel and `kraft-X.Y.Z.vsix` attached.
   Needs nothing: the workflow's own `GITHUB_TOKEN`. Check:
   `gh release view vX.Y.Z --json assets --jq '.assets[].name'` lists both files.
2. **Docs rebuild**, a dispatch of the `docs` workflow so `/kraft/` serves the
   new tag. Needs nothing (`GITHUB_TOKEN`, `actions: write`), and only warns
   when it can't dispatch. Check: `gh run list --workflow docs --limit 1` shows
   success.
3. **PyPI**, [`kraft-sdlc`](https://pypi.org/project/kraft-sdlc/). Needs no
   secret: trusted publishing, with the publisher registered on PyPI for this
   repository, `release.yml` and the `release` environment. Check:
   `curl -sf -o /dev/null https://pypi.org/pypi/kraft-sdlc/X.Y.Z/json && echo ok`.
   The run does this itself and fails after 10 minutes.
4. **VS Code Marketplace**, `kraft-sdlc.kraft`. Needs `VSCE_PAT`: a personal
   access token for the `kraft-sdlc` publisher with the Marketplace *Manage*
   scope. **With it empty the publish and its check are skipped and the run
   still goes green**; the `.vsix` is only on the GitHub Release. Check:
   `npx --prefix vscode vsce show kraft-sdlc.kraft`, and read the version. The
   run warns if the Marketplace hasn't listed it within 5 minutes.
5. **Open VSX**, `kraft-sdlc/kraft`, which VSCodium and Cursor install from.
   Needs no secret: trusted publishing, registered for `kraft-sdlc.kraft`
   against this workflow and the `release` environment. Check:
   `curl -s https://open-vsx.org/api/kraft-sdlc/kraft | jq -r .version`.
6. **The stamp pull request**, which brings the five manifests and both
   changelogs up to date on `main`. Needs the release-bot GitHub App:
   `RELEASE_BOT_CLIENT_ID` and `RELEASE_BOT_PRIVATE_KEY`, with the App
   installed on `kraft` and allowed contents and pull-request writes. Auto-merge
   must be on for the repository. Check:
   `gh pr list --state merged --search "stamp plugin manifests" --limit 1` names
   `vX.Y.Z`.
7. **The Homebrew tap**, `itsOmidKarami/homebrew-kraft`, whose formula is pointed
   at step 1's wheel. Needs `HOMEBREW_TAP_TOKEN`: a token with contents write
   on the tap. The tap's `main` is protected, so the token must be one that may
   push to it. Check:
   `curl -s https://raw.githubusercontent.com/itsOmidKarami/homebrew-kraft/main/Formula/kraft.rb | grep '^  url'`
   names `vX.Y.Z`.

Two things the build writes into those artifacts, because the tag alone doesn't:

- **The README's images are pinned to the tag.** `README.md` links its
  screenshots on `main`, which PyPI would show on every version's page for good.
  After the build, `dev/release_artifacts.py pin-wheel` rewrites the wheel's
  description to point at the tag, and the smoke test fails a wheel that still
  links `main`. The wheel, not `README.md`, because a modified tree makes
  setuptools-scm version the build as a dev release. The `.vsix` does the same
  through `vsce package --baseImagesUrl`, which is why `vscode/README.md` links
  its images relative to `vscode/`. A release's page keeps its images; the
  repository's front page and the docs on `/next/` still show `main`'s, and
  releases published before this existed keep the links they have, since PyPI
  can't edit a release.
- **`vscode/CHANGELOG.md` is written by the release, never by hand.** The
  extension ships at Kraft's version, so each section is Kraft's notes for that
  version, from the same pull requests as `CHANGELOG.md`. It is what the
  Marketplace and Open VSX show on the extension's Changelog tab, read from
  inside the `.vsix`, so the build adds the new section for the package and
  puts the file back. The stamp pull request commits it to `main` with the root
  one.

A run with **sha** set to a commit from before `vscode/CHANGELOG.md` existed fails
at the extension build, which comes before the tag is pushed, so nothing is
published and a rerun at a later commit is safe.

## Plugin manifest versions

Each plugin carries its own `version` field in `.claude-plugin/plugin.json`
(shown in `/plugin list`) and in `.cursor-plugin/plugin.json`, for
`plugins/kraft` and `plugins/kraft-lite`. You never edit these by
hand and pull requests never touch them: `release.yml` stamps all five (those
four and the extension's `vscode/package.json`) with
`dev/stamp_plugin_versions.py` right after it tags a release, writes the
release notes into `CHANGELOG.md` and `vscode/CHANGELOG.md`, then opens and
auto-merges a `release::none` pull request with all of them. Doing this on a
release, rather than asking every in-flight pull request to predict its own
future version, is what a hand-stamped file could never do without conflicting
with every other open pull request the moment a release lands.

Opening that pull request needs its own credential: the default `GITHUB_TOKEN`
can't be used, because GitHub suppresses further workflow runs triggered by
`GITHUB_TOKEN`, which would leave the PR's required status checks pending
forever. `release.yml` mints a short-lived token from a GitHub App installed
on this repo instead (`RELEASE_BOT_CLIENT_ID` / `RELEASE_BOT_PRIVATE_KEY`),
scoped to just contents and pull-request writes on `kraft`.

So the tagged commit lags its own release by design: at `vX.Y.Z` the
`plugin.json` files, `vscode/package.json` and both changelogs still describe the
release before it. The artifacts don't lag. The wheel's version comes from the
tag through setuptools-scm, and the `.vsix` gets its version from the
`vsce package <version>` command line and its changelog from the build. The
stamp pull request then brings `main` up to date.

## Verify a release

Run the workflow with **dry run** ticked first. The summary page must show the
version you expect and the notes you expect. After a real run, confirm that the
new `vX.Y.Z` tag and GitHub Release exist, the wheel is attached, the version is
on [PyPI](https://pypi.org/project/kraft-sdlc/), and the stamping pull request
for the plugin manifests has merged. The checks under
[What a stable run publishes](#what-a-stable-run-publishes) cover the other
targets, one line each.

A dry run stops after the notes: every step that builds, packs or publishes is
skipped. So it can't tell you the wheel was pinned, the `.vsix` got its version,
or a credential works; a change to any of those first shows on a real
pre-release (`rc`, which reaches PyPI and the GitHub Release and nothing else).

A stable run checks its own publishing as its last steps. It fails if PyPI
doesn't serve the new version within 10 minutes. It warns if the Marketplace
hasn't listed it within 5; check that by hand, since the Marketplace can be slow
to index. It dispatches the docs rebuild without waiting for it, so open the
**docs** run on the Actions tab and confirm it passed.

If the run stops on an unlabeled pull request, label it (labels can be changed
after merge) and run the workflow again. A failed build leaves no tag behind, so
rerunning is safe.

## The `release/v1` branch

Retired. It was the 1.0 stabilisation branch: it split from `main` at the
`v0.76.2` stamp on 2026-09-19, the `v1.0.0` release candidates were cut from it,
and its last commit is from 2026-09-22, the day `v1.0.0` was tagged on `main`.
Every stable release from `v1.0.0` on, and every release since, was cut from
`main`. The workflow only runs there (`if: github.ref == 'refs/heads/main'`) and
the `release` environment only deploys from `main`, so nothing can release from
`release/v1`. It is safe to delete. The one workflow that still names it is
`.github/workflows/codeql.yml`, in its branch lists. A future maintenance line
would be a branch from a release tag, and the workflow's `if:` and the
environment's branch rule would have to be widened before it could release.
