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

import contextlib
import json
import os
import secrets
import shlex
import shutil
import stat
import subprocess
import sys
from pathlib import Path

from kraft.worker import shim as _shim

_REL = ".cursor/hooks.json"
_EXCLUDE_LINE = f"/{_REL}"
#: On its own line: gitignore has no trailing comments, a `# ...` after a
#: pattern would be part of it.
_EXCLUDE_NOTE = "# kraft: cursor permission hook"
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


def hook_argv(harness_id: str, sandbox: dict | None = None) -> list[str]:
    """The hook's command. In a sandbox under `network:`, the `kraft` shim at
    its fixed container path, answered by the daemon through the session's
    channel: never the host's interpreter, which the container has not got."""
    if sandbox and sandbox.get("network"):
        return [f"{_shim.CONTAINER_DIR}/kraft", "admin", "permission-hook", harness_id]
    return [sys.executable, "-m", "kraft", "admin", "permission-hook", harness_id]


def command_of(argv: list[str]) -> str:
    return shlex.join(argv)


def _git(worktree: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(worktree), *args], capture_output=True, text=True)


def _write_cursor_hook(dfd: int, path: Path, argv: list[str]) -> None:
    """Kraft's entry into `hooks.json` under the open `.cursor` directory,
    never through a symlink and never blocking on what a worker planted:
    opened O_NONBLOCK|O_NOFOLLOW and read only if it is a regular file (a
    FIFO opened blocking waits for a writer forever, and the daemon's loop
    with it), replaced by a rename of a fresh temp file."""
    name = path.name
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dfd)
    except FileNotFoundError:
        data = {"version": 1, "hooks": {}}
    except OSError as exc:
        raise HookFileError(
            f"{path} is not a file Kraft can read (a symlink, or {exc.strerror}); remove it"
        ) from exc
    else:
        with os.fdopen(fd, "rb") as f:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise HookFileError(f"{path} is not a regular file; remove it")
            os.set_blocking(fd, True)
            text = f.read().decode(errors="replace")
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise _unusable(path, exc) from exc
    try:
        entries = data.setdefault("hooks", {}).setdefault("preToolUse", [])
        entries[:] = [e for e in entries if _OURS not in str(e.get("command", ""))]
    except (AttributeError, TypeError) as exc:
        raise _unusable(path, exc) from exc
    # Cursor allows the call when a hook crashes, times out or prints nothing,
    # unless the entry says otherwise: Kraft's gate must deny instead.
    entries.append({"command": command_of(argv), "timeout": 10, "failClosed": True})
    # Random and exclusive: nothing planted under the name is opened.
    tmp = f".{name}.{secrets.token_hex(8)}.tmp"
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        with os.fdopen(os.open(tmp, flags, 0o644, dir_fd=dfd), "w") as f:
            f.write(json.dumps(data, indent=2) + "\n")
        # A rename replaces a symlink planted since, never writes through it.
        os.replace(tmp, name, src_dir_fd=dfd, dst_dir_fd=dfd)
    except OSError as exc:
        with contextlib.suppress(OSError):
            os.unlink(tmp, dir_fd=dfd)
        raise HookFileError(f"could not write {path}: {exc}") from exc


def _unusable(path: Path, exc: Exception) -> HookFileError:
    return HookFileError(
        f"{path} is not a Cursor hooks file Kraft can add its permission hook to "
        f"({exc}); fix or remove it"
    )


def install_cursor_hook(worktree: Path, argv: list[str]) -> None:
    """Make Kraft's preToolUse entry `argv`. Never removed while the worktree
    lives: sibling launches share it, and one with nothing to enforce gets
    `no_opinion` from the gate. Idempotent: a relaunch replaces Kraft's
    entry, never adds a second."""
    path = worktree / _REL
    # A sandboxed worker writes this worktree: a symlink it planted at
    # `.cursor` or the file would have the host write wherever it points.
    # Every step below goes through a directory fd opened without following.
    try:
        (worktree / ".cursor").mkdir(exist_ok=True)
        dfd = os.open(
            worktree / ".cursor", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK
        )
    except OSError as exc:
        raise HookFileError(
            f"{worktree / '.cursor'} is not a directory Kraft can write its permission hook "
            f"into (a symlink, or {exc.strerror}); remove it"
        ) from exc
    try:
        _write_cursor_hook(dfd, path, argv)
    finally:
        os.close(dfd)
    if _git(worktree, "ls-files", "--error-unmatch", _REL).returncode == 0:
        _git(worktree, "update-index", "--skip-worktree", _REL).check_returncode()
        return
    out = _git(worktree, "rev-parse", "--path-format=absolute", "--git-path", "info/exclude")
    out.check_returncode()
    exclude = Path(out.stdout.strip())
    exclude.parent.mkdir(parents=True, exist_ok=True)
    text = exclude.read_text() if exclude.exists() else ""
    lines = text.splitlines()
    if _EXCLUDE_LINE in lines:
        # Kraft 1.1 through the 1.5.0 release candidates wrote the note with a
        # tracker id after it.
        legacy = [n for n, line in enumerate(lines) if line.startswith(f"{_EXCLUDE_NOTE} (")]
        for n in legacy:
            lines[n] = _EXCLUDE_NOTE
        if legacy:
            exclude.write_text("\n".join(lines) + "\n")
        return
    sep = "\n" if text and not text.endswith("\n") else ""
    exclude.write_text(f"{text}{sep}{_EXCLUDE_NOTE}\n{_EXCLUDE_LINE}\n")


# -- codex ---------------------------------------------------------------------
#
# Codex takes a hook per launch (`-c hooks.PreToolUse=...`) but skips it,
# silently, until it is trusted. Trust is per launch too: `-c hooks.state=`
# names the hook's key and hash, both read off `codex app-server`'s
# `hooks/list`. Never `--dangerously-bypass-hook-trust`: that trusts every
# hook a repo ships as well (probe, codex-cli 0.155.0, Kraft-4in7z.3).
#
# Skipped means the call runs: `codex exec` with an untrusted hook, or one
# whose trusted_hash does not match, ran the tool call, never ran the hook
# and printed nothing (measured, 0.155.0, a fake model server). So the hash
# is always asked of the binary the session runs: a sandboxed session's is
# the image's, asked in a container (`runner`), never the host's.

CODEX_TRUST_TIMEOUT = 15.0
#: `features.hooks = false` in a lower config layer lists no hook at all.
_HOOKS_ON = "features.hooks=true"
_codex_flags: dict[tuple, tuple[str, ...]] = {}


class CodexTrustError(ValueError):
    """Codex could not be made to trust Kraft's hook for this launch."""


def _toml(s: str) -> str:
    # A JSON string is a valid TOML basic string.
    return json.dumps(s)


async def _codex_hook(
    exe: list[str], flags: list[str], cwd: Path, command: str, runner: list[str], sandboxed: bool
) -> dict:
    """Kraft's own entry in `codex app-server -c <flags>`'s `hooks/list`."""
    import asyncio

    proc = await asyncio.create_subprocess_exec(
        *runner,
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
    hooks = [h for group in result.get("data") or () for h in group.get("hooks") or ()]
    ours = next(
        (h for h in hooks if h.get("source") == "sessionFlags" and h.get("command") == command),
        None,
    )
    if ours is None:
        raise CodexTrustError("codex app-server did not list Kraft's preToolUse hook")
    # A worker can plant a PreToolUse hook of its own, and its trusted_hash,
    # in the config under its HOME (measured, 0.155.0: listed enabled and
    # trusted beside Kraft's). Whether it could outvote Kraft's deny is not
    # found out: any other that would run refuses a sandboxed launch. A host
    # operator's own ~/.codex hooks are theirs (a separate bead).
    for h in hooks if sandboxed else ():
        if (
            h is not ours
            and h.get("eventName") == "preToolUse"
            and h.get("enabled") is not False
            and h.get("trustStatus") != "untrusted"
        ):
            raise CodexTrustError(
                f"another preToolUse hook would run beside Kraft's: {h.get('source')} "
                f"{h.get('key')} ({h.get('command')!r}); remove it"
            )
    return ours


def _exe_identity(exe: list[str]) -> tuple:
    # A codex upgrade may hash hooks differently: a new binary is a new key.
    found = shutil.which(exe[0])
    real = os.path.realpath(found) if found else exe[0]
    return (real, os.stat(real).st_mtime_ns if found else None, *exe[1:])


async def codex_hook_flags(
    exe: list[str], argv: list[str], cwd: Path, runner=None
) -> tuple[str, ...]:
    """The `-c` flags that install Kraft's preToolUse hook in one codex
    launch, trusted: checked by listing them once more, since an untrusted
    hook is skipped without a word. `runner` runs the session's own codex (a
    sandbox backend's `oneshot`: `.argv()` prefixes each run of `exe`,
    `.close()` after); None is the host's, cached per codex binary and
    command. Raises `CodexTrustError` on anything else -- the caller refuses
    the launch rather than run it unenforced."""
    command = command_of(argv)
    hook = f'hooks.PreToolUse=[{{hooks=[{{type="command",command={_toml(command)},timeout=10}}]}}]'
    # ponytail: an image's codex is asked on every launch (two short
    # containers): its tag can be rebuilt with another codex under it, and a
    # stale hash is a silently skipped hook. Key on the image id if it shows.
    key = None if runner is not None else (_exe_identity(exe), hook)
    if key in _codex_flags:
        return _codex_flags[key]

    def prefix() -> list[str]:
        return runner.argv() if runner is not None else []

    try:
        sandboxed = runner is not None
        found = await _codex_hook(exe, [hook, _HOOKS_ON], cwd, command, prefix(), sandboxed)
        # `enabled` too: a `[hooks.state."<key>"] enabled = false` in the
        # config under a worker-writable HOME is a lower layer than `-c`,
        # and would switch the hook off with its trust intact (measured).
        state = (
            f"hooks.state={{{_toml(found['key'])}="
            f"{{trusted_hash={_toml(found['currentHash'])},enabled=true}}}}"
        )
        listed = await _codex_hook(exe, [hook, _HOOKS_ON, state], cwd, command, prefix(), sandboxed)
        if listed.get("trustStatus") != "trusted":
            raise CodexTrustError("codex did not trust Kraft's hook with the hash it listed")
        if listed.get("enabled") is not True:
            raise CodexTrustError("codex listed Kraft's hook as not enabled")
    except CodexTrustError:
        raise
    except TimeoutError as exc:
        raise CodexTrustError(
            f"`{shlex.join(exe)} app-server` gave no hooks/list within {CODEX_TRUST_TIMEOUT:g}s"
        ) from exc
    except (OSError, ValueError, KeyError) as exc:
        raise CodexTrustError(f"`{shlex.join(exe)} app-server`: {exc!r}") from exc
    finally:
        if runner is not None:
            await runner.close()
    # The launch carries exactly the flags checked: a sibling session editing
    # the shared HOME after the check cannot turn the hook off.
    flags = ("-c", hook, "-c", _HOOKS_ON, "-c", state)
    if key is not None:
        _codex_flags[key] = flags
    return flags
