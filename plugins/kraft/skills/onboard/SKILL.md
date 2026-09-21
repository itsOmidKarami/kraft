---
name: onboard
description: Use to connect a new repo to Kraft - runs repo connect, admin init --repo and admin doctor in sequence, then verifies each step actually matches this repo's real setup before calling it done.
---

Kraft's tools come from the `kraft` MCP server. If `kraft` is not on PATH, this
plugin has been installed without the program it drives: say so and point at
https://github.com/itsOmidKarami/kraft#install rather than reporting a
connection error.

# Onboarding a repo

Four mechanical steps, each followed by a check against the repo itself — a
zero exit code says the command ran, not that what it did was right.

1. **Connect.** `ensure_repo()` (or `kraft repo connect [PATH]` from a
   terminal) - files the repo and probes it: test command, forge. Read back
   the probed `test_command`.

   Check it against what the repo actually runs: is there a `Justfile` or
   `justfile` at the repo root with a `test` recipe? If so, and it differs
   from the probed command, prefer that recipe's command and say so —
   `probe_repo` has no `Justfile` marker yet (Kraft-reriq), so a repo like
   this one gets probed with raw `pytest`, which its own CLAUDE.md says never
   to run directly.

   Neither `ensure_repo`/`kraft repo connect` nor any MCP tool takes a
   `test_command` override — only `PATCH /repos` on the API does, and there
   is no CLI/MCP verb for it. So correct a wrong probe by editing this
   repo's entry in `repos.yaml` (under Kraft's templates dir) directly,
   setting `test_command` to the right command, and say what you changed —
   the running server reads that file fresh on each request, no restart
   needed.

   Read back the probed `setup_command` the same way, and check it just as
   hard. It is what prepares every worktree for this repo, and there is no
   default behind it: a repo with no `setup_command` stops its next work item
   rather than guessing. A repo that genuinely needs no preparation declares
   `setup_command: ""` — deliberately nothing, not an oversight. Correct a
   wrong probe by editing this repo's entry in `repos.yaml` directly, the same
   way as `test_command`.

   If the repo has submodules, connect writes each `.gitmodules` path as its
   own repo entry, not a sub-field of this one — run `kraft repo list --all`
   (they're `managed: false` until touched, so plain `kraft repo list` won't
   show them) and say how many landed. Each is a real, disabled repo of its
   own: it needs its own probed `test_command` checked the same way as the
   parent's, and its own `enabled: true` (`PATCH /repos`) before any item can
   be scoped to it — connecting the parent does not turn any of them on.

2. **Register.** `kraft admin init --repo` - registers Kraft's MCP server and
   skills for this repo's agent. It prints every path it wrote
   (`kraft: wrote <path>`); read those lines rather than assuming — the flag
   means repo-scope (a `.mcp.json` under the repo root), so a run that prints
   a path outside the repo means something is off before you go further.

3. **Rehearse.** A worktree is a fresh checkout of HEAD: nothing untracked
   comes with it. Find out now, while you can still ask, rather than on the
   repo's first work item.

   Cut a throwaway worktree outside the repo, once, capturing the path
   `mktemp` actually created:

   ```bash
   PROBE=$(mktemp -d /tmp/kraft-onboard-XXXXXX)
   git -C <repo> worktree add -q "$PROBE" -b "kraft-onboard-${PROBE##*-}"
   echo "$PROBE"
   ```

   Every command below is a separate shell — `$PROBE` will not still be set in
   it. Read the path the `echo` printed and substitute that literal value
   (e.g. `/tmp/kraft-onboard-a1b2c3`) everywhere `$PROBE` appears from here on.
   Re-running `mktemp` for a later command gives you a directory with no
   worktree in it, not the one you just created — that mismatch is what made
   the old `$$`-based version of this step fail its own cleanup.

   First, the repo's own declared preparation — this is what every real
   worktree gets, so a wrong `setup_command` should fail here, once, while
   someone is still watching:

   ```bash
   (cd "$PROBE" && <setup_command>)
   ```

   Use the `setup_command` confirmed in step 1, not a guess.

   Next, cheap and fast — whatever fits this repo's toolchain (`uv run
   python -V`, `node -v`, ...), not the test suite:

   ```bash
   (cd "$PROBE" && uv run python -V)
   ```

   This is the one likeliest to catch a missing pin silently: `requires-python
   = "~=3.11"` is satisfied by 3.14 too, so the wrong interpreter can pass
   every test without ever saying so (Kraft-gxcmy).

   Second probe, opt-in — the repo's own test command, in the same worktree.
   Say what you're about to run and roughly how long it takes before you run
   it (a cold `uv sync` plus a full suite can be minutes, not seconds), and
   let the person decide whether to wait for it now:

   ```bash
   (cd "$PROBE" && <test_command>)
   ```

   Use the `test_command` you confirmed in step 1, not a guess. Then clean up,
   with the same literal path:

   ```bash
   git -C <repo> worktree remove --force "$PROBE"
   git -C <repo> branch -D "kraft-onboard-${PROBE##*-}"
   ```

   A failure in either probe is the finding, not an error to route around.
   Compare `git -C <repo> ls-files --others --directory` against the
   worktree: a root-level file listed there and missing from the probe is a
   file Kraft will not carry either. A toolchain pin (`.python-version`,
   `.nvmrc`, `.tool-versions`), a `.env`, an `.npmrc` — any of these can
   change what the worktree resolves without changing whether either probe
   exits zero, so read the list even when both probes pass.

   Anything the repo genuinely needs goes in its `repos.yaml` entry:

   ```yaml
   local_files:
     - .python-version
   ```

   Kraft copies those into every worktree before it runs `setup_command`, and
   **refuses any entry the repo does not gitignore** — it cannot keep an
   unignored file out of a commit. If a needed file is not ignored, add it to
   `.gitignore` rather than dropping it from `local_files`. Cut a fresh probe
   worktree and re-run after editing, and say whether it went green.

   Do not put a directory or a glob in `local_files`; it takes literal file
   paths, and the list is validated on load.

4. **Verify.** `kraft admin doctor` - confirm `mcp server` and the `repo
   <name>` row both read `ok`. If either doesn't, stop and report the exact
   line rather than declaring onboarding done with a known problem still
   open.

Finish by handing off into the `check` skill for the full drift report
against this install's library and chains — that skill already owns the diff, no need to
repeat it here.
