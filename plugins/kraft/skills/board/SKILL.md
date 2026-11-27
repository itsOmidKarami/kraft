---
name: board
description: "Reads what Kraft is doing across work items: the board, and a cross-repo search of specs, plans and session summaries. Use when asked what work is running, blocked or waiting on a person, or whether a decision was already made in a spec or plan in another repo. One item's progress or a watch belongs to kraft:status, and a stopped item to kraft:triage."
---

Kraft's tools come from the `kraft` MCP server. If `kraft` is not on PATH, this
plugin has been installed without the program it drives: say so and point at
https://kraft-sdlc.dev/get-started/install rather than reporting a
connection error.

# Reading Kraft

- `list_work_items(status)` - the board. `status="needs_human"` is what is
  waiting on a person: a row whose `pending_gate` is set needs an answer
  (`kraft:review` to decide, `kraft:gates` to record it), one without has
  stopped (`kraft:triage`). A sliced parent has no `pending_gate` of its own:
  its row lists its slices under `children`, and under `needs_human` only the
  ones that need a person, so call `get_work_item(<child id>)` for the gate.
  `status="paused"` is filed but not started, or paused by hand or by Kraft.
  `status="active"` is running a node now; `waiting` (on CI, a review or a
  merge) and `rate_limited` are in flight too. `status="blocked"` comes after
  an unfinished item, and `status="queued"` waits for a free slot. Rows span
  every connected repo. Without `status` it lists every item except archived and
  abandoned ones; ask `status="abandoned"` for the abandoned.
- `get_work_item()` - one item past its board row: status, current and next
  node, pending gate, stop reason, dependencies, not its chain. With no argument
  it resolves the item this session is standing in, which is correct when the
  cwd is a Kraft worktree. Its progress and a watch are `kraft:status`'s.
- `search(q)` - specs, plans and session summaries across every connected repo.
  A decision may already have been made and written down in another repo: that
  is the whole reason the index spans them. Each hit has `repo`, `kind`, `title`,
  a `snippet` and an `id`; `kraft view doc ID` prints the whole document. No hit
  isn't proof: try another wording before saying nothing was decided.
