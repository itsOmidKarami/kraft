"""Shared setup for the forge adapter tests: stub CLIs on PATH, and one forge
node run against a fake through `forge.run_task`."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from kraft import events
from kraft.adapters import forge

from .nodes import FAIL, back_half


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

    def stub(self, name: str, stdout: str = "", *, rc: int = 0, routes=None, default="{}"):
        """Install `name`. Without `routes` every call prints `stdout` and exits
        `rc`. With `routes` (`{"mr view": stdout, "ci get": FAIL}`, keyed on
        the first two arguments) the answer depends on the subcommand, and an
        unrouted call prints `default` (`FAIL`: exit 1)."""

        def answer(out):
            if out is FAIL:
                return "echo 'stub: failed' >&2; exit 1"
            return f"cat <<'STUBEOF'\n{out}\nSTUBEOF\n  exit {rc}"

        if routes is None:
            routes, default = {}, stdout
        cases = "".join(f"  '{k}') {answer(v)} ;;\n" for k, v in routes.items())
        p = self.bindir / name
        p.write_text(
            f'#!/bin/sh\nfor a in "$@"; do printf "%s\\n" "$a" >> {self.bindir / name}.argv; done\n'
            f'echo "$@" >> {self.bindir / name}.calls\n'
            f'case "$1 $2" in\n{cases}  *) {answer(default)} ;;\nesac\n'
        )
        p.chmod(0o755)

    def argv(self, name: str) -> list[str]:
        """Every argument of every call to `name`, one per item."""
        path = self.bindir / f"{name}.argv"
        return path.read_text().splitlines() if path.exists() else []

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
        return (self.run_dirs.logs / f"{sid}.log").read_text()

    def events(self, type: str) -> list[dict]:
        evts = self.database.read(lambda c: events.read_after(c, 0, "w1"))
        return [e["payload"] for e in evts if e["type"] == type]

    def sessions(self) -> list[dict]:
        return self.item.sessions()


@pytest.fixture
def run_forge(database, run_dirs, item_on, tmp_path, monkeypatch) -> ForgeRun:
    return ForgeRun(database, run_dirs, item_on, tmp_path, monkeypatch)
