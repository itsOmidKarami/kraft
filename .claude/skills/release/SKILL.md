---
name: release
description: Cuts a Kraft release - a release candidate, a sweep of the installed rc, a fix loop until the sweep is clean, then the stable release on PyPI, GitHub, Homebrew, Open VSX and the VS Code Marketplace. Use when the maintainer asks to cut a release, start with an rc, or ship X.Y.Z.
disable-model-invocation: true
argument-hint: "[rc|stable]"
---

# Release Kraft

This drives the `release` workflow (`.github/workflows/release.yml`) on
`itsOmidKarami/kraft`, checks every output, and stops only for the maintainer's
decisions. `RELEASING.md` and `release.yml` are the source of truth: read both at
the start of every run, and where they disagree with this skill, they win.

Two names, used throughout:

- **`<last-tag>`**: the newest exact `vX.Y.Z` tag, the one this release follows.
- **`X.Y.Z`**: the version being released. The workflow computes it from the
  largest `release::` label among the PRs since `<last-tag>`; read it from the
  dry run. Never pick a number yourself.

**Never hand-edit** `CHANGELOG.md`, `vscode/CHANGELOG.md`, plugin manifest
versions, or `_Added in next_` notes. The release writes them.

**Stop and ask the maintainer before:**

1. Adding or changing any PR label (for example `notes::highlight`).
2. The stable (non-rc) run. PyPI publishes cannot be undone.
3. Any fix that changes code, unless they have already said "fix what you find".

An rc run needs no extra confirmation once they have asked for one.

**Tools.** Where `gh` exists, use it. A cloud session has no `gh`: use the GitHub
MCP tools instead (load them with ToolSearch, and reload them if they vanish
mid-session). Each step gives both.

```
preflight -> local sweep -> rc dry run -> rc -> rc sweep --clean--> ask -> stable dry run -> stable -> stamp PR
                  ^                                  |finding
                  +------- fix on main, next rc -----+
```

## Phase 1: Preflight

Is there something to release, and is main's tip safe to release from?

1. Read `RELEASING.md` and the top of `release.yml`.
2. `git fetch origin main --tags`, then find `<last-tag>` the way the workflow does:
   `git tag --list | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | sort -V | tail -1`.
3. List what ships: `git log --first-parent --oneline <last-tag>..origin/main`.
   Each squash commit ends in its PR number. Note the likely headline.
4. Check open PRs for anything that looks meant for this release, and mention it
   (`gh pr list` / `list_pull_requests`, state `open`).
5. Main's tip must have a `test` run that is `completed` / `success`, or the
   workflow refuses to run: `gh run list --workflow test --branch main --commit <sha>`
   / `list_workflow_runs` on branch `main`. If it is still running, do Phase 2
   while you wait.

Record the tip sha. Every later run is pinned to it.

Label problems (a PR with no `release::` label or two, a misspelled `notes::`
label, `notes::highlight` on a `release::none` PR) need no check here: the dry run
fails on each, naming the PR.

## Phase 2: Local sweep

The checks CI does not repeat at release time, about 10 minutes. CI already ran
the full backend suite on main; do not rerun it. Write each step's log to a file
and show only its tail. Any failure goes to the fix loop before an rc is cut.

| Step | Command | Pass means |
|---|---|---|
| `just`, if missing | `uv tool install rust-just` | `which just` prints a path |
| Set up | `just setup` | exit 0 |
| Lint | `just lint` | "All checks passed" and formatting clean |
| Frontend | `just test-ui` | exit 0 |
| Intent pins | `just intent` | 0 unpinned, 0 broken |
| Dashboard demo | `just demo-smoke` | every Playwright test passes |

`kraft admin templates lint` needs a running server, so it runs in Phase 4.

## Phase 3: Cut the release candidate

An rc goes to PyPI and a GitHub pre-release, and nothing else: no Homebrew, no
stamp PR, no Marketplace, no Open VSX.

1. **Dry run**, pinned to the tip sha:
   `gh workflow run release.yml --ref main -f dry_run=true -f pre=rc -f sha=<sha>`
   / `actions_run_trigger` with `method: run_workflow`, `workflow_id: release.yml`,
   `ref: main`, `inputs: {dry_run: true, pre: "rc", sha: "<sha>"}`.
   Find the run (`gh run list --workflow release.yml -L 1` / `list_workflow_runs`,
   resource `release.yml`, perPage 1), wait for it to finish, and read the job log
   (`gh run view <run-id> --log` / `list_workflow_jobs`, then `get_job_logs` with
   the `job_id`). The log prints the notes (Highlights if any, New, Fixes, each
   line ending in its PR number), then `dry run: would release vX.Y.ZrcN`. A
   label problem fails the run here instead, naming the PR.
2. **Real run.** The same call with `dry_run=false`. Always pass `sha`: it pins the
   release to the commit that passed CI and your sweep, even if someone merges
   in the meantime.
3. **Wait for PyPI** in the background: `scripts/wait_published.sh X.Y.ZrcN`. Then
   confirm the run concluded `success` and the GitHub Release exists as a
   pre-release with the install line at the top of its notes.

## Phase 4: Sweep the release candidate

Test what users will install: the wheel from PyPI, in a clean venv on Python
3.12 (the floor), with its own `KRAFT_HOME` and port.

1. `scripts/smoke.sh X.Y.ZrcN` (a second argument picks the port, default 8799).
   It prints:
   - the version, which must be the rc
   - `kraft admin health`: `status ok` and the rc version
   - `kraft admin doctor`: every check `ok`, except the expected failures below
   - `kraft admin templates lint`: no errors
   - the web UI's `<title>Kraft`
2. **Exercise the headline feature by hand** on the installed rc, in the work dir
   the script printed, with that `KRAFT_HOME`. Write the inputs that should pass
   and one that should fail, and check that the CLI tells them apart. For v2.2.0
   (chains extending chains) that was: a `config/chains/lean.yaml` with
   `extends: default` and a `remove:` list; a `broken.yaml` removing a node that
   does not exist, which `kraft admin templates lint` must name; delete it,
   `kraft admin reload`, lint clean; and `kraft admin templates show lean --resolved`
   leaving out the removed nodes.
3. Pick one or two more features from the notes that a smoke run can reach, and
   exercise them the same way.

**Expected doctor failures** (not regressions): `kraft on PATH` always fails,
since the smoke venv is never on `PATH`; `mcp server` fails where Kraft's MCP is
not registered, as in a cloud session. Anything else failing is a finding.

## Fix loop

A finding from Phase 2 or Phase 4 is fixed on main and shipped as the next rc;
an rc is never patched in place. v2.1.0 went through rc1 to rc7 this way.

1. **Report the finding**: what failed, the command, the output, and whether it
   blocks the release.
2. **Fix it** on a branch, following `CONTRIBUTING.md`: one PR per fix, with a
   `## Changelog` section and a `release::` label. A fix to a feature no stable
   release has shipped yet can be `release::none`, since the feature's own entry
   covers it (#978). A fix to something users already have is `release::patch`.
3. **Prove it** before pushing: `just lint`, `just test` on the touched code,
   `just test-ui` for the frontend, and the failing sweep step again.
4. **Open the PR, watch it to green**, and ask the maintainer to merge it (or
   merge it if they have said to).
5. **Back to Phase 1** on the new tip: wait for its `test` run, redo the local
   sweep, cut the next rc. The workflow numbers it `rcN+1` itself.

Repeat until a full Phase 4 sweep comes back clean. Only then go on to Phase 5.

## Phase 5: Stable release

The stable run publishes everywhere and cannot be undone, so it starts with
questions. **Ask in one prompt** (`AskUserQuestion`, with a recommended option):

- **Highlight:** if the rc notes had no Highlights section, propose the headline
  PR. Check first that its `## Changelog` line reads as a headline.
- **Credentials:** you cannot check them. Ask the maintainer to confirm the two
  under "Before a stable run" in `RELEASING.md`. If `VSCE_PAT` is empty, the
  Marketplace steps are skipped and the run still goes green.
- **Go / hold.**

Then:

1. If they picked a highlight, add the label: `gh pr edit <n> --add-label notes::highlight`
   / `issue_write` with `method: update`. `issue_write` replaces the whole label
   set, so send the existing `release::` label too:
   `labels: ["release::minor", "notes::highlight"]`.
2. Confirm main has not moved past the pinned sha in a way that matters:
   `git log <sha>..origin/main` is empty, or every new commit is `release::none`.
3. **Dry run** with `pre=none` and the same `sha`. The log must show the notes,
   Highlights included if you added one, and `dry run: would release vX.Y.Z`.
4. **Real run**, the same inputs with `dry_run=false`.
5. Wait in the background: `scripts/wait_published.sh X.Y.Z pypi brew openvsx`.
   Then confirm the run concluded `success` and check the rest:

   | Target | Check |
   |---|---|
   | GitHub Release | `vX.Y.Z`, not a pre-release, wheel and `.vsix` attached |
   | VS Code Marketplace | the run's "publish the VS Code extension" step succeeded; its listing check only warns |
   | Docs | the `docs` run the release dispatched passed |

6. `scripts/smoke.sh X.Y.Z`, as in Phase 4.

## Phase 6: After the release

The release is done when the stamp PR has merged.

1. **Stamp PR.** The release bot opens
   `chore: stamp plugin manifests and changelog for vX.Y.Z` (`release::none`) and
   auto-merges it once CI passes; for v2.2.0 that took about 9 minutes. Find it
   (`gh pr list --search "stamp plugin manifests vX.Y.Z"` / `search_pull_requests`).
   If it is still open, watch it to green. If it is red, the usual cause is an
   `_Added in next_` note on a patch release (`RELEASING.md`, "Docs Added in notes").
2. **Report**, outcome first: the version, where it is published, what the sweeps
   checked, anything that warned, and the release link.
3. **Offer an announcement.** Build it from `CHANGELOG.md` on main, grouped by
   theme rather than by version, with the install lines
   (`uv tool install kraft-sdlc`, `brew tap itsOmidKarami/kraft && brew install kraft`,
   `kraft admin update`) and the live demo link. Keep every feature claim to what
   the changelog says.

## Gotchas

| Gotcha | What to do |
|---|---|
| `get_job_logs` answers 404 | The job is still running. Wait for `get_workflow_job` to report `completed`, then fetch the log. |
| `get_job_logs` needs a `job_id` | Get it from `list_workflow_jobs` on the run id; `run_id` works only with `failed_only: true`. |
| `issue_read get_labels` fails on a PR number | Read a PR's labels with `search_pull_requests` (fields `labels`); `issue_write update` does work on PRs. |
| `issue_write` replaces the label set | Always send the full set, the existing `release::` label included. |
| A large tool result lands in a file | Parse it with Python instead of reading it whole. |
| Foreground `sleep` is blocked | Wait with a `run_in_background` command or a Monitor. Never chain short sleeps. |
| `git pull origin main` on a cloud session's branch | It fast-forwards the branch, and the stop hook reports an unpushed commit. Use `git fetch` only; if it happens, push the branch, which carries only main's commits. |
| A merge lands between the rc and stable | Pin `sha` on every run, and check `git log <sha>..origin/main` before the stable one. |
