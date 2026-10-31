"""Read an API account's token from its source: git's credential store, a command, an
environment variable taken at startup, or a file. Never interactively, and no error
here quotes what a source printed."""

from __future__ import annotations

import asyncio
import base64
import errno
import os
import re
import shlex
import signal
import stat
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from kraft import redact
from kraft.config import ConfigError, RepoEntry
from kraft.forges import ForgeAccount, ForgesFile, env_name_problem

CREDENTIAL_TIMEOUT_S = 10.0


@dataclass(frozen=True)
class Kind:
    name: str
    header: str
    git_username: str
    gitlab_scopes: tuple[str, ...]


KINDS: dict[str, Kind] = {
    "pat": Kind("pat", "Bearer", "oauth2", ("pat",)),
    "oauth": Kind("oauth", "Bearer", "oauth2", ("oauth",)),
    "unknown": Kind("unknown", "Bearer", "oauth2", ("pat", "oauth")),
}

PREFIXES = {"ghp_": "pat", "github_pat_": "pat", "gho_": "oauth", "ghu_": "oauth", "glpat-": "pat"}

# Taken from the server's environment at startup, so a worker's env cannot supply it.
_TAKEN: dict[str, str] = {}


# Token files, kept from every sandbox's view (set at load and on reload).
_SECRET_FILES: tuple[Path, ...] = ()
_ENV_NAME = re.compile(r"\benv:\s*[\"']?([A-Za-z_]\w*)")
_FILE_PATH = re.compile(r"\bfile:\s*[\"']?([^\"'\s,}]+)")


class NoToken(Exception):
    """Fixed operator text; never a source's output."""


def prefix_warning(token: str, kind: str) -> str | None:
    if kind == "unknown":
        return None
    for prefix, actual in PREFIXES.items():
        if token.startswith(prefix):
            if actual != kind:
                return f"the token starts with {prefix}, a {actual} token, but kind says {kind}"
            return None
    return None


def basic_form(token: str, kind: str) -> str:
    return base64.b64encode(f"{KINDS[kind].git_username}:{token}".encode()).decode()


def neutral_run(
    argv: list[str], *, input: str | None = None, timeout: float
) -> subprocess.CompletedProcess[str]:
    """Run `argv` in an empty directory, outside the repo, with every prompt turned off, in
    its own session so a timeout kills what a credential helper started too."""
    with tempfile.TemporaryDirectory(prefix="kraft-cred-") as cwd:
        env = {
            **os.environ,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_ASKPASS": "",
            "SSH_ASKPASS": "",
            "GCM_INTERACTIVE": "never",
            "GIT_CEILING_DIRECTORIES": str(Path(cwd).parent),
        }
        proc = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            out, err = proc.communicate(input, timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            # A descendant that left the session may still hold the pipes.
            try:
                proc.communicate(timeout=1)
            except subprocess.TimeoutExpired:
                for pipe in (proc.stdin, proc.stdout, proc.stderr):
                    if pipe:
                        pipe.close()
            try:
                proc.wait(timeout=1)  # reap it
            except subprocess.TimeoutExpired:
                pass
            raise
        return subprocess.CompletedProcess(argv, proc.returncode, out, err)


def _timed_out(what: str) -> NoToken:
    wait = (
        "it may be waiting for you to allow Keychain access for git"
        if sys.platform == "darwin"
        else "a credential helper may be waiting for input"
    )
    return NoToken(f"{what} did not answer in {CREDENTIAL_TIMEOUT_S:g}s: {wait}")


async def _run(what: str, argv: list[str], input: str | None = None):
    try:
        return await asyncio.to_thread(neutral_run, argv, input=input, timeout=CREDENTIAL_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise _timed_out(what) from None
    except OSError as exc:
        raise NoToken(f"{what} could not run ({type(exc).__name__})") from None


async def git_credential(web_url: str) -> str | None:
    """The password git has saved for the host, or None when it has none."""
    netloc = urlsplit(web_url).netloc
    done = await _run(
        "git credential fill",
        ["git", "credential", "fill"],
        f"protocol=https\nhost={netloc}\n\n",
    )
    # With prompts off, real git says "terminal prompts disabled" and exits 128
    # when it has nothing saved; only that fixed phrase is read from stderr.
    if done.returncode == 128 and "terminal prompts disabled" in done.stderr:
        return None
    if done.returncode != 0:
        raise NoToken(f"git credential fill exited {done.returncode}; its output is not shown")
    return next(
        (
            line[len("password=") :]
            for line in done.stdout.splitlines()
            if line.startswith("password=")
        ),
        None,
    )


async def run_command(command: str) -> str:
    try:
        argv = shlex.split(command)
    except ValueError:
        raise NoToken("the token command is not valid shell-style quoting") from None
    if not argv:
        raise NoToken("the token command is empty")
    done = await _run("the token command", argv)
    if done.returncode != 0:
        raise NoToken(f"command exited {done.returncode}; its output is not shown")
    lines = done.stdout.strip().splitlines()
    if not lines:
        raise NoToken("the command printed nothing")
    if len(lines) > 1:
        raise NoToken("the command printed more than one line; its output is not shown")
    return lines[0].strip()


def read_file(
    spec: str, *, repos: Sequence[RepoEntry], worktrees: Path, expose: Sequence[Path]
) -> str:
    # Inside: host_view imports credentials.
    from kraft.worker.host_view import system_dirs

    expanded = os.path.expandvars(os.path.expanduser(spec))
    if unset := re.findall(r"\$\{?(\w+)", expanded):
        raise NoToken(f"the token file path names ${unset[0]}, a variable that is not set")
    if "$" in expanded:
        raise NoToken("the token file path names a variable that is not set")
    path = Path(expanded)
    # Where a worker, a sandbox or the system could read it.
    real = Path(os.path.realpath(path))
    roots = [
        *(Path(r.path).expanduser() for r in repos),
        worktrees,
        *expose,
        Path("/etc"),
        *system_dirs(),
    ]
    for root in roots:
        for form in (root, Path(os.path.realpath(root))):
            if path == form or path.is_relative_to(form) or real.is_relative_to(form):
                raise NoToken(f"the token file is inside {form}, which a worker can read")
    # One descriptor for the checks and the read: no swap in between.
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise NoToken("the token file is a symlink; name the file itself") from None
        raise NoToken("the token file does not exist or cannot be read") from None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise NoToken("the token file is not a regular file")
        if st.st_uid != os.getuid():
            raise NoToken("the token file is owned by another user")
        if st.st_mode & 0o077:
            raise NoToken("the token file is readable by group or others; chmod 600 it")
        with os.fdopen(fd, encoding="utf-8") as f:
            fd = -1
            return f.read().strip()
    except (OSError, UnicodeDecodeError):
        raise NoToken("the token file cannot be read") from None
    finally:
        if fd >= 0:
            os.close(fd)


def env_token(name: str) -> str:
    try:
        return _TAKEN[name]
    except KeyError:
        raise NoToken(
            f"{name} was not set in the server's environment when it started; "
            "set it there and restart"
        ) from None


async def read_token(
    account: ForgeAccount,
    *,
    repos: Sequence[RepoEntry],
    worktrees: Path,
    expose: Sequence[Path] = (),
    ssh_hint: bool = False,
) -> str:
    source = account.source()
    if source.from_ == "git-credential":
        token = await git_credential(account.web_url)
        if token is None:
            host = urlsplit(account.web_url).netloc
            hint = "; your remote is SSH, so git never saved one" if ssh_hint else ""
            raise NoToken(f"git has no saved credential for {host}{hint}")
    elif source.from_ == "command":
        token = await run_command(source.command or "")
    elif source.from_ == "env":
        token = env_token(source.env or "")
    else:
        token = read_file(source.file or "", repos=repos, worktrees=worktrees, expose=expose)
    if len(token) < redact.MIN_LEN:
        raise NoToken(f"the token is shorter than {redact.MIN_LEN} characters")
    redact.register(token)
    redact.register(basic_form(token, source.kind))
    return token


def take_env(names: Iterable[str]) -> list[str]:
    """Move each variable out of the process environment, so no child inherits it."""
    taken = []
    for name in names:
        if name in os.environ:
            _TAKEN[name] = os.environ.pop(name)
            taken.append(name)
    return taken


def _scan(path: Path, pattern: re.Pattern[str]) -> list[str]:
    try:
        return pattern.findall(path.read_text())
    except (OSError, UnicodeDecodeError):
        return []


def env_source_names(forges_yaml: Path) -> list[str]:
    """Every `env` source's variable. A file that does not load is scanned as text: a
    token must leave even when the file is broken."""
    try:
        file = ForgesFile.load(forges_yaml)
        names = [a.token.env for a in file.accounts.values() if a.token and a.token.env]
    except ConfigError:
        names = _scan(forges_yaml, _ENV_NAME)
    return [n for n in dict.fromkeys(names) if env_name_problem(n) is None]


def strip_env_sources(forges_yaml: Path) -> list[str]:
    """Take them before anything can start a child."""
    return take_env(env_source_names(forges_yaml))


def taken_env(names: Iterable[str]) -> dict[str, str]:
    """For `spawn_restart` only: the restarted server must find them again. Only those
    the config still names, or the new server (which strips only those) would leak one."""
    return {n: _TAKEN[n] for n in names if n in _TAKEN}


def _expand(spec: str) -> Path:
    return Path(os.path.realpath(os.path.expandvars(os.path.expanduser(spec))))


def set_secret_files(forges_yaml: Path) -> None:
    """Token files stay out of every sandbox. As with env sources, a file that does not
    load is scanned as text."""
    global _SECRET_FILES
    try:
        file = ForgesFile.load(forges_yaml)
        specs = [a.token.file for a in file.accounts.values() if a.token and a.token.file]
    except ConfigError:
        specs = _scan(forges_yaml, _FILE_PATH)
    _SECRET_FILES = tuple(_expand(s) for s in specs)


def secret_files() -> tuple[Path, ...]:
    return _SECRET_FILES
