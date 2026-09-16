---
name: board
description: Use when you need to know what Kraft is doing - what work is running, what
  is blocked or waiting on a person, the state of one work item, or whether a decision
  was already made in a spec or plan somewhere across the repos.
---

Kraft's tools come from the `kraft` MCP server. If `kraft` is not on PATH, this
plugin has been installed without the program it drives: say so and point at
https://github.com/itsOmidKarami/kraft#install rather than reporting a
connection error.

# Reading Kraft

- `list_work_items(status)` - the board. `status="paused"` is what is waiting on
  a person; `status="active"` is what is running now.
- `get_work_item()` - one item in full: its chain, its current node, any gate it
  is waiting on. With no argument it resolves the item this session is standing
  in, which is correct when the cwd is a Kraft worktree.
- `search(q)` - specs, plans, and session summaries across every connected repo.

**Search before writing a spec.** The decision you are about to make may already
have been made and written down in another repo. That is the whole reason the
index spans them.
