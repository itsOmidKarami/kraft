---
name: release
description: Cuts a Kraft release - a release candidate, a sweep of the installed rc, a fix loop until the sweep is clean, then the stable release on PyPI, GitHub, Homebrew, Open VSX and the VS Code Marketplace. Use when the maintainer asks to cut a release, start with an rc, or ship X.Y.Z.
disable-model-invocation: true
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

Run the scripts from the repo root: `.claude/skills/release/scripts/…`.

**Never hand-edit** `CHANGELOG.md`, `vscode/CHANGELOG.md`, plugin manifest
versions, or `_Added in next_` notes. The release writes them.

**Stop and ask the maintainer before:**

1. Changing a label on any PR but your own fix PRs (for example adding
   `notes::highlight`).
2. The stable (non-rc) run. PyPI publishes cannot be undone.
3. Any fix that changes code, unless they have already said "fix what you find".
4. Anything after a real run that did not conclude `success`: no rerun, no
   publishing by hand, no yanking.

An rc run needs no extra confirmation once they have asked for one, unless its
dry run plans a version they would not expect (a major, or a number other than
the one the last rc led to).

**Tools.** Use `gh` when `gh auth status` succeeds. A cloud session has `gh` but
no valid token: use the GitHub MCP tools there (load them with ToolSearch, and
reload them if they vanish mid-session). Each step gives both. MCP tools cannot
be called from a shell loop, so wait for a run with a background `sleep` (60s
for a dry run, which takes about a minute) and check again when it ends.

```
preflight -> rc dry run -> rc -> rc sweep --clean--> ask -> stable dry run -> go? -> stable -> stamp PR
    ^                               |finding
    +------ fix on main, next rc ---+
```

## Dispatching a run

Every run, dry or real, goes the same way. `gh workflow run` returns before the
run exists, so the newest run straight after a dispatch is often the previous one.

1. Note the newest `release.yml` run's id: `gh run list --workflow release.yml -L 1 --json databaseId`
   / `list_workflow_runs` with `resource_id: release.yml`, `perPage: 1`.
2. Dispatch, always with `sha`, so the release is the commit that passed CI even
   if someone merges in the meantime:
   `gh workflow run release.yml --ref main -f dry_run=<true|false> -f pre=<rc|none> -f sha=<sha>`
   / `actions_run_trigger` with `method: run_workflow`, `workflow_id: release.yml`,
   `ref: main`, `inputs: {dry_run: <true|false>, pre: "<rc|none>", sha: "<sha>"}`.
3. Poll until a `workflow_dispatch` run with a larger id appears, created after
   your dispatch by your account (another dispatch in the same minute would
   otherwise pass for yours), then wait until
   it is `completed` (`gh run watch <id>` in the background / `list_workflow_jobs`
   on the run id, whose job `status` and `conclusion` say when it is done).
4. Read its job log (`gh run view <id> --log` / `get_job_logs` with the job's
   `job_id`, `return_content: true`, `tail_lines: 300`). It prints the notes (Highlights if any,
   New, Fixes, each line ending in its PR number), then one of:
   - `dry run: would release vX.Y.Z…`: a dry run's plan.
   - `releasing vX.Y.Z…`: a real run. A real run whose log lacks this line is the
     wrong run.
   - `nothing since <last-tag> ships; not releasing`: every PR is
     `release::none`. Stop and report.
   - an error naming a PR: a label problem (no `release::` label or two, a
     misspelled `notes::` label, `notes::highlight` on a `release::none` PR).
     Report it; fixing someone's label is the maintainer's call.

## Phase 1: Preflight

Is there something to release, and is main's tip safe to release from?

1. Read `RELEASING.md` and the top of `release.yml`.
2. `git fetch origin main --tags`, then find `<last-tag>` the way the workflow does:
   `git tag --list | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | sort -V | tail -1`.
3. List what ships: `git log --first-parent --oneline <last-tag>..origin/main`.
   Each squash commit ends in its PR number. Note the likely headline.
4. Check open PRs for anything that looks meant for this release, and mention it
   (`gh pr list` / `list_pull_requests`, state `open`).
5. Main's tip must have a `test` run on a push that is `completed` / `success`,
   or the workflow refuses it:
   `gh run list --workflow test --branch main --event push --commit <sha>`
   / `list_workflow_runs` with `resource_id: test.yml`,
   `workflow_runs_filter: {branch: main, event: push}`, `perPage: 3`, which
   cannot filter by commit: pick the run whose `head_sha` is the tip. Wait for it
   if it is still running.

Record the tip sha. Every later run is pinned to it.

Do not rerun the tests locally. That `test` run covers lint, the docs checks,
intent pins, the backend suite, the frontend and the demo smoke, and the workflow
will not release a sha without it. What CI cannot see is the published wheel,
which Phase 3 installs.

## Phase 2: Cut the release candidate

An rc goes to PyPI and a GitHub pre-release, and nothing else: no Homebrew, no
stamp PR, no Marketplace, no Open VSX.

1. **Dry run** with `pre=rc` (see "Dispatching a run"). Show the maintainer the
   notes and the planned `vX.Y.ZrcN`.
2. **Real run**, the same inputs with `dry_run=false`.
3. If it concluded `success`, wait in the background:
   `.claude/skills/release/scripts/wait_published.sh X.Y.ZrcN`. Then check the
   GitHub Release (`gh release view vX.Y.ZrcN` / `list_releases`): a pre-release,
   the install line at the top of its notes, the wheel and `kraft-X.Y.ZrcN.vsix`
   attached. If it did not conclude `success`, see "A real run that failed".

## Phase 3: Sweep the release candidate

Test what users will install: the wheel from PyPI, in a clean venv on Python
3.12 (the floor), with its own `KRAFT_HOME` and port.

1. `.claude/skills/release/scripts/smoke.sh X.Y.ZrcN` (a second argument picks
   the port, default 8799). It prints:
   - the version, which must be the rc
   - `kraft admin health`: `status ok` and the rc version
   - `kraft admin doctor`: every check `ok`, except the expected failures below
   - `kraft admin templates lint`: no errors
   - the web UI's `<title>Kraft`
2. **Exercise the headline feature by hand** on the installed rc. The script
   stopped its server; start it again in bash, with the same isolation:
   ```bash
   W=<the work dir it printed>; P=8799   # or the port you gave it
   for v in $(compgen -e | grep '^KRAFT_'); do unset "$v"; done
   export KRAFT_HOME=$W/home KRAFT_PORT=$P; K=$W/venv/bin/kraft
   $K admin start --port $P > $W/server2.log 2>&1 &   # $K admin stop when done
   ```
   Use `$K` for every command. Feed it inputs that should pass and one that
   should fail, and check that the CLI tells them apart. For v2.2.0 (chains extending chains) that was: a
   `config/chains/lean.yaml` with `extends: default` and a `remove:` list; a
   `broken.yaml` removing a node that does not exist, which
   `kraft admin templates lint` must name; delete it, `kraft admin reload`, lint
   clean; and `kraft admin templates show lean --resolved` leaving out the removed
   nodes.
3. Pick one or two more features from the notes that a smoke run can reach, and
   exercise them the same way.
4. Delete the work dir when you are done with it.

**Expected doctor failures** (not regressions): `kraft on PATH` always fails,
since the smoke venv is never on `PATH`; `mcp server` fails where Kraft's MCP is
not registered in Claude Code's settings (it reads only the registration, so it
can pass while the MCP server is down). Anything else failing is a finding.

## Fix loop

A finding from Phase 3 is fixed on main and shipped as the next rc; an rc is
never patched in place. v2.1.0 went through rc1 to rc7 this way.

1. **Report the finding**: what failed, the command, the output, and whether it
   blocks the release.
2. **Fix it** on a branch, following `CONTRIBUTING.md`: one PR per fix, with a
   `## Changelog` section and a `release::` label you choose and tell the
   maintainer about. A fix to a feature no stable release has shipped yet is
   `release::none`, since the feature's own entry covers it. A fix to something
   users already have is `release::patch`.
3. **Prove it** before pushing: `just lint`, `just test` on the touched code,
   `just test-ui` for the frontend, and the step that failed again.
4. **Open the PR, watch it to green**, and ask the maintainer to merge it (or
   merge it if they have said to).
5. **Back to Phase 1** on the new tip, then the next rc. The workflow numbers it
   `rcN+1` itself.

Repeat until a full Phase 3 sweep comes back clean. Only then go on to Phase 4.

## Phase 4: Stable release

The stable run publishes everywhere and cannot be undone.

1. **Ask in one prompt** (`AskUserQuestion`, with a recommended option):
   - **Highlight:** if the rc notes had no Highlights section, propose the
     headline PR. Check first that its `## Changelog` line reads as a headline.
   - **Credentials:** you cannot check them. Ask the maintainer to confirm the two
     under "Before a stable run" in `RELEASING.md`. If the Marketplace identity
     or its federated credential is gone, the publish fails and the run goes red.
2. If they picked a highlight, add the label: `gh pr edit <n> --add-label notes::highlight`
   / `issue_write` with `method: update`. `issue_write` replaces the whole label
   set, so send the existing `release::` label too:
   `labels: ["release::minor", "notes::highlight"]`.
3. `git fetch origin main`. If `git log <sha>..origin/main` shows any commit that
   is not `release::none`, it is not in the rc you swept: go back to Phase 1 for
   a new rc, or ask the maintainer whether to release the pinned sha anyway.
4. **Dry run** with `pre=none` and the same `sha`. The version must be the last
   rc's without its suffix (`v2.3.0` after `v2.3.0rc2`). If it is not, a label
   changed since the rc: stop and report.
5. **Ask Go / hold**, showing the notes and the version.
6. On Go, the **real run**, the same inputs with `dry_run=false`.
7. If it concluded `success`, wait in the background:
   `.claude/skills/release/scripts/wait_published.sh X.Y.Z pypi brew openvsx`.
   Then check the rest:

   | Target | Check |
   |---|---|
   | GitHub Release | `vX.Y.Z`, not a pre-release, wheel and `.vsix` attached |
   | VS Code Marketplace | the run's "publish the VS Code extension" step succeeded. A failure there usually means the identity's login or membership lapsed, and the extension is not published: report it. |
   | Docs | the `docs` run the release dispatched passed; a warning that it was not dispatched means run `docs` by hand |

8. `.claude/skills/release/scripts/smoke.sh X.Y.Z`, as in Phase 3.

## A real run that failed

Stop and report: the run, the step that failed, and its log. Do not rerun,
publish by hand, or yank anything without the maintainer. What happens next
depends on where it failed. A build before the tag push leaves nothing behind,
and a rerun is safe. After the tag, a rerun plans nothing, and the targets that
did not publish are finished by hand. `RELEASING.md`, "What a stable run
publishes", lists the steps in order and the commands for each.

## Phase 5: After the release

The release is done when the stamp PR has merged.

1. **Stamp PR.** The release bot opens
   `chore: stamp plugin manifests and changelog for vX.Y.Z` (`release::none`) and
   auto-merges it once CI passes; for v2.2.0 that took about 9 minutes. Find it
   with `gh pr list --state all --search "stamp plugin manifests vX.Y.Z"`
   / `search_pull_requests`. If it is still open, watch it to green. If a check
   fails, read that check's log and report it. A patch release that ships an
   `_Added in next_` note does not fail it: the stamp step warns and adds a line
   to the PR (`RELEASING.md`, "Docs Added in notes").
2. **Report**, outcome first: the version, where it is published, what the rc
   and stable smoke runs checked, anything that warned, and the release link.
3. **Offer an announcement.** Build it from `CHANGELOG.md` on main, grouped by
   theme rather than by version, with the install lines
   (`uv tool install kraft-sdlc`, `brew tap itsOmidKarami/kraft && brew install kraft`,
   `kraft admin update`) and the live demo, https://kraft-sdlc.dev/demo/. Keep
   every feature claim to what the changelog says.

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
