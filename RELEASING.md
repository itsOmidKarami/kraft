# Releasing

How a maintainer cuts a Kraft release. Pull request authors need only
[CONTRIBUTING.md](CONTRIBUTING.md#pull-requests-and-release-labels): the label
and the `## Changelog` section. Never edit `CHANGELOG.md` by hand: the release
writes it, and each pull request's `## Changelog` section is its release note.

Kraft's version is the git tag. `setuptools-scm` derives it at build time, so
there is no `version =` line to bump.

## Cut a release

Actions → **release** → **Run workflow** on `main`. Tick **dry run** first to see
the version and notes on the run's summary page without releasing anything.
Leave **sha** empty to release main's tip, or give a commit on main to release
an earlier point. That is useful while the tip's tests are still running. It
cannot be a commit behind the newest release.

`.github/workflows/release.yml` collects every pull request merged since the
previous `vX.Y.Z` tag and bumps by the largest label among them: two
`release::minor` and three `release::patch` is a minor release. If all of them
are `release::none`, there is no version to bump and it releases nothing. The notes are those pull requests'
`## Changelog` sections, grouped into breaking changes, new features and fixes.

It refuses to run until `test` has passed on the commit being released. Then it
builds the wheel, smoke-tests it, pushes the tag, creates the GitHub Release
with the wheel attached, and publishes to PyPI. The tag is created locally
before the build (setuptools-scm reads the version from it) and pushed only
after the smoke test passes, so a failed build leaves nothing behind.

## Plugin manifest versions

`plugins/kraft/.claude-plugin/plugin.json` and `plugins/kraft-lite/.claude-plugin/plugin.json`
carry their own `version` field, shown in `/plugin list`. You never edit this by
hand and pull requests never touch it: `release.yml` stamps both files with
`dev/stamp_plugin_versions.py` right after it tags a release, writes the
release notes into `CHANGELOG.md`, then opens and auto-merges a
`release::none` pull request with both. Doing this on a
release, rather than asking every in-flight pull request to predict its own
future version, is what a hand-stamped file could never do without conflicting
with every other open pull request the moment a release lands.

Opening that pull request needs its own credential: the default `GITHUB_TOKEN`
can't be used, because GitHub suppresses further workflow runs triggered by
`GITHUB_TOKEN`, which would leave the PR's required status checks pending
forever. `release.yml` mints a short-lived token from a GitHub App installed
on this repo instead (`RELEASE_BOT_APP_ID` / `RELEASE_BOT_PRIVATE_KEY`),
scoped to just contents and pull-request writes on `kraft`.

## Verify a release

Run the workflow with **dry run** ticked first. The summary page must show the
version you expect and the notes you expect. After a real run, confirm that the
new `vX.Y.Z` tag and GitHub Release exist, the wheel is attached, the version is
on [PyPI](https://pypi.org/project/kraft-sdlc/), and the stamping pull request
for the plugin manifests has merged.

If the run stops on an unlabeled pull request, label it (labels can be changed
after merge) and run the workflow again. A failed build leaves no tag behind, so
rerunning is safe.
