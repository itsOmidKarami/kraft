---
name: onboard
description: Use to connect a new repo to Kraft - runs repo connect, admin init --repo and admin doctor in sequence, then verifies each step actually matches this repo's real setup before calling it done.
---

Kraft's tools come from the `kraft` MCP server. If `kraft` is not on PATH, this
plugin has been installed without the program it drives: say so and point at
https://gitlab.com/itsOmidKarami/kraft#install rather than reporting a
connection error.

# Onboarding a repo

Three mechanical steps, each followed by a check against the repo itself — a
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

3. **Verify.** `kraft admin doctor` - confirm `mcp server` and the `repo
   <name>` row both read `ok`. If either doesn't, stop and report the exact
   line rather than declaring onboarding done with a known problem still
   open.

Finish by handing off into the `check` skill for the full drift report
against this repo's registry — that skill already owns the diff, no need to
repeat it here.
