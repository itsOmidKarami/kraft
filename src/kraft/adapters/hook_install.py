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
import os
import shlex
import shutil
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


class HookFileError(ValueError):
    """The worktree's hooks file is not one Kraft can add its entry to."""


def needs_hook(allowed_tools, deny_tools, grants) -> bool:
    """Policy the hook would enforce. `git-commit` alone needs none: the
    launch itself lets a worker commit (`writable_dirs`, attribution off)."""
    return allowed_tools is not None or bool(deny_tools) or any(g != "git-commit" for g in grants)


#: Set in a worker's env when its launch holds an allowlist: the hook reads
#: it, so fail-closed is per session while the entry itself is the same for
#: every launch in the worktree (Kraft-4in7z).
FAIL_CLOSED_ENV = "KRAFT_PERMISSION_FAIL_CLOSED"


def hook_argv(harness_id: str) -> list[str]:
    return [sys.executable, "-m", "kraft", "admin", "permission-hook", harness_id]


def command_of(argv: list[str]) -> str:
    return shlex.join(argv)


def _git(worktree: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(worktree), *args], capture_output=True, text=True)


def install_cursor_hook(worktree: Path, argv: list[str]) -> None:
    """Make Kraft's preToolUse entry `argv`. Never removed while the worktree
    lives: sibling launches share it, and one with nothing to enforce gets
    `no_opinion` from the gate. Idempotent: a relaunch replaces Kraft's
    entry, never adds a second."""
    path = worktree / _REL
    try:
        data = json.loads(path.read_text()) if path.exists() else {"version": 1, "hooks": {}}
        entries = data.setdefault("hooks", {}).setdefault("preToolUse", [])
        entries[:] = [e for e in entries if _OURS not in str(e.get("command", ""))]
    except (ValueError, AttributeError, TypeError) as exc:
        raise HookFileError(
            f"{path} is not a Cursor hooks file Kraft can add its permission hook to "
            f"({exc}); fix or remove it"
        ) from exc
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


# -- codex ---------------------------------------------------------------------
#
# Codex takes a hook per launch (`-c hooks.PreToolUse=...`) but skips it,
# silently, until it is trusted. Trust is per launch too: `-c hooks.state=`
# names the hook's key and hash, both read off `codex app-server`'s
# `hooks/list`. Never `--dangerously-bypass-hook-trust`: that trusts every
# hook a repo ships as well (probe, codex-cli 0.155.0, Kraft-4in7z.3).

CODEX_TRUST_TIMEOUT = 15.0
_codex_flags: dict[tuple, tuple[str, ...]] = {}


class CodexTrustError(ValueError):
    """Codex could not be made to trust Kraft's hook for this launch."""


def _toml(s: str) -> str:
    # A JSON string is a valid TOML basic string.
    return json.dumps(s)


async def _codex_hook(exe: list[str], flags: list[str], cwd: Path, command: str) -> dict:
    """Kraft's own entry in `codex app-server -c <flags>`'s `hooks/list`."""
    import asyncio

    proc = await asyncio.create_subprocess_exec(
        *exe,
        "app-server",
        *(a for f in flags for a in ("-c", f)),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        cwd=cwd,
    )
    msgs = [
        {
            "id": 1,
            "method": "initialize",
            "params": {"clientInfo": {"name": "kraft", "version": "1"}},
        },
        {"method": "initialized"},
        {"id": 2, "method": "hooks/list", "params": {"cwds": [str(cwd)]}},
    ]
    proc.stdin.write("".join(json.dumps(m) + "\n" for m in msgs).encode())

    async def listed() -> dict:
        while line := await proc.stdout.readline():
            msg = json.loads(line)
            if msg.get("id") == 2:
                if "error" in msg:
                    raise CodexTrustError(f"hooks/list failed: {msg['error']}")
                return msg["result"]
        raise CodexTrustError("codex app-server exited before answering hooks/list")

    try:
        result = await asyncio.wait_for(listed(), CODEX_TRUST_TIMEOUT)
    finally:
        if proc.returncode is None:
            proc.kill()
        await proc.wait()
    for group in result.get("data") or ():
        for hook in group.get("hooks") or ():
            if hook.get("source") == "sessionFlags" and hook.get("command") == command:
                return hook
    raise CodexTrustError("codex app-server did not list Kraft's preToolUse hook")


def _exe_identity(exe: list[str]) -> tuple:
    # A codex upgrade may hash hooks differently: a new binary is a new key.
    found = shutil.which(exe[0])
    real = os.path.realpath(found) if found else exe[0]
    return (real, os.stat(real).st_mtime_ns if found else None, *exe[1:])


async def codex_hook_flags(exe: list[str], argv: list[str], cwd: Path) -> tuple[str, ...]:
    """The `-c` flags that install Kraft's preToolUse hook in one codex
    launch, trusted: checked by listing them once more, since an untrusted
    hook is skipped without a word. Cached per codex binary and command.
    Raises `CodexTrustError` on anything else -- the caller refuses the
    launch rather than run it unenforced."""
    command = command_of(argv)
    hook = f'hooks.PreToolUse=[{{hooks=[{{type="command",command={_toml(command)},timeout=10}}]}}]'
    key = (_exe_identity(exe), hook)
    if key in _codex_flags:
        return _codex_flags[key]
    try:
        found = await _codex_hook(exe, [hook], cwd, command)
        state = (
            f"hooks.state={{{_toml(found['key'])}={{trusted_hash={_toml(found['currentHash'])}}}}}"
        )
        if (await _codex_hook(exe, [hook, state], cwd, command)).get("trustStatus") != "trusted":
            raise CodexTrustError("codex did not trust Kraft's hook with the hash it listed")
    except CodexTrustError:
        raise
    except TimeoutError as exc:
        raise CodexTrustError(
            f"`{shlex.join(exe)} app-server` gave no hooks/list within {CODEX_TRUST_TIMEOUT:g}s"
        ) from exc
    except (OSError, ValueError, KeyError) as exc:
        raise CodexTrustError(f"`{shlex.join(exe)} app-server`: {exc!r}") from exc
    _codex_flags[key] = flags = ("-c", hook, "-c", state)
    return flags
