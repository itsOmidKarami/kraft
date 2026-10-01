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
them touch homebrew, the stamp PR or the Marketplace. Users pick one up with
`kraft admin update --channel rc|beta|alpha`. The next stable release's notes
still cover everything since the last stable tag.

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

It refuses to run until `test` has passed on the commit being released. Then it
builds the wheel, smoke-tests it (installed on the floor Python, 3.12, since the
one wheel serves every supported version), pushes the tag, creates the GitHub Release
with the wheel attached, and publishes to PyPI. A stable release also dispatches
the `docs` workflow, so the docs site's default version switches to the new
tag. If the run shows a "docs rebuild not dispatched" warning, run **docs** by
hand from the Actions tab on `main`. The tag is created locally
before the build (setuptools-scm reads the version from it) and pushed only
after the smoke test passes, so a failed build leaves nothing behind.

## Plugin manifest versions

Each plugin carries its own `version` field in `.claude-plugin/plugin.json`
(shown in `/plugin list`) and in `.cursor-plugin/plugin.json`, for
`plugins/kraft` and `plugins/kraft-lite`. You never edit these by
hand and pull requests never touch them: `release.yml` stamps all four with
`dev/stamp_plugin_versions.py` right after it tags a release, writes the
release notes into `CHANGELOG.md`, then opens and auto-merges a
`release::none` pull request with all of them. Doing this on a
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
`plugin.json` files, `vscode/package.json` and `CHANGELOG.md` still describe the
release before it. The artifacts don't lag. The wheel's version comes from the
tag through setuptools-scm, and the `.vsix` gets its version from the
`vsce package <version>` command line. The stamp pull request then brings
`main` up to date.

## Verify a release

Run the workflow with **dry run** ticked first. The summary page must show the
version you expect and the notes you expect. After a real run, confirm that the
new `vX.Y.Z` tag and GitHub Release exist, the wheel is attached, the version is
on [PyPI](https://pypi.org/project/kraft-sdlc/), and the stamping pull request
for the plugin manifests has merged.

A stable run checks its own publishing as its last steps. It fails if PyPI
doesn't serve the new version within 10 minutes. It warns if the Marketplace
hasn't listed it within 5; check that by hand, since the Marketplace can be slow
to index. It dispatches the docs rebuild without waiting for it, so open the
**docs** run on the Actions tab and confirm it passed.

If the run stops on an unlabeled pull request, label it (labels can be changed
after merge) and run the workflow again. A failed build leaves no tag behind, so
rerunning is safe.
