---
name: board
description: "Reads what Kraft is doing across work items: the board, and a cross-repo search of specs, plans and session summaries. Use when asked what work is running, blocked or waiting on a person, or whether a decision was already made in a spec or plan in another repo. One item's progress or a watch belongs to kraft:status, and a stopped item to kraft:triage."
---

Kraft's tools come from the `kraft` MCP server. If `kraft` is not on PATH, this
plugin has been installed without the program it drives: say so and point at
https://itsomidkarami.github.io/kraft/get-started/install rather than reporting a
connection error.

# Reading Kraft

- `list_work_items(status)` - the board. `status="needs_human"` is what is
  waiting on a person: an item with a pending gate needs an answer
  (`kraft:gates`), one without has stopped (`kraft:triage`). `status="paused"`
  is filed but not started, or paused by hand; `status="active"` is running now.
  Without `status` it lists every item except abandoned ones.
- `get_work_item()` - one item in full: its chain, its current node, any gate it
  is waiting on. With no argument it resolves the item this session is standing
  in, which is correct when the cwd is a Kraft worktree.
- `search(q)` - specs, plans, and session summaries across every connected repo.

**Search before writing a spec.** The decision you are about to make may already
have been made and written down in another repo. That is the whole reason the
index spans them.
