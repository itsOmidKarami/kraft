---
name: doctor
description: "Use when the Kraft server seems unhealthy or unresponsive, work items are not being picked up, or an upgrade or config change needs applying."
---

Kraft's tools come from the `kraft` MCP server. If `kraft` is not on PATH, this
plugin has been installed without the program it drives: say so and point at
https://github.com/itsOmidKarami/kraft#install rather than reporting a
connection error.

# When Kraft itself is unwell

This is the server's health, not one work item's or one repo's. A repo that
will not connect is `kraft:onboard`; a stopped item is `kraft:triage`.

## 1. Diagnose

```bash
kraft admin health     # exit 1 when degraded
kraft admin doctor     # every check at once; exit 1 on any
```

Run both and report every failing line verbatim. Paraphrasing loses the check
name, and the check name is what the fix is keyed on.

"Nothing is being picked up" is not always a health failure: Kraft files items
paused unless they were created with `--autostart`. If both checks pass, run
`kraft view list --status=paused` and hand what you find to `kraft:gates`.

## 2. Fix what the line names

| Situation | Action |
|---|---|
| Edited the template library or `policy.yaml` | `kraft admin reload`: rereads from disk, no restart |
| Server wedged or running old code | `kraft admin restart`: stops, then starts it the way it was running |
| Install is out of date | `kraft admin update [--restart]` |
| MCP server or a repo row not `ok` | `kraft admin init --repo`, then doctor again |

`reload` is safe to run. `restart` and `update` interrupt whatever the server is
running, so say that and ask first. `update` also replaces the installed
program.

## 3. Verify

Run `kraft admin doctor` again and report the result. A fix that was not
followed by a clean run is a guess. If a line still fails, stop and report it
rather than trying a further command.
