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

from kraft.paths import BUNDLED

#: Where the source tree keeps the skills. Also one half of the published
#: marketplace: `plugins/` splits to a repo whose root holds `kraft/` and
#: `kraft-lite/`, so the plugin lives beside its sibling rather than inside the
#: Python package.
SOURCE_SKILLS = Path(__file__).resolve().parents[2] / "plugins" / "kraft" / "skills"


def skills_dir() -> Path:
    """The skills to install, bundled copy first.

    One skill per moment you would reach for Kraft, rather than one skill listing
    every tool. The manifest in `_plugin_manifest` turns the written directory
    into a namespace, so these are invoked as `/kraft:handoff`, `/kraft:board`,
    `/kraft:gates`, `/kraft:status`.

    Files rather than string literals because this same directory is published as
    a marketplace plugin, and the same content maintained in two places is the
    same content that starts disagreeing.

    An installed Kraft has no `plugins/` beside it, so `just bundle` copies these
    into `_bundled/plugin-skills` exactly as it copies the built SPA, and
    package-data ships them. The source fallback is for a checkout that has not
    run `just bundle` - under site-packages that path does not exist, which is
    what stops it resolving to something meaningless (paths.py:9).
    """
    bundled = BUNDLED / "plugin-skills"
    return bundled if bundled.is_dir() else SOURCE_SKILLS


def _plugin_manifest() -> dict:
    """`name` here is the slash-command prefix: these skills load as
    `/kraft:handoff` and friends because this file says `kraft`.

    A skills directory *without* this manifest still loads, but flat and
    unprefixed - the manifest is the whole difference between `/handoff` and
    `/kraft:handoff`.
    """
    try:
        version = metadata.version("kraft-sdlc")
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

    for source in sorted(skills_dir().glob("*/SKILL.md")):
        path = plugin_root / "skills" / source.parent.name / "SKILL.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source.read_text())
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
