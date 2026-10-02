"""The suite's guard against running a real agent or forge CLI.

Two layers, installed per test by `tests/conftest.py`'s `_no_real_agent_binary`:

- **A stub directory first on `PATH`**, one refusing script per name in
  `GUARDED_BINARIES`. Anything that resolves one of those names through
  `PATH` -- `subprocess`, `asyncio.create_subprocess_exec`, `os.system`,
  `sh -c "claude ..."`, `env claude`, a `python -m kraft` child (which
  inherits the variable, and `support.server.child_env` puts it back when a
  test overrides it) -- runs the stub instead. The stub appends its name, cwd
  and argv to this process's log, prints `MESSAGE` and exits 127; the fixture
  fails the test at teardown with every call the log gained while it ran,
  because Kraft degrades on a failed CLI and the exit code alone could go
  unseen.
- **`subprocess.Popen` itself**, for a launch that never asks `PATH`: an
  absolute path to an installed real binary, or an `env=` whose own `PATH`
  leaves the stubs out. It compares the resolved executable's real path
  against the real binaries this machine has, refuses with an
  `AssertionError` and records the call for the same teardown check.
  `asyncio` subprocesses go through `Popen` too.

A guarded name an `e2e(...)` marker lists is left out of both, so that test
gets the real binary back. A fixture agent never matches: it is called by
its own path (`fixtures/fake-claude.sh`, `tests/support/fake_agent.py`) or put
first on `PATH` by the test, ahead of the stubs.
"""

from __future__ import annotations

import atexit
import inspect
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from support.harness import REAL_AGENT_BINARIES

#: The forge CLIs `kraft.adapters.forge` shells out to.
FORGE_BINARIES = frozenset({"gh", "glab"})

#: Every name the stub directory shadows.
GUARDED_BINARIES = REAL_AGENT_BINARIES | FORGE_BINARIES

#: What a stub prints to stderr, so a log or a child's output says why.
MESSAGE = (
    "refused by the test suite's real-binary guard (tests/support/real_binaries.py): "
    "a unit test must not run the real `{name}`. Point it at a fixture, or mark the "
    'test e2e("{name}").'
)

#: The guard of the test running now, None between tests and when a test opted
#: out (`real_executor`). `support.server.child_env` reads it.
ACTIVE: Guard | None = None

_root: Path | None = None
_dirs: dict[frozenset[str], Path] = {}
_installed_cache: dict[tuple[str, frozenset[str]], dict[str, str]] = {}


def _root_dir() -> Path:
    global _root
    if _root is None:
        _root = Path(tempfile.mkdtemp(prefix="kraft-guard-"))
        atexit.register(shutil.rmtree, _root, ignore_errors=True)
    return _root


def log_path() -> Path:
    """The file every stub in this process appends to."""
    return _root_dir() / "calls.log"


def write_stub(path: Path, name: str, log: str) -> None:
    """A `name` that logs `name<TAB>cwd<TAB>argv` to `log` (a shell word, so it
    may expand a variable), prints `MESSAGE` and exits 127."""
    path.write_text(
        "#!/bin/sh\n"
        f'printf \'%s\\t%s\\t%s\\n\' {shlex.quote(name)} "$PWD" "$*" >> {log}\n'
        f"echo {shlex.quote(MESSAGE.format(name=name))} >&2\n"
        "exit 127\n"
    )
    path.chmod(0o755)


def stub_dir(names: frozenset[str] = GUARDED_BINARIES) -> Path:
    """A directory holding a refusing stub for each of `names`, built once per
    process for each distinct set."""
    names = frozenset(names)
    if names not in _dirs:
        d = _root_dir() / f"bin-{len(_dirs)}"
        d.mkdir()
        for name in sorted(names):
            write_stub(d / name, name, shlex.quote(str(log_path())))
        _dirs[names] = d
    return _dirs[names]


def shadowed_for(e2e_names: set[str] | frozenset[str]) -> frozenset[str]:
    """The guarded names a test marked `e2e(*e2e_names)` does not get back."""
    return GUARDED_BINARIES - frozenset(e2e_names)


def _without_stubs(path: str) -> str:
    stubs = {str(d) for d in _dirs.values()}
    return os.pathsep.join(p for p in path.split(os.pathsep) if p not in stubs)


def installed(names: frozenset[str], path: str) -> dict[str, str]:
    """`{real path: name}` for each of `names` that `path` finds."""
    key = (path, names)
    if key not in _installed_cache:
        found = {}
        for name in names:
            exe = shutil.which(name, path=path)
            if exe:
                found[os.path.realpath(exe)] = name
        _installed_cache[key] = found
    return _installed_cache[key]


def _log_size() -> int:
    try:
        return log_path().stat().st_size
    except FileNotFoundError:
        return 0


_POPEN_SIGNATURE = inspect.signature(subprocess.Popen.__init__)


class Guard:
    """One test's view of the guard: `calls()` so far, `take()` to read and
    forget them (a test proving the guard fires), `check()` at teardown."""

    def __init__(self, nodeid: str, stubs: Path, real: dict[str, str]):
        self.nodeid = nodeid
        self.stubs = stubs
        #: `{real path: name}`: what `Popen` refuses to start.
        self.real = dict(real)
        self._refused: list[str] = []
        self._offset = _log_size()

    def _logged(self) -> list[str]:
        try:
            with log_path().open("rb") as f:
                f.seek(self._offset)
                return f.read().decode(errors="replace").splitlines()
        except FileNotFoundError:
            return []

    def calls(self) -> list[str]:
        return [*self._refused, *self._logged()]

    def take(self) -> list[str]:
        calls = self.calls()
        self._refused.clear()
        self._offset = _log_size()
        return calls

    def check(self) -> None:
        calls = self.take()
        if calls:
            raise AssertionError(
                f"{self.nodeid} ran a real agent or forge CLI, refused by the guard "
                f"(tests/support/real_binaries.py): {calls!r}. Point it at a fixture "
                f"agent or a stub of its own first on PATH, or mark the test "
                f'e2e("<cli>") if it means to.'
            )

    def refused_program(self, arguments: dict) -> str | None:
        """The guarded name a `Popen(**arguments)` would start, else None. A
        shell command line is the shell's to resolve, through the stubs."""
        if arguments.get("shell"):
            return None
        args = arguments.get("args")
        program = arguments.get("executable")
        if program is None:
            if isinstance(args, str | bytes | os.PathLike):
                program = args
            elif args:
                program = args[0]
        if program is None:
            return None
        program = os.fsdecode(program)
        if os.sep in program:
            candidate = os.path.join(os.fsdecode(arguments.get("cwd") or os.getcwd()), program)
        else:
            env = arguments.get("env")
            search = (env if env is not None else os.environ).get("PATH", os.defpath)
            candidate = shutil.which(program, path=search)
        if not candidate:
            return None
        return self.real.get(os.path.realpath(candidate))

    def wrap_popen_init(self, real_init):
        guard = self

        def __init__(self, *args, **kwargs):
            try:
                bound = _POPEN_SIGNATURE.bind(self, *args, **kwargs)
            except TypeError:
                return real_init(self, *args, **kwargs)  # let Popen word the error
            bound.apply_defaults()
            name = guard.refused_program(bound.arguments)
            if name is not None:
                argv = bound.arguments.get("args")
                cwd = bound.arguments.get("cwd") or os.getcwd()
                guard._refused.append(f"{name}\t{cwd}\t{argv!r}")
                raise AssertionError(
                    f"{guard.nodeid} started the real `{name}` ({argv!r}), refused by "
                    f"the guard (tests/support/real_binaries.py)"
                )
            return real_init(self, *args, **kwargs)

        return __init__


def install(monkeypatch, nodeid: str, e2e_names: frozenset[str] = frozenset()) -> Guard:
    """Both layers, for one test, undone by `monkeypatch`."""
    names = shadowed_for(e2e_names)
    stubs = stub_dir(names)
    path = _without_stubs(os.environ.get("PATH", os.defpath))
    guard = Guard(nodeid, stubs, installed(names, path))
    monkeypatch.setenv("PATH", os.pathsep.join([str(stubs), path]))
    monkeypatch.setattr(
        subprocess.Popen, "__init__", guard.wrap_popen_init(subprocess.Popen.__init__)
    )
    monkeypatch.setattr(sys.modules[__name__], "ACTIVE", guard)
    return guard


def stubbed_path(path: str) -> str:
    """`path` with the active guard's stubs first, for a child process's env."""
    if ACTIVE is None:
        return path
    return os.pathsep.join([str(ACTIVE.stubs), _without_stubs(path)])
