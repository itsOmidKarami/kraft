# Use it from an agent session

Kraft can also be driven from a coding-agent session over MCP, so work can be
filed, read, and unblocked without switching to the browser.

```bash
kraft admin init          # register the MCP server for your user, install the skills
kraft admin init --repo   # or write .mcp.json + .claude/skills/ into this repo
```

User scope shells out to `claude mcp add` rather than editing `~/.claude.json`
itself — that file is large, shared, agent-owned state. If `claude` is not on
`PATH`, `kraft admin init` prints the command for you to run instead of guessing.

They also publish as a marketplace plugin, alongside Kraft Lite:
`/plugin marketplace add itsOmidKarami/kraft`.

The skills install as a plugin, so they namespace: `/kraft:handoff` to file work
after a spec and plan are agreed, `/kraft:board` to see what is running or
blocked, `/kraft:gates` to approve, reject, or steer. That is a plain directory
tree under `.claude/skills/kraft/` with a `.claude-plugin/plugin.json` — nothing
is registered in Claude Code's managed state, and uninstalling is `rm -rf` on the
directory. It works the same at user and repo scope.

The tools you'll reach for most, over the same local HTTP API the browser uses:

| | |
|---|---|
| read | `list_work_items`, `get_work_item`, `search` |
| write | `create_work_item`, `ensure_repo` |
| act | `approve_gate`, `reject_gate`, `pause_work_item`, `resume_work_item` |

## Two rules enforced in code, not in prose

**An agent cannot start work.** Everything an agent creates lands paused, and the
board shows it as *Waiting to start* with a single Start button. Nothing spends
tokens until a person clicks it.

**A worker cannot act on itself.** Sessions Kraft starts carry
`KRAFT_WORK_ITEM_ID`, and any attempt to approve, reject, pause, or resume the
work item running that session is refused before a request is sent. A gate is
where a human decides; an agent approving its own would make the gate decorative.

`kraft admin mcp` runs the server on stdio, and every tool is also a `kraft`
subcommand, so hooks and non-MCP agents get the same surface — see the
[CLI reference](cli.md).

## Without the orchestrator

`plugins/kraft-lite/` runs the same chain inside a single agent session — same
node list, same gates, same caps, no service. It is the attended half of Kraft:
one chain, in front of you, resumable across sessions but not outliving your
terminal. That directory is published as a standalone repo, so it must stay
self-contained: no import above `plugins/kraft-lite/`, no dependency beyond the
standard library.

See [`plugins/kraft-lite/README.md`](https://github.com/itsOmidKarami/kraft/blob/main/plugins/kraft-lite/README.md).
