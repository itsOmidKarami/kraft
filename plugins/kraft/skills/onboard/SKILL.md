---
name: onboard
description: "Connects a repository to Kraft and verifies that its test and setup commands work in a fresh worktree. Use when a repo is not yet connected to Kraft, or a newly connected repo has not been verified. Config drift in an onboarded repo belongs to kraft:check, an unhealthy server to kraft:doctor."
---

Kraft's tools come from the `kraft` MCP server. If `kraft` is not on PATH, this
plugin has been installed without the program it drives: say so and point at
https://kraft-sdlc.dev/get-started/install rather than reporting a
connection error.

# Onboarding a repo

Five steps (connect, register, rehearse, narrow the tests, verify), each followed by a check
against the repo itself — a zero exit code says the command ran, not that what
it did was right. Then hand off to `kraft:check`.

1. **Connect.** Read how the repo says to build and test itself before you
   connect it: its README, CONTRIBUTING, agent instructions (`CLAUDE.md`,
   `AGENTS.md`) and CI config. Look for the command a contributor is told to
   run, and any rule that the raw runner must not be called directly.

   Then `ensure_repo()` (or `kraft repo connect [PATH]` from a terminal). It
   proposes a test and a setup command from the repo's own committed files
   (origin's default branch, so a change not pushed yet is not read), best
   evidence first:
   - a task runner's `test`/`setup` task (justfile, Makefile, Taskfile, mise,
     `script/test`)
   - the toolchain its lockfile names (pnpm, Poetry, Gradle, Cargo, ...)
   - what CI runs, where neither of those has a test command

   It returns each command's source and every other candidate it saw. Compare
   them with what the repo's docs say:
   - They agree: keep what it proposed.
   - The docs say something else (a wrapper the probe cannot know, such as
     `./ci/run-tests`): connect with the repo's own commands rather than
     correcting afterwards:
     `ensure_repo(test_command=..., setup_command=...)`, or
     `kraft repo connect --test-command ... --setup-command ...`.

   `ensure_repo` saves as soon as it is called. With Kraft's own proposal the
   repo is saved enabled, so the person sees the commands only through you.
   Commands you pass save it disabled (the answer's `held` says so): they run
   for every work item, so the person reads them in Settings › Repos and
   enables it there. Either way, show them what was saved, each command with
   its source, and the candidates it passed over, then ask them to confirm
   or correct it before the first work item. Say which you chose and why.

   An empty command means "deliberately nothing", not an oversight:
   - A repo with nothing to prepare declares `setup_command: ""`. A repo with no
     `setup_command` key at all is a different case: its next work item stops
     rather than guessing.
   - A repo with no tests declares `test_command: ""` (`--no-tests`). Declare
     no tests only when the person says the repo has none: every work item on
     it passes verification without running a test. `ensure_repo(test_command="")`
     saves the repo disabled for that reason, and the person enables it.

   The setup command prepares every worktree for this repo, so check it as
   hard as the test command. A repo with more than one project in it gets a
   test scope per project, with its commands run from that directory
   (`sh -c 'cd web && npm test'`). Read those back too.

   Once a repo is connected, `ensure_repo` leaves it alone. To correct its
   commands, tell the person what to change and why: they change it in
   Settings › Repos. Do not edit `~/.kraft/config/repos.yaml` yourself; a
   command there runs for every work item, so a person sets it. A convention Kraft keeps getting wrong
   across repos belongs in `~/.kraft/config/detectors.yaml` instead: see
   https://kraft-sdlc.dev/reference/configuration/repos/detectors

   If the repo has submodules, connect writes each `.gitmodules` path as its
   own repo entry, not a sub-field of this one — run `kraft repo list --all`
   (they're `managed: false` until touched, so plain `kraft repo list` won't
   show them) and say how many landed. Each is a real, disabled repo of its
   own: it needs its own probed `test_command` checked the same way as the
   parent's, and a person enables it in Settings › Repos before any item can
   be scoped to it — connecting the parent does not turn any of them on.

2. **Register.** Nothing to run when this skill came with the Kraft plugin:
   the plugin registers Kraft's MCP server itself, and workers find it there
   (`kraft admin doctor`'s `mcp server` row names the tool they use). Do not
   run `kraft admin init` on top of it. Only a session without the plugin -
   Kraft's tools unreachable, or the skills installed by hand - needs `kraft
   admin init` (user scope); say so and let the person run it.

3. **Rehearse.** A worktree is a fresh checkout of HEAD: nothing untracked
   comes with it. Find out now, while you can still ask, rather than on the
   repo's first work item.

   `kraft repo connect --verify` cuts a throwaway worktree of the commit a
   work item starts from (origin's default branch), runs the declared
   `setup_command` the way every work item does, then each test scope's
   command, and removes the worktree. It prints each command's result and
   time, and the tail of the output of any that failed. It also fails on a
   command that runs past `--timeout` minutes (a test runner in watch mode),
   and on one that leaves files every work item would commit. These are
   the repo's own commands running on this machine, and the test suite can
   take minutes, not seconds: say what you are about to run and let the
   person decide whether to run it now. A sandboxed repo is refused unless
   the person asks for `--on-host`.

   A failure is the finding, not an error to route around. Run
   `git -C <repo> ls-files --others --directory`: a root-level file it lists
   is a file no worktree gets. A toolchain pin (`.python-version`,
   `.nvmrc`, `.tool-versions`), a `.env`, an `.npmrc` — any of these can
   change what the worktree resolves without changing whether either command
   exits zero, so read the list even when `--verify` passes. A pin is the
   likeliest to fail silently: `requires-python = "~=3.11"` is satisfied by
   3.14 too, so the wrong interpreter can pass every test without saying so.

   Anything the repo genuinely needs goes in its `repos.yaml` entry:

   ```yaml
   local_files:
     - .python-version
   ```

   Kraft copies those into every worktree before it runs `setup_command`, and
   **refuses any entry the repo does not gitignore** — it cannot keep an
   unignored file out of a commit. If a needed file is not ignored, add it to
   `.gitignore` rather than dropping it from `local_files`. Re-run
   `kraft repo connect --verify` after editing, and say whether it went green.

   Do not put a directory or a glob in `local_files`; it takes literal file
   paths, and the list is validated on load.

4. **Narrow the tests.** Verification runs a scope's `command`, normally the
   whole suite, in its first round and in every repair round. A repo whose
   tooling can pick the tests a change affects can let verification run only
   those, and leave the full suite to the merge request's CI.

   - **Find the tool.** Look for what the repo already uses to pick tests:
     pytest-testmon, `jest --findRelatedTests`, `nx affected`, Go's package
     graph, Bazel. For each test scope, propose a `targeted_command` beside its
     `command`. Kraft gives it `KRAFT_TEST_BASE` (the branch's base commit) and
     `KRAFT_CHANGED_FILES` (the path of a file listing the changed paths, one
     per line). It must run more, never less, when it cannot see a change: a
     YAML or a generated file is invisible to testmon, so the command runs the
     full suite for one.
   - **`carry`.** Only for a tool whose state lives in files: testmon's
     `.testmondata` and `.testmondata-wal` (a single-process run leaves the
     WAL, an xdist run does not). Only state that is safe when stale: the tool
     checks file content, runs more when unsure, and updates on every run. A
     tool that trusts its cache by timestamp alone does not belong in `carry`.
     The rules are `local_files`': literal file paths, and git must ignore them.

     ```yaml
     carry:
       - .testmondata
     test_scopes:
       - paths: ["src/**", "tests/**"]
         command: <the full suite>
         targeted_command: <the tool's command>
     ```

   - **Check the command for these.** testmon needs xdist to be fast, and
     `--testmon-forceselect` because `-m`, `-k`, `--lf` or a `file::test`
     argument silently turn its selection off. Tests that check committed
     generated files should always run, since testmon cannot see the import-time
     code that makes them stale.
   - **Warn about CI.** Targeted verification leaves the full suite to the
     merge request's CI. When the repo has no forge remote, sets
     `ci_checks: false`, or its default chain opens no merge request (like
     `quick-task`), the full suite then runs nowhere. Say so, and point at the
     `suite: full` option of the verification task, in a second verification
     node after `verification`.
   - **Check that it narrows.** Run the targeted command once to warm it,
     touch one source file, run it again, and confirm the second run ran far
     fewer tests than the full command. Do it in the person's own checkout only
     with their go-ahead, since it writes the tool's state there; otherwise in
     a scratch worktree.
   - **No such tool:** say so, and leave both fields out.

   Finish with `kraft repo connect --verify`. It seeds the carried files as a
   work item would, runs the targeted command against the changes of `HEAD^1`
   (a repo with a single commit skips that run and says so), and lists each
   carried file with where it came from and whether git ignores it.

5. **Verify.** First `kraft admin health`: it exits 1 when the server is not
   up, and every check after it needs the server. If it fails, report that
   and stop - `kraft admin start` (or the service) comes first. Then `kraft
   admin doctor` - confirm `mcp server` and the `repo <name>` row both read
   `ok`. If either doesn't, stop and report the exact line rather than
   declaring onboarding done with a known problem still open.

Finish by handing off into `kraft:check` for the full drift report against
this install's library and chains — that skill already owns the diff, no need
to repeat it here.
