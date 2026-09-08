"""`kraft admin init` — install the agent-facing surface at user or repo scope.

Design §8. User scope delegates MCP registration to the `claude` CLI:
`~/.claude.json` is large, shared, agent-owned user state, and hand-editing it is
how an installer corrupts somebody's whole configuration. The repo-scope
`.mcp.json` is written directly — small documented schema, and the file belongs
to the repo the human pointed Kraft at.

Nothing here touches `CLAUDE.md`, `AGENTS.md`, or any other ambient repo file
(§1.4 as amended in §8.1): every path written is one `kraft admin init` was asked for.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from importlib import metadata
from pathlib import Path

#: One skill per moment you would reach for Kraft, rather than one skill listing
#: every tool. The manifest below turns this directory into a namespace, so these
#: are invoked as `/kraft:handoff`, `/kraft:board`, `/kraft:gates`.
SKILLS = {
    "handoff": """---
name: handoff
description: Use when work agreed in this session should be handed to Kraft instead of
  done here - after a spec and plan are settled, when the task is too big for this
  session, or when the current repo needs connecting to Kraft first.
---

# Handing work to Kraft

Kraft runs semi-autonomous work items as chains, with human gates. Hand work off
rather than doing it inline when it is large enough to want that structure.

1. `ensure_repo()` - connects the current repo if Kraft has not seen it.
   Idempotent, so call it every time rather than checking first.
2. `create_work_item(title)` - files the work.

**`create_work_item` does not start anything.** The item lands paused and a
person starts it from the board. When you report back, say the work is *filed*,
not that it is underway - telling someone their work is running when nothing is
running is the one failure this whole surface is built to avoid.

## When the spec and plan already exist

If documents were written in this session or already live in the repo, attach
them at intake instead of letting Kraft re-run those phases. Attaching a spec or
plan trims the node whose gate it satisfies, so the person is not asked to
re-approve what they just agreed with you, and the implementing agent is told to
follow the documents rather than guess from the title.
""",
    "board": """---
name: board
description: Use when you need to know what Kraft is doing - what work is running, what
  is blocked or waiting on a person, the state of one work item, or whether a decision
  was already made in a spec or plan somewhere across the repos.
---

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
""",
    "gates": """---
name: gates
description: Use when a Kraft work item needs a human decision or has gone wrong -
  approving or rejecting the gate it is waiting on, or pausing and resuming work that
  is heading in the wrong direction.
---

# Gates and steering

## Gates

- `approve_gate()` - let the chain continue past the gate it is waiting on.
- `reject_gate(note="...")` - send it back to be re-planned. The note is
  required, because a rejection with no reason strands whoever picks the work up
  next.

**Ask the person before calling either.** A gate exists precisely because this is
a decision a human makes. Read them the diff or the plan, get an answer, then act
on it. Approving a gate because it seemed obvious is how the gate stops meaning
anything.

## Steering

There is no channel into a running agent, so redirecting work means stopping it
and starting it again with new context:

- `pause_work_item()` - stop the current attempt.
- `resume_work_item(steer="...")` - start again, with the steer leading the next
  attempt's prompt.

`resume_work_item()` is also how a freshly filed work item is started for the
first time.

## If you are a Kraft worker session

You cannot act on the work item that is running you - approve, reject, pause and
resume against your own item are all refused. Report what you found and let the
person decide.
""",
    "status": r"""---
name: status
description: Use when someone needs to know where a Kraft work item has got to, or
  when a handed-off item should be watched until it finishes - the phase it is in, the
  node coming next, any gate it is waiting on, and a follow that ends by itself.
---

# Where a Kraft work item has got to

## The phase line

```bash
kraft show ID --json | jq -r '
  "\(.status): \(.current_node_id) → next \(.next_node_id // "done")" +
  (if .pending_gate then " · gate \(.pending_gate)" else "" end)
'
```

One line: the status, the node running now, the node coming next, and the gate
it is waiting on if there is one. `next_node_id` is null on the last node of the
chain, which renders as `done`; on an item nobody has started yet it is node
zero, because that is what starting it will run.

Drop the id to ask about the work item you are standing in: `kraft view show --json`
resolves it from the worktree.

## Watching it until it ends

Arm a monitor on:

```bash
kraft events ID -f
```

Do not pass `--type`. `-f` ends itself on `work_item_completed` or
`work_item_abandoned`, so the monitor disarms on its own; a type filter would go
silent through exactly the escalation the person needs to hear about. A chain
emits tens of events over its life, not thousands.

`--json` makes that stream NDJSON, one object per line.

## What to report

Say the phase and the next node. If a gate is pending, say which one and that it
is waiting on a person - a work item sitting at a gate is not stuck, and calling
it stuck sends someone looking for a fault that is not there.
""",
}


def _plugin_manifest() -> dict:
    """`name` here is the slash-command prefix: these skills load as
    `/kraft:handoff` and friends because this file says `kraft`.

    A skills directory *without* this manifest still loads, but flat and
    unprefixed - the manifest is the whole difference between `/handoff` and
    `/kraft:handoff`.
    """
    try:
        version = metadata.version("kraft")
    except metadata.PackageNotFoundError:
        # Running from a source tree that was never installed.
        version = "0.0.0"
    return {
        "$schema": "https://anthropic.com/claude-code/plugin.schema.json",
        "name": "kraft",
        "version": version,
        "description": (
            "Drive Kraft from an agent session: file work, read the board, act on gates."
        ),
        "skills": ["./"],
    }


def _write_plugin(root: Path) -> list[str]:
    """Write the plugin tree under `<root>/skills/kraft/`. Returns paths written.

    `~/.claude` and `<repo>/.claude` behave identically: a directory under either
    carrying a `.claude-plugin/` manifest auto-loads as `kraft@skills-dir`.
    """
    plugin_root = root / "skills" / "kraft"
    manifest = plugin_root / ".claude-plugin" / "plugin.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps(_plugin_manifest(), indent=2) + "\n")
    written = [str(manifest)]

    for name, body in SKILLS.items():
        path = plugin_root / "skills" / name / "SKILL.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
        written.append(str(path))

    # Earlier versions wrote one flat SKILL.md here. Left behind it lingers as a
    # second, unprefixed `/kraft` beside the namespaced ones.
    (plugin_root / "SKILL.md").unlink(missing_ok=True)
    return written


def _write_repo_mcp_json(cwd: Path) -> str:
    """Merge into any existing .mcp.json — other servers are not ours to drop."""
    path = cwd / ".mcp.json"
    try:
        config = json.loads(path.read_text())
    except OSError, ValueError:
        config = {}
    config.setdefault("mcpServers", {})["kraft"] = {"command": "kraft", "args": ["admin", "mcp"]}
    path.write_text(json.dumps(config, indent=2) + "\n")
    return str(path)


def install(
    repo_scope: bool,
    cwd: Path | None = None,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> list[str]:
    """Install the Kraft agent surface. Returns the paths written."""
    cwd = Path(cwd or Path.cwd())
    if repo_scope:
        return [_write_repo_mcp_json(cwd), *_write_plugin(cwd / ".claude")]

    command = ["claude", "mcp", "add", "--scope", "user", "kraft", "--", "kraft", "admin", "mcp"]
    try:
        result = run(command, capture_output=True, text=True)
    except FileNotFoundError:
        result = None
    if result is None or result.returncode != 0:
        # A missing agent CLI is a thing to report, not to work around by
        # rewriting user state we do not own.
        raise SystemExit(
            "kraft init: could not register the MCP server automatically. Run this yourself:\n"
            f"  {' '.join(command)}"
        )
    return _write_plugin(Path.home() / ".claude")
