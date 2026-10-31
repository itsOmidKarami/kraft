"""Shared setup for the forge adapter tests: stub CLIs on PATH, and one forge
node run against a fake through `forge.run_task`."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from support.harness import own_files

from kraft import events
from kraft.adapters import forge

from .nodes import FAIL, Api, FailWith, back_half


class Cli:
    """Fake forge CLIs (`glab`, `gh`, `git`) first on PATH.

    A stub binary rather than a monkeypatched `subprocess.run`: the real argv
    building and the real decoding path run, so a wrong flag or a bytes/str
    slip still fails the test.
    """

    def __init__(self, bindir: Path, monkeypatch):
        self.bindir = bindir
        bindir.mkdir(exist_ok=True)
        monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")

    def stub(
        self, name: str, stdout: str = "", *, rc: int = 0, routes=None, default="{}", record=False
    ):
        """Install `name`. Without `routes` every call prints `stdout` and exits
        `rc`. With `routes` (`{"mr view": stdout, "ci get": FAIL}`) the answer
        depends on the call, and an unrouted call prints `default` (`FAIL`: exit
        1; a `FailWith` exits 1 saying its text; an `Api` answers like
        `<cli> api`). A key without `*` matches the first two arguments; a key
        with `*` globs the whole argv (`"api*pulls/7*"`). Routes are tried in
        order. Asked `api ... -i`, a plain string answer comes back as a 200
        envelope, unless it already is one. `record=True` also keeps the last
        call's stdin, cwd, environment, pid and session id (`recorded`)."""

        def quote(text):
            return "'" + text.replace("'", "'\\''") + "'"

        def doc(text):
            return f"cat <<'STUBEOF'\n{text}\nSTUBEOF\n"

        def answer(out):
            if out is FAIL:
                return "echo 'stub: failed' >&2; exit 1"
            if isinstance(out, FailWith):
                return f"echo '{out}' >&2; exit 1"
            if isinstance(out, Api):
                err = f"echo {quote(out.message(name))} >&2; " if out.status >= 400 else ""
                return (
                    f'if [ -n "$INC" ]; then {doc(out.envelope())}  else {doc(out.body)}  {err}fi; '
                    f"exit {out.exit_code}"
                )
            wrap = name != "git" and not out.startswith("HTTP/")
            head = 'printf "HTTP/2.0 200 OK\\n\\n"; ' if wrap else ""
            gate = '[ "$1" = api ] && [ -n "$INC" ]'
            return (
                f"{'if ' + gate + '; then ' + head + 'fi; ' if wrap else ''}{doc(out)}  exit {rc}"
            )

        if routes is None:
            routes, default = {}, stdout
        cases = ""
        for key, out in routes.items():
            subject, pat = (
                ('"$*"', "*".join(map(quote, key.split("*"))))
                if "*" in key
                else (
                    '"$1 $2"',
                    quote(key),
                )
            )
            cases += f"case {subject} in\n  {pat}) {answer(out)} ;;\nesac\n"
        base = self.bindir / name
        rec = (
            f"cat > {base}.stdin; pwd > {base}.pwd; env > {base}.env; "
            f"echo $$ > {base}.pid; "
            f"{sys.executable} -c 'import os; print(os.getsid(0))' > {base}.sid\n"
            if record
            else ""
        )
        p = self.bindir / name
        p.write_text(
            f'#!/bin/sh\nfor a in "$@"; do printf "%s\\n" "$a" >> {self.bindir / name}.argv; done\n'
            f'echo "$@" >> {self.bindir / name}.calls\n'
            f"{rec}"
            f'INC=; for a in "$@"; do [ "$a" = -i ] && INC=1; done\n'
            f"{cases}{answer(default)}\n"
        )
        p.chmod(0o755)

    def argv(self, name: str) -> list[str]:
        """Every argument of every call to `name`, one per item."""
        path = self.bindir / f"{name}.argv"
        return path.read_text().splitlines() if path.exists() else []

    def recorded(self, name: str) -> dict:
        """The last recorded call: `stdin`, `pwd`, `env` (a dict), `pid`, `sid`."""
        read = lambda ext: (self.bindir / f"{name}.{ext}").read_text()  # noqa: E731
        env = dict(line.split("=", 1) for line in read("env").splitlines() if "=" in line)
        return {
            "stdin": read("stdin"),
            "pwd": read("pwd").strip(),
            "env": env,
            "pid": int(read("pid")),
            "sid": int(read("sid")),
        }

    def calls(self, name: str) -> list[str]:
        """One space-joined argv string per call to `name`."""
        path = self.bindir / f"{name}.calls"
        return path.read_text().splitlines() if path.exists() else []


@pytest.fixture
def cli(tmp_path, monkeypatch) -> Cli:
    return Cli(tmp_path / "bin", monkeypatch)


class ForgeRun:
    """`await run(fake, handler, sid, **run_task_kwargs)`: one forge node for
    work item `w1`, with `forge.run.resolve` answering `fake`.

    Returns `(returned status, recorded session status)`. Defaults are the
    `mr_checks` node on branch `kraft/w1` in `tmp_path`, against the fake
    backend; `merge_watch` gets its own node and `orig_repo` unless given.
    """

    def __init__(self, database, run_dirs, item_on, tmp_path, monkeypatch):
        self.database, self.run_dirs = database, run_dirs
        self._item_on, self._tmp_path, self._monkeypatch = item_on, tmp_path, monkeypatch
        self.item = None

    async def __call__(self, fake, handler: str, sid: str = "s1", *, repo=None, **kw):
        repo = Path(repo or self._tmp_path)
        self._monkeypatch.setattr(forge.run, "resolve", lambda name: fake)
        if self.item is None:
            self.item = await self._item_on(back_half(), repo=repo)
        if handler == "merge_watch":
            kw = {
                "node_id": "post_merge_watch",
                "hook_point": "on.merge.watch",
                "orig_repo": repo,
                **kw,
            }
        kw = {"node_id": "mr_checks", "hook_point": "on.ci.poll", "backend": "fake", **kw}
        returned = await forge.run_task(
            self.database,
            self.run_dirs,
            session_id=sid,
            work_item_id="w1",
            handler=handler,
            repo=repo,
            **{"branch": "kraft/w1", "title": "t", **kw},
        )
        row = self.database.read(
            lambda c: c.execute(
                "SELECT status FROM worker_sessions WHERE id = ?", (sid,)
            ).fetchone()
        )
        return returned, row["status"] if row else None

    def log(self, sid: str) -> str:
        return own_files(self.run_dirs, sid).log.read_text()

    def events(self, type: str) -> list[dict]:
        evts = self.database.read(lambda c: events.read_after(c, 0, "w1"))
        return [e["payload"] for e in evts if e["type"] == type]

    def sessions(self) -> list[dict]:
        return self.item.sessions()


@pytest.fixture
def run_forge(database, run_dirs, item_on, tmp_path, monkeypatch) -> ForgeRun:
    return ForgeRun(database, run_dirs, item_on, tmp_path, monkeypatch)
