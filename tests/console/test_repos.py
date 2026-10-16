"""Repos (spec §7.5): every repo with its commands, `c` to connect one through
a form the probe fills, `d` to disconnect one after the console's confirm.
The probe and the list are faked at `kraft.client`; every `kraft` child is a
`python -c` echo (`kraft_is`), so nothing is connected or disconnected."""

from __future__ import annotations

import asyncio
import os
import shlex

import pytest
from support.console_board import _settled
from textual.widgets import Button, Checkbox, Input, OptionList

from kraft import client
from kraft.console.dialogs import Confirm, Connect
from kraft.console.screens import repos as repos_view

ECHO = "import sys; print(' '.join(sys.argv[1:]))"
REPOS = [
    {"name": "kraft", "path": "/r", "test_command": "just test [/]", "setup_command": "just setup"},
    {
        "name": "web",
        "path": "/src/web",
        "test_command": None,
        "setup_command": "",
        "enabled": False,
    },
    {
        "name": "mono",
        "path": "/src/mono",
        "test_command": None,
        "test_scopes": [{"dir": "a"}, {"dir": "b"}],
        "setup_command": "make\x1b[2K",
        "managed": False,
    },
]
PROBED = {"path": "/r", "test_command": "uv run pytest", "setup_command": "uv sync"}
PROPOSED = "Kraft proposes these for /r. Keep or change them."
NOT_GIT = client.status_error(400, "/x is not a git repository [/]")


def _rows(app) -> list[str]:
    return [o.prompt.plain for o in app.query_one("#repo-rows", OptionList).options]


def _paths(app) -> list[str | None]:
    return [o.id for o in app.query_one("#repo-rows", OptionList).options]


def _said(app) -> str:
    return " ".join(str(w.render()) for w in app.screen.query("#connect-said"))


@pytest.mark.parametrize(
    ("listed", "lines"),
    [
        (
            REPOS,
            [
                "* kraft  /r\n    test: just test [/] · setup: just setup",
                "  web  /src/web · disabled\n    test: not set · setup: none",
                "  mono  /src/mono · detected, not managed\n"
                "    test: 2 scopes · setup: make\\x1b[2K",
            ],
        ),
        ([], [repos_view.EMPTY]),
    ],
    ids=["three-repos", "none-connected"],
)
def test_the_list_shows_each_repo_with_its_commands(board, monkeypatch, listed, lines):
    """`*` on the repo the console was opened in; a command not decided reads
    "not set", a declared none "none"; a control character is written out,
    never obeyed; `[/]` is drawn as typed."""

    async def repos():
        return listed

    monkeypatch.setattr(client, "repos", repos)
    console = board([], repo="/r")

    async def scenario(app, pilot):
        await _settled(console, app, pilot)
        await pilot.press("4")
        await console.until(pilot, lambda: len(_rows(app)) == len(lines))
        return _rows(app)

    assert console.run(scenario) == lines


@pytest.mark.parametrize(
    ("probe", "edits", "flags", "said"),
    [
        (PROBED, {}, [], PROPOSED),
        (PROBED, {"test": "make test"}, ["--test-command=make test"], PROPOSED),
        (PROBED, {"setup": ""}, ["--setup-command="], PROPOSED),
        (PROBED, {"test": "-n auto"}, ["--test-command=-n auto"], PROPOSED),
        (PROBED, {"verify": True}, ["--verify"], PROPOSED),
        (
            NOT_GIT,
            {"test": "pytest"},
            ["--test-command=pytest"],
            "400: /x is not a git repository [/]",
        ),
        (PROBED, None, None, PROPOSED),
    ],
    ids=[
        "as-proposed",
        "test-changed",
        "setup-emptied",
        "a-command-like-a-flag",
        "verify",
        "probe-refused",
        "esc",
    ],
)
def test_c_connects_through_a_form_the_probe_fills(
    board, kraft_is, monkeypatch, tmp_path, probe, edits, flags, said
):
    """The form probes the console's directory and fills what Kraft proposes;
    `kraft repo connect` runs in the output panel with only what was changed,
    so a proposal left alone stands whole. A refused probe says why in the
    server's words, and a command can still be typed. Esc connects nothing."""
    monkeypatch.chdir(tmp_path)
    here = os.getcwd()
    kraft_is(ECHO)
    asked: list[str | None] = []

    async def probe_repo(path=None, *, detect=True, test_command=None):
        asked.append(path)
        if isinstance(probe, Exception):
            raise probe
        return probe

    async def repos():
        return []

    monkeypatch.setattr(client, "probe_repo", probe_repo)
    monkeypatch.setattr(client, "repos", repos)
    console = board([])

    async def scenario(app, pilot):
        await _settled(console, app, pilot)
        await pilot.press("4", "c")
        await console.until(
            pilot,
            lambda: isinstance(app.screen, Connect) and _said(app) not in ("", "Reading the repo…"),
        )
        shown, dialog = _said(app), app.screen
        if edits is None:
            await pilot.press("escape")
        else:
            # A short path: the panel wraps the echo of a temp directory's.
            dialog.query_one("#connect-path", Input).value = "/p"
            for name, value in edits.items():
                if name == "verify":
                    dialog.query_one(Checkbox).value = value
                else:
                    dialog.query_one(f"#connect-{name}", Input).value = value
            dialog.query_one("#connect-go", Button).press()
            await console.until(pilot, lambda: console.output(app)[-1:] == ["exit 0"])
        await console.until(pilot, lambda: not isinstance(app.screen, Connect))
        await pilot.pause()
        return asked, shown, console.output(app)

    argv = None if flags is None else ["repo", "connect", "/p", *flags]
    ran = [f"kraft {shlex.join(argv)}", " ".join(argv), "exit 0"] if argv else []
    assert console.run(scenario) == ([here], said, ran)


@pytest.mark.parametrize(
    ("second", "fields", "said"),
    [
        # The first path's answer lands after the box holds another path: dropped.
        ({"path": "/other", "test_command": "new", "setup_command": "s2"}, ("new", "s2"), "/other"),
        # What the repo's own files propose is written out, never obeyed.
        (
            {"path": "/ot\x1bher", "test_command": "a\x1b[2K", "setup_command": ""},
            ("a\\x1b[2K", ""),
            "/ot\\x1bher",
        ),
        # A refused second probe leaves no command of the first path behind.
        (NOT_GIT, ("", ""), "400: /x is not a git repository [/]"),
    ],
    ids=["stale-answer-dropped", "proposal-written-out", "each-probe-empties-the-fields"],
)
def test_the_probe_answers_only_for_the_path_in_the_box(
    board, monkeypatch, tmp_path, second, fields, said
):
    """Each probe first empties both command fields, and an answer for a path
    no longer in the box is dropped. The first answer is held, then released
    after the second probe has answered, as a reply already in flight is."""
    monkeypatch.chdir(tmp_path)
    release = asyncio.Event()
    first_asked = asyncio.Event()

    async def probe_repo(path=None, *, detect=True, test_command=None):
        if path == "/other":
            if isinstance(second, Exception):
                raise second
            return second
        first_asked.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            await release.wait()  # the reply is already on its way: a cancel does not recall it
        return PROBED

    async def repos():
        return []

    monkeypatch.setattr(client, "probe_repo", probe_repo)
    monkeypatch.setattr(client, "repos", repos)
    console = board([])

    async def scenario(app, pilot):
        await _settled(console, app, pilot)
        await pilot.press("4", "c")
        await console.until(pilot, lambda: isinstance(app.screen, Connect) and first_asked.is_set())
        box = app.screen.query_one("#connect-path", Input)
        if isinstance(second, Exception):
            # Fill the fields from a first answer, then ask a second path.
            release.set()
            await console.until(pilot, lambda: _said(app) == PROPOSED)
        box.value = "/other"
        await pilot.press("enter")
        await console.until(pilot, lambda: said in _said(app))
        release.set()
        await pilot.pause()
        await pilot.pause()
        got = tuple(app.screen.query_one(f"#connect-{n}", Input).value for n in ("test", "setup"))
        return got, _said(app)

    got, shown = console.run(scenario)
    assert (got, said in shown) == (fields, True)


@pytest.mark.parametrize(
    ("keys", "ran", "left"),
    [
        (
            ["shift+tab", "enter"],
            ["kraft repo disconnect /src/web", "repo disconnect /src/web", "exit 0"],
            ["/r"],
        ),
        (["escape"], [], ["/r", "/src/web"]),
    ],
    ids=["go-on", "cancel"],
)
def test_d_disconnects_the_selected_repo_after_the_consoles_confirm(
    board, kraft_is, monkeypatch, tmp_path, keys, ran, left
):
    """`d` asks what a typed `/repo disconnect` asks (spec §7.8), runs it for
    the selected repo, and reads the list again."""
    gone = tmp_path / "disconnected"
    kraft_is(f"import pathlib, sys; pathlib.Path({str(gone)!r}).touch(); {ECHO}")

    async def repos():
        return REPOS[:1] if gone.exists() else REPOS[:2]

    monkeypatch.setattr(client, "repos", repos)
    console = board([])

    async def scenario(app, pilot):
        await _settled(console, app, pilot)
        await pilot.press("4")
        await console.until(pilot, lambda: len(_paths(app)) == 2)
        await pilot.press("j", "d")
        await console.until(pilot, lambda: isinstance(app.screen, Confirm))
        await pilot.pause()
        question = (
            str(app.screen.query_one("#question").render()),
            str(app.screen.query_one("#listing").render()),
        )
        await pilot.press(*keys)
        await console.until(pilot, lambda: not isinstance(app.screen, Confirm))
        if ran:
            await console.until(pilot, lambda: console.output(app)[-1:] == ["exit 0"])
        await console.until(pilot, lambda: _paths(app) == left)
        return question, console.output(app)

    assert console.run(scenario) == (
        ("kraft repo disconnect /src/web", "This disconnects the repo from Kraft."),
        ran,
    )


def test_a_hidden_repos_view_reads_nothing(board, monkeypatch):
    """It stays mounted behind the others and hears every reread: it reads the
    list when shown, and on a reread only while it is shown."""
    reads: list[None] = []

    async def repos():
        reads.append(None)
        return REPOS[:1]

    monkeypatch.setattr(client, "repos", repos)
    console = board([])

    async def scenario(app, pilot):
        await _settled(console, app, pilot)  # the first board read was a reread too
        seen = [len(reads)]
        await pilot.press("4")
        await console.until(pilot, lambda: len(reads) == 1)
        app._live_event([], [])
        await console.until(pilot, lambda: len(reads) == 2)
        await pilot.press("1")
        app._live_event([], [])
        await pilot.pause()
        return [*seen, len(reads)]

    assert console.run(scenario) == [0, 2]
