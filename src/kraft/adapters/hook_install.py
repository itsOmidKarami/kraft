"""Put Kraft's permission hook where a CLI will run it (Kraft-4in7z).

Cursor reads hooks only from the project's `.cursor/hooks.json` (it ignores
them in CURSOR_CONFIG_DIR), so the entry goes into the worktree. It is kept
out of every commit: an untracked file through one marked line in the repo's
shared `info/exclude` (git reads no per-worktree exclude; a per-worktree
core.excludesFile would switch on extensions.worktreeConfig and shadow the
user's global excludes), a tracked one through skip-worktree in this
worktree's index. A repo's own hooks stay; Kraft's entry sits beside them.
"""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path

_REL = ".cursor/hooks.json"
_EXCLUDE_LINE = f"/{_REL}"
#: On its own line: gitignore has no trailing comments, a `# ...` after a
#: pattern would be part of it.
_EXCLUDE_NOTE = "# kraft: cursor permission hook (Kraft-4in7z)"
#: What marks an entry as Kraft's, whatever interpreter path or flag it carries.
_OURS = "kraft admin permission-hook"


def needs_hook(allowed_tools, deny_tools, grants) -> bool:
    """Policy the hook would enforce. `git-commit` alone needs none: the
    launch itself lets a worker commit (`writable_dirs`, attribution off)."""
    return allowed_tools is not None or bool(deny_tools) or any(g != "git-commit" for g in grants)


def hook_argv(harness_id: str, fail_closed: bool) -> list[str]:
    return [sys.executable, "-m", "kraft", "admin", "permission-hook", harness_id] + (
        ["--fail-closed"] if fail_closed else []
    )


def command_of(argv: list[str]) -> str:
    return shlex.join(argv)


def _git(worktree: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(worktree), *args], capture_output=True, text=True)


def install_cursor_hook(worktree: Path, argv: list[str] | None) -> None:
    """Make Kraft's preToolUse entry `argv`, or remove it when `argv` is None
    (a worktree launched again under a policy with nothing to enforce).
    Idempotent: a relaunch replaces Kraft's entry, never adds a second."""
    path = worktree / _REL
    if argv is None and not path.exists():
        return
    data = json.loads(path.read_text()) if path.exists() else {"version": 1, "hooks": {}}
    entries = data.setdefault("hooks", {}).setdefault("preToolUse", [])
    entries[:] = [e for e in entries if _OURS not in str(e.get("command", ""))]
    if argv is not None:
        entries.append({"command": command_of(argv), "timeout": 10})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")
    if _git(worktree, "ls-files", "--error-unmatch", _REL).returncode == 0:
        _git(worktree, "update-index", "--skip-worktree", _REL).check_returncode()
        return
    out = _git(worktree, "rev-parse", "--path-format=absolute", "--git-path", "info/exclude")
    out.check_returncode()
    exclude = Path(out.stdout.strip())
    exclude.parent.mkdir(parents=True, exist_ok=True)
    text = exclude.read_text() if exclude.exists() else ""
    if _EXCLUDE_LINE not in text.splitlines():
        sep = "\n" if text and not text.endswith("\n") else ""
        exclude.write_text(f"{text}{sep}{_EXCLUDE_NOTE}\n{_EXCLUDE_LINE}\n")
