---
name: doctor
description: "Diagnoses an unhealthy Kraft server and applies the reload, restart or update its health check calls for. Use when the server seems unhealthy, degraded or unresponsive (connection refused, kraft admin doctor fails), no work item starts though none was filed paused, or an upgrade or config change needs applying. A repo that will not connect is kraft:onboard; a stopped work item is kraft:triage."
---

Kraft's tools come from the `kraft` MCP server. If `kraft` is not on PATH, this
plugin has been installed without the program it drives: say so and point at
https://kraft-sdlc.dev/get-started/install rather than reporting a
connection error.

# When Kraft itself is unwell

This is the server's health, not one work item's or one repo's. A repo that
will not connect is `kraft:onboard`; a stopped item is `kraft:triage`.

## 1. Diagnose

```bash
kraft admin health     # exit 1 when degraded
kraft admin doctor     # every check at once; exit 1 on any
```

Run both and report every `FAIL` and `warn` row, and the `version` row,
verbatim. Paraphrasing loses the check name, and the check name is what the
fix is keyed on.

"Nothing is being picked up" is not always a health failure: Kraft files items
paused unless they were created with `--autostart`. If both checks pass, run
`kraft view list --all --status=paused` and hand what you find to `kraft:gates`.

## 2. Fix what the line names

| Situation | Action |
|---|---|
| Edited the template library, `policy.yaml` or `intake.yaml` | `kraft admin reload`: rereads from disk, no restart |
| `server` fails | `kraft admin start --detach`: `restart` refuses when there is nothing to restart |
| Server wedged, or `restart` warns it runs old code | `kraft admin restart`: stops, then starts it the way it was running |
| `version` says a newer release is available | `kraft admin update [--restart]` |
| `mcp server` not `ok` | Install the Kraft plugin, or `kraft admin init` for a terminal-only setup; then doctor again |
| A `repo <name>` row not `ok` | `kraft:onboard` |
| A `kraft-default` row that warns, or a `chains`, `agent: <profile>`, `capabilities` or `setup <repo>` row not `ok` | That is config drift, not server health: hand it to `kraft:check` |
| Any other row | Its entry at https://kraft-sdlc.dev/troubleshooting/doctor-failures; run nothing outside this table without asking |

`reload` is safe to run. Ask before `restart` and `update`: run `kraft view
list --all --status=active` and name what it shows. A restart leaves a worktree
item's local steps running, but ends the steps of an item that runs in place.
Offer `--when-free` to wait for nothing to be running, and pass `-y` only once
the person agrees. `update` also replaces the installed program.

## 3. Verify

Run `kraft admin doctor` again and report the result. A fix that was not
followed by a clean run is a guess. If a line still fails, stop and report it
rather than trying a further command.
