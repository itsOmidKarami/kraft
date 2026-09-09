# Installation artefacts: version, wheel, update, plugin, release

Installing Kraft today means cloning the repo and running `just install`. That
recipe builds the SPA, copies it into the package tree, and installs from
source. Nothing about it survives leaving this laptop: the version is pinned at
`0.0.0`, no wheel is published anywhere, and a wheel built by any other means
ships an API with no UI.

This design makes a tag produce an installable artefact, and makes an installed
Kraft able to say what version it is and when it is behind.

## Scope

Six pieces, one branch:

1. A real version, derived from the git tag.
2. The built SPA reaching the wheel, and a broken wheel failing loudly.
3. `kraft admin update`, plus a version check on `health`, `doctor` and `start`.
4. The agent skills as real files, split-published as a marketplace plugin.
5. A tag pipeline that publishes a release, and an `install.sh` one-liner.
6. A declared release impact per merge request, so tags keep getting made.

Pieces 1-3 are independent of each other. 4 and 5 both depend on 2. 6 depends
on 5, and is what makes 5 ever run.

## 1. Version from the git tag

`pyproject.toml` drops `version = "0.0.0"` for `dynamic = ["version"]`, adds
`setuptools-scm>=8` to `build-system.requires`, and an empty
`[tool.setuptools_scm]` table.

A tag `v0.3.0` builds `kraft-0.3.0`. An untagged checkout builds
`0.3.1.dev4+g1a2b3c`, which is what a bug report wants: the last release, the
distance from it, and the commit.

Nothing in `src/` changes. `cli._version()` and `init._plugin_manifest()`
already read `importlib.metadata`; their `0.0.0+source` and `0.0.0` fallbacks
remain the answer for a source tree that was never installed, and that is still
the truthful answer.

The cost is one build-time dependency and one CI trap: `setuptools-scm` reads
tags from the git history, so any job that builds needs `GIT_DEPTH: 0`. A
shallow clone has no tags, and every build in it silently versions itself
`0.1.dev1`. The release job sets it; the smoke test in section 2 is what
catches it if a future job forgets.

## 2. The SPA in the wheel

### The hole

`just install` runs `npm run build`, copies `frontend/dist` to
`src/kraft/_bundled/web` and `templates/` to `_bundled/templates`, deletes
`access.yaml` and `notify.yaml` from the copy because one holds a password hash
and the other a webhook URL with a bearer token in it, and only then installs.
`pyproject.toml` ships `_bundled/**/*` as package data. A `uv build` that skips
those steps produces a wheel whose package-data glob matches nothing: a Kraft
that serves JSON and no UI, with nothing in the output saying so.

### The change

The copy steps move out of `just install` into `just bundle`, which both
`just install` and the release job call. One recipe, so the local path and the
CI path cannot drift, and the secret-deletion lines exist once.

This leaves the hole open for a bare `uv build`. Two guards close it where it
matters:

- `admin doctor` gains a check that `_bundled/web/index.html` exists. A
  degraded install reports itself instead of waiting to be noticed.
- The release job installs the wheel it just built into a clean venv and
  asserts both that `kraft --version` prints the tag and that the bundle is
  there. A UI-less or mis-versioned wheel never becomes a release.

The `ponytail:` comment at `justfile:64` is rewritten rather than deleted. Its
ceiling moves from "only this laptop builds a correct wheel" to "only a
`just bundle` build does", and a `build_py` subclass stays the named upgrade
for the day a wheel is built somewhere neither path covers.

## 3. Update and version detection

New module `src/kraft/update.py`, two functions.

`latest()` GETs the GitLab Releases API for the newest tag with a 2s timeout,
and caches the result in `$KRAFT_HOME/run/update-check.json` with a timestamp.
Inside 24h it answers from the cache without a request. Every failure - no
network, a timeout, an unparseable body, a 500 - returns `None`. This function
never raises and never blocks longer than its timeout, because all three of its
callers are on paths that must work on a disconnected machine.

`kraft admin update` shells out to `uv tool install --force --from <wheel-url>
kraft` and prints the old and new versions. It refuses when already current
unless given `--force`.

Three surfaces read `latest()`:

- `admin health` and `admin doctor` gain a row reading `0.3.0 installed, 0.4.0
  available`. It is informational and does not affect the exit code. `doctor`
  exits 1 on any failed check, and a release day must not start failing
  `kraft admin doctor && deploy` in somebody's script.
- `admin start` prints the same notice at boot from the cached read.
  `KRAFT_NO_UPDATE_CHECK=1` skips the check entirely. A cold cache with no
  network costs the 2s timeout once per day, not once per start.

Nothing replaces the binary without the user typing `admin update`. The server
orchestrates work items in live worktrees; upgrading it underneath them is a
different feature with a rollback story attached, and this is not it.

## 4. The agent skills as files, and the marketplace plugin

`kraft admin init` writes `board`, `gates`, `handoff` and `status` into
`~/.claude/skills/kraft/` from Python string literals in `src/kraft/init.py`.
That works and is the current delivery path, but a marketplace plugin needs
those skills as files in a publishable tree, and maintaining the same content
twice is how the two versions start disagreeing. (There are four of them, not
three as an earlier draft of this section said - which is itself the argument
for a directory glob over a hand-kept list.)

The literals move to `src/kraft/plugin/skills/<name>/SKILL.md`.
`init._write_plugin()` copies files instead of writing strings; the content and
the written result are unchanged. `[tool.setuptools.package-data]` grows
`plugin/**/*` so the wheel still carries them, which is what keeps
`admin init` working from an installed Kraft.

That directory is then also the marketplace plugin. It gains
`.claude-plugin/plugin.json` and `marketplace.json`, and `just plugin-publish`
force-pushes it to its own public repo, modelled directly on `lite-publish`:
regenerate, validate with `claude plugin validate --strict`, refuse if the
version's tag already exists, tag `kraft--v<version>`, push. The version is
stamped from the git tag at publish time, because a static manifest cannot
compute what `importlib.metadata` computes.

`dev/check_lite_version.py` is generalised to take the manifest path and the
surface globs as arguments, and CI calls it once per plugin. A second copy of
that logic is the thing this avoids.

The skills gain a first instruction: if `kraft` is not on `PATH`, say so and
point at the installer. A user who installs the plugin from the marketplace
without the binary otherwise meets the problem as an MCP connection error.

## 5. Tag pipeline and installer

One new `release` stage, gated on `if: $CI_COMMIT_TAG`. The existing `workflow`
rules already admit tag pipelines, so no change there. The job:

1. `GIT_DEPTH: 0`, so setuptools-scm sees the tag.
2. `just bundle` - `npm ci`, `npm run build`, copy, delete the secret YAMLs.
3. `uv build`.
4. Smoke test: fresh venv, install the wheel, assert the version matches
   `$CI_COMMIT_TAG` and `_bundled/web/index.html` is present.
5. Upload the wheel to the project's Generic Package Registry.
6. A `release:` block creates the GitLab Release pointing at it.

`install.sh` is committed at the repo root and served from the release. It
resolves the newest tag from the Releases API, ensures `uv` is present,
runs `uv tool install --from <wheel-url> kraft`, and prints `kraft admin init`
as the next step. The README's install section becomes that one line;
`just install` stays and is documented as the from-source developer path.

This is a `curl | sh` install, with the objections that implies. What reduces
them: the script is short and committed, it is reviewable at a stable URL
before running, and the artefact it fetches is attached to a tagged release
rather than fetched from a mutable location. What does not reduce them is
arguing the pattern is fine.

## 6. Keeping the version moving

Deriving the version from the tag removes the bump commit, and with it the
thing anyone could forget to do *loudly*. The new failure mode is quieter and
worse: nothing ever fails, main accumulates untagged commits, and the newest
release stays whatever it was months ago while `install.sh` keeps serving it.
A version scheme with no process for advancing it is a pinned `0.0.0` with
extra steps.

So every merge request declares its release impact, and merging is what tags.

### Declaring

A merge request carries exactly one of four labels: `release::major`,
`release::minor`, `release::patch`, `release::none`. GitLab's scoped labels
enforce the "exactly one" part - applying a second one in the same scope
removes the first - so the MR job only has to check that the scope is present
at all.

A new MR job, on `merge_request_event` only, reads `$CI_MERGE_REQUEST_LABELS`
and fails when no `release::` label is set. The failure message names the four
labels. This is a bookkeeping failure and it will annoy people; it is also the
only moment where the author knows what the change is worth, which is the whole
reason the declaration lives here and not on main.

`release::none` is a first-class answer, and the expected one for docs,
comments, CI config and test-only changes. It is not an escape hatch, it is the
declaration that this change ships nothing to a user.

### Tagging

A main-branch job, after the merge lands, asks the API which merge request
produced `$CI_COMMIT_SHA` (`/projects/:id/repository/commits/:sha/merge_requests`),
reads its `release::` label, and:

- `none` - exits 0, having done nothing. Most merges take this path.
- otherwise - computes the next tag from `git describe --tags --abbrev=0` with
  that component incremented, and pushes it. Like the release job, this one
  needs `GIT_DEPTH: 0`: a shallow clone finds no tag and would compute every
  release as the first one.

That tag then fires the release pipeline from section 5 exactly as a
hand-pushed tag would. One merge, at most one release, its size chosen by the
person who wrote the change.

The job needs a project access token with write access to protected tags; the
default `CI_JOB_TOKEN` cannot push. That token is the one genuinely new secret
this design introduces, and it is scoped to this project.

Two edges worth stating, because both are silent otherwise:

- A commit reaching main without a merge request - a direct push, a revert
  button, a cherry-pick - has no labels to read. The job treats a missing MR as
  `none` and logs that it did. Direct pushes to main do not release.
- The tag push must not itself trigger the main job again. The `workflow` rules
  already separate `$CI_COMMIT_TAG` from `$CI_COMMIT_BRANCH`, so this holds,
  but it is the failure that would loop the pipeline forever and it is worth a
  comment in the file.

### Why not the alternatives

Tagging every merge automatically needs no process and produces no meaning: a
typo fix and a rewritten executor both become patch releases, and the version
stops answering "how much changed". A `just release` recipe run by hand keeps
the meaning and loses the guarantee - it is exactly the step that stops
happening, which is the problem this section exists to solve.


## Testing

- Version: a test asserts `kraft --version` matches `importlib.metadata`, and
  that the not-installed fallback path still returns rather than raising.
- Bundle: the `doctor` check gets a test with the bundle present and absent.
  The wheel itself is covered by the release job's smoke test, not by pytest -
  building a wheel per test run is minutes for a guarantee CI already gives.
- Update: `latest()` is tested against a stubbed HTTP layer for the success,
  timeout, garbage-body and cache-hit paths. The assertion that matters is
  that only the success path returns non-`None` and that none of them raise.
  `admin update` is tested for its refusal-when-current behaviour with the
  subprocess stubbed; it is not tested end to end, because the thing it does
  is replace the binary running the test.
- Plugin: the existing `admin init` tests already assert the written tree.
  They should pass unchanged after the literals become files - that is the
  point of the refactor, and a change in them means the content drifted.
- Release impact: the next-tag computation is a pure function of the previous
  tag and the label, and is tested as one - including the first-release case
  where `git describe` finds no tag at all.
- Pipeline: not testable before it runs. The first tag is the test, and the
  smoke step is what makes its failure loud instead of silent.

## Explicitly not in scope

- Self-updating server. Section 3 says why.
- PyPI. The release-asset URL is the install target; PyPI is a later decision
  about a permanent public name, not a packaging problem.
- Signing the wheel or the install script.
- A changelog generator. The GitLab Release body is written by hand.
- Enforcing that the declared impact matches the diff. Nothing checks that a
  breaking change was labelled `major`; the label is a claim by its author,
  and review is what tests it.
- macOS/Linux binaries via PyInstaller. `uv` fetching a 3.14 interpreter is
  the supported path.
