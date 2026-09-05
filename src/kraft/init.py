"""`kraft init` — install the agent-facing surface at user or repo scope.

Design §8. User scope delegates MCP registration to the `claude` CLI:
`~/.claude.json` is large, shared, agent-owned user state, and hand-editing it is
how an installer corrupts somebody's whole configuration. The repo-scope
`.mcp.json` is written directly — small documented schema, and the file belongs
to the repo the human pointed Kraft at.

Nothing here touches `CLAUDE.md`, `AGENTS.md`, or any other ambient repo file
(§1.4 as amended in §8.1): every path written is one `kraft init` was asked for.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path

SKILL = """---
name: kraft
description: Use when work should be handed to Kraft rather than done in this
  session - filing a work item after a spec and plan are agreed, checking what
  Kraft is running, or connecting the current repo to Kraft.
---

# Kraft

Kraft runs semi-autonomous work items as chains, with human gates. This session
talks to it over MCP.

## Handing work off

After a spec and plan are agreed and the work is bigger than this session should
do inline, file it:

1. `ensure_repo()` - connects the current repo if Kraft has not seen it.
2. `create_work_item(title)` - files the work.

**`create_work_item` does not start anything.** The item lands paused and a human
starts it from the board. Say so when you report back; do not tell the user work
is underway.

## Reading

- `list_work_items(status)` - the board. `status="paused"` is what is waiting on a human.
- `get_work_item()` - the item this session is standing in, when the cwd is a Kraft worktree.
- `search(q)` - specs, plans, and session summaries across every connected repo.
  Worth a call before writing a spec, to find whether the decision was already made.

## Driving a stuck item

- `pause_work_item()` then `resume_work_item(steer="...")` - redirect work that
  is going wrong. There is no channel into a running agent, so this is what
  steering means.
- `approve_gate()` / `reject_gate(note="...")` - the human gates. **Ask the
  person before calling either.** A gate is where a human decides; if you are a
  Kraft worker session, you cannot act on your own item at all.
"""


def _write_skill(root: Path) -> str:
    path = root / "skills" / "kraft" / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(SKILL)
    return str(path)


def _write_repo_mcp_json(cwd: Path) -> str:
    """Merge into any existing .mcp.json — other servers are not ours to drop."""
    path = cwd / ".mcp.json"
    try:
        config = json.loads(path.read_text())
    except OSError, ValueError:
        config = {}
    config.setdefault("mcpServers", {})["kraft"] = {"command": "kraft", "args": ["mcp"]}
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
        return [_write_repo_mcp_json(cwd), _write_skill(cwd / ".claude")]

    command = ["claude", "mcp", "add", "--scope", "user", "kraft", "--", "kraft", "mcp"]
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
    return [_write_skill(Path.home() / ".claude")]
