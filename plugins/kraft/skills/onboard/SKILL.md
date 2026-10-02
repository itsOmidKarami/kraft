---
name: onboard
description: "Use when a repo is not yet connected to Kraft, or a newly connected repo has not been verified."
---

Kraft's tools come from the `kraft` MCP server. If `kraft` is not on PATH, this
plugin has been installed without the program it drives: say so and point at
https://itsomidkarami.github.io/kraft/get-started/install rather than reporting a
connection error.

# Onboarding a repo

Four steps, each followed by a check against the repo itself — a
zero exit code says the command ran, not that what it did was right.

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
   them with what the repo's docs say. When the docs say something else (a
   wrapper the probe cannot know, such as `./ci/run-tests`), connect with the
   repo's own commands rather than correcting afterwards:
   `ensure_repo(test_command=..., setup_command=...)`, or
   `kraft repo connect --test-command ... --setup-command ...`.

   `ensure_repo` saves as soon as it is called, so the person sees the
   commands only through you. Show them what was saved, each command with
   its source, and the candidates it passed over, then ask them to confirm
   or correct it before the first work item. Say which you chose and why.

   A repo with nothing to prepare declares `setup_command: ""`, and a repo
   with no tests declares `test_command: ""` (`--no-tests`). Both mean
   "deliberately nothing", not an oversight. Without a `setup_command`, the
   repo's next work item stops rather than guessing. Declare no tests only
   when the person says the repo has none: every work item on it passes
   verification without running a test. `ensure_repo(test_command="")`
   saves the repo disabled for that reason, and the person enables it.

   The setup command prepares every worktree for this repo, so check it as
   hard as the test command. A repo with more than one project in it gets a
   test scope per project, with its commands run from that directory
   (`sh -c 'cd web && npm test'`). Read those back too.

   Once a repo is connected, `ensure_repo` leaves it alone. Correct it by
   editing this repo's entry in `~/.kraft/templates/repos.yaml` directly, and
   say what you changed. The running server reads that file fresh on each
   request, so no restart is needed. A convention Kraft keeps getting wrong
   across repos belongs in `~/.kraft/templates/detectors.yaml` instead: see
   https://itsomidkarami.github.io/kraft/next/reference/configuration/repos/detectors

   If the repo has submodules, connect writes each `.gitmodules` path as its
   own repo entry, not a sub-field of this one — run `kraft repo list --all`
   (they're `managed: false` until touched, so plain `kraft repo list` won't
   show them) and say how many landed. Each is a real, disabled repo of its
   own: it needs its own probed `test_command` checked the same way as the
   parent's, and its own `enabled: true` (set in its `repos.yaml` entry, as with `test_command`) before any item can
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

4. **Verify.** First `kraft admin health`: it exits 1 when the server is not
   up, and every check after it needs the server. If it fails, report that
   and stop - `kraft admin start` (or the service) comes first. Then `kraft
   admin doctor` - confirm `mcp server` and the `repo <name>` row both read
   `ok`. If either doesn't, stop and report the exact line rather than
   declaring onboarding done with a known problem still open.

Finish by handing off into `kraft:check` for the full drift report
against this install's library and chains — that skill already owns the diff, no need to
repeat it here.
