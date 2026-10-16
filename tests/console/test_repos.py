"""Repos (spec §7.5): every repo with its commands, `c` to connect one through
a form the probe fills, `d` to disconnect one after the console's confirm.
The probe and the list are faked at `kraft.client`; every `kraft` child is a
`python -c` echo (`kraft_is`), so nothing is connected or disconnected."""

from __future__ import annotations

import asyncio
import os
import shlex

import pytest
from support.console_board import _settled, _text
from textual.color import Color
from textual.widgets import Button, Checkbox, Input, OptionList

from kraft import client
from kraft.console.dialogs import Confirm, Connect
from kraft.console.prompt import Prompt
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
        "name": "mono\x1b",
        "path": "/src/mono\x1b",
        "test_command": None,
        "test_scopes": [{"dir": "a"}, {"dir": "b"}],
        "setup_command": "make\x1b[2K",
        "managed": False,
    },
]
PROBED = {"path": "/r", "test_command": "uv run pytest", "setup_command": "uv sync"}
PROPOSED = "Kraft proposes these for r. Keep or change them."
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
                "  mono\\x1b  /src/mono\\x1b · detected, not managed\n"
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
            await pilot.press("enter")  # read it, so its proposal is what the fields show
            await console.until(
                pilot, lambda: len(asked) == 2 and _said(app) != "Reading the repo…"
            )
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

    argv = None if flags is None else ["repo", "connect", *flags, "--", "/p"]
    ran = [f"kraft {shlex.join(argv)}", " ".join(argv), "exit 0"] if argv else []
    assert console.run(scenario) == ([here] if flags is None else [here, "/p"], said, ran)


@pytest.mark.parametrize(
    ("second", "fields", "said"),
    [
        # What the repo's own files propose is written out, never obeyed.
        (
            {"path": "/ot\x1bher", "test_command": "a\x1b[2K", "setup_command": ""},
            ("a\\x1b[2K", ""),
            "Kraft proposes these for ot\\x1bher. Keep or change them.",
        ),
        # A probe that found nothing says so, naming the repo by its last component.
        (
            {"path": "/src/empty-repo/", "test_command": None, "setup_command": ""},
            ("", ""),
            "Kraft found no test or setup command in empty-repo; type them, or leave them empty.",
        ),
        # A refused second probe leaves no command of the first path behind.
        (NOT_GIT, ("", ""), "400: /x is not a git repository [/]"),
        # ... and its words are written out too: the server echoes the typed path.
        (
            client.status_error(400, "/x\x1b[2K is not a git repository"),
            ("", ""),
            "400: /x\\x1b[2K is not a git repository",
        ),
    ],
    ids=[
        "proposal-written-out",
        "nothing-proposed",
        "each-probe-empties-the-fields",
        "refusal-written-out",
    ],
)
def test_each_probe_starts_from_empty_fields_and_writes_out_what_it_shows(
    board, monkeypatch, second, fields, said
):
    """Reading a second path first empties both command fields, and what the
    probe answers or refuses with is written out, never obeyed."""

    asked: list[None] = []

    async def probe_repo(path=None, *, detect=True, test_command=None):
        asked.append(None)
        if len(asked) == 1:
            return PROBED
        if isinstance(second, Exception):
            raise second
        return second

    async def repos():
        return []

    monkeypatch.setattr(client, "probe_repo", probe_repo)
    monkeypatch.setattr(client, "repos", repos)
    console = board([])

    async def scenario(app, pilot):
        await _settled(console, app, pilot)
        await pilot.press("4", "c")
        await console.until(pilot, lambda: _said(app) == PROPOSED)
        await pilot.press("enter")  # the path box has the focus: read the same path again
        await console.until(pilot, lambda: _said(app) == said)
        return tuple(app.screen.query_one(f"#connect-{n}", Input).value for n in ("test", "setup"))

    assert console.run(scenario) == fields


@pytest.mark.parametrize(
    ("edit", "refused", "fields", "said", "flags"),
    [
        # The box holds another path by the time the answer lands: dropped.
        ("path", False, ("", ""), "Enter reads the new path", []),
        # ... and so is a refusal for the old path.
        ("path", True, ("", ""), "Enter reads the new path", []),
        # A command typed meanwhile is the person's: kept, and passed.
        ("test", False, ("mine", "uv sync"), PROPOSED, ["--test-command=mine"]),
    ],
    ids=["stale-answer-dropped", "stale-refusal-dropped", "typed-command-kept"],
)
def test_an_answer_that_lands_late_never_overwrites_what_the_person_did(
    board, monkeypatch, tmp_path, edit, refused, fields, said, flags
):
    """A probe can run for minutes. The person edits the path (without Enter)
    or a command meanwhile: the old path's answer or refusal is dropped, and a
    typed command stays."""
    monkeypatch.chdir(tmp_path)
    release = asyncio.Event()
    first_asked = asyncio.Event()
    ran: list[list[str]] = []

    async def probe_repo(path=None, *, detect=True, test_command=None):
        first_asked.set()
        await release.wait()
        if refused:
            raise NOT_GIT
        return PROBED

    async def repos():
        return []

    monkeypatch.setattr(client, "probe_repo", probe_repo)
    monkeypatch.setattr(client, "repos", repos)
    console = board([])

    async def scenario(app, pilot):
        async def run_command(words):
            ran.append(words)

        app.run_command = run_command
        await _settled(console, app, pilot)
        await pilot.press("4", "c")
        await console.until(pilot, lambda: isinstance(app.screen, Connect) and first_asked.is_set())
        dialog = app.screen
        dialog.query_one(f"#connect-{edit}", Input).value = "/other" if edit == "path" else "mine"
        await pilot.pause()
        release.set()
        await console.until(pilot, lambda: _said(app) == said)
        got = tuple(dialog.query_one(f"#connect-{n}", Input).value for n in ("test", "setup"))
        dialog.query_one("#connect-go", Button).press()
        await console.until(pilot, lambda: ran)
        return got, ran[0]

    path = "/other" if edit == "path" else os.getcwd()
    assert console.run(scenario) == (fields, ["repo", "connect", *flags, "--", path])


@pytest.mark.parametrize(
    ("typed", "fields", "flags"),
    [
        (None, ("", ""), []),
        ("mine", ("mine", ""), ["--test-command=mine"]),
    ],
    ids=["untouched", "typed"],
)
def test_editing_the_path_drops_the_old_paths_proposal_and_sends_only_what_was_typed(
    board, monkeypatch, tmp_path, typed, fields, flags
):
    """The fields show `/a`'s commands; the path becomes `/b` without Enter.
    `/a`'s proposal is no longer shown, and never passed for `/b`."""
    monkeypatch.chdir(tmp_path)
    ran: list[list[str]] = []

    async def probe_repo(path=None, *, detect=True, test_command=None):
        return PROBED

    async def repos():
        return []

    monkeypatch.setattr(client, "probe_repo", probe_repo)
    monkeypatch.setattr(client, "repos", repos)
    console = board([])

    async def scenario(app, pilot):
        async def run_command(words):
            ran.append(words)

        app.run_command = run_command
        await _settled(console, app, pilot)
        await pilot.press("4", "c")
        await console.until(pilot, lambda: _said(app) == PROPOSED)
        dialog = app.screen
        dialog.query_one("#connect-path", Input).value = "/b"
        await console.until(pilot, lambda: _said(app) == "Enter reads the new path")
        if typed:
            dialog.query_one("#connect-test", Input).value = typed
        got = tuple(dialog.query_one(f"#connect-{n}", Input).value for n in ("test", "setup"))
        dialog.query_one("#connect-go", Button).press()
        await console.until(pilot, lambda: ran)
        return got, ran[0]

    assert console.run(scenario) == (fields, ["repo", "connect", *flags, "--", "/b"])


def test_c_while_a_command_runs_says_so_and_opens_no_form(board, monkeypatch):
    """A connect refused after the form would throw away what was typed: the
    form does not open while another command runs."""

    async def repos():
        return []

    monkeypatch.setattr(client, "repos", repos)
    console = board([])

    async def scenario(app, pilot):
        app.command = object()  # what `run_command` checks
        await _settled(console, app, pilot)
        await pilot.press("4", "c")
        await pilot.pause(0.2)
        toasts = [str(t.render()) for t in app.screen.query("Toast")]
        app.command = None
        return isinstance(app.screen, Connect), toasts

    assert console.run(scenario, notifications=True) == (
        False,
        ["A command is still running: Ctrl+O shows it, x stops it."],
    )


@pytest.mark.parametrize("typed", ["-x", "sub dir"], ids=["leading-dash", "space"])
def test_a_typed_path_reaches_the_runner_absolute_never_as_a_flag(
    board, monkeypatch, tmp_path, typed
):
    """`kraft repo connect -x` would read the path as a flag: the form makes
    it absolute against the console's directory first."""
    monkeypatch.chdir(tmp_path)
    ran: list[list[str]] = []

    async def probe_repo(path=None, *, detect=True, test_command=None):
        return PROBED

    async def repos():
        return []

    monkeypatch.setattr(client, "probe_repo", probe_repo)
    monkeypatch.setattr(client, "repos", repos)
    console = board([])

    async def scenario(app, pilot):
        async def run_command(words):
            ran.append(words)

        app.run_command = run_command
        await _settled(console, app, pilot)
        await pilot.press("4", "c")
        await console.until(pilot, lambda: _said(app) == PROPOSED)
        app.screen.query_one("#connect-path", Input).value = typed
        app.screen.query_one("#connect-go", Button).press()
        await console.until(pilot, lambda: ran)
        return ran[0]

    assert console.run(scenario) == ["repo", "connect", "--", os.path.join(os.getcwd(), typed)]


@pytest.mark.parametrize(
    ("size", "tabs", "buttons", "focus"),
    [
        ((80, 24), 0, ("go", "no"), "connect-path"),
        ((60, 16), 4, ("go",), "connect-go"),
    ],
    ids=["whole-at-80x24", "reachable-at-60x16"],
)
def test_the_connect_form_fits_a_window_or_scrolls_to_the_focused_button(
    board, monkeypatch, size, tabs, buttons, focus
):
    """At 80x24 both buttons are on screen; in a small window, Tab to Connect
    brings it into view."""

    async def probe_repo(path=None, *, detect=True, test_command=None):
        return PROBED

    async def repos():
        return []

    monkeypatch.setattr(client, "probe_repo", probe_repo)
    monkeypatch.setattr(client, "repos", repos)
    console = board([])

    async def scenario(app, pilot):
        await _settled(console, app, pilot)
        await pilot.press("4", "c")
        await console.until(pilot, lambda: _said(app) == PROPOSED)
        for _ in range(tabs):  # from the path: test, setup, Verify, Connect
            await pilot.press("tab")
        await pilot.pause()
        inside = {
            b: app.screen.region.contains_region(
                app.screen.query_one(f"#connect-{b}", Button).region
            )
            for b in buttons
        }
        return inside, app.focused.id

    assert console.run(scenario, size=size) == ({b: True for b in buttons}, focus)


def test_a_hidden_repos_view_stops_a_read_in_flight(board, monkeypatch):
    """Switching away cancels a read still waiting on the server."""
    started, stopped = asyncio.Event(), asyncio.Event()

    async def repos():
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            stopped.set()
            raise

    monkeypatch.setattr(client, "repos", repos)
    console = board([])

    async def scenario(app, pilot):
        await _settled(console, app, pilot)
        await pilot.press("4")
        await console.until(pilot, started.is_set)
        await pilot.press("1")
        await console.until(pilot, stopped.is_set)  # bounded: it fails if never cancelled
        return stopped.is_set()

    assert console.run(scenario) is True


@pytest.mark.parametrize(
    ("keys", "ran", "left"),
    [
        (
            ["shift+tab", "enter"],
            [
                "kraft repo disconnect -- /src/web",
                "repo disconnect -- /src/web",
                "exit 0",
            ],
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
        ("kraft repo disconnect -- /src/web", "This disconnects the repo from Kraft."),
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


@pytest.mark.parametrize("how", ["form-connect", "typed-disconnect"])
def test_connecting_or_disconnecting_the_consoles_own_repo_moves_its_scope(
    board, kraft_is, monkeypatch, tmp_path, how
):
    """Whoever runs `repo connect` or `repo disconnect`, the directory's repo is
    resolved again: the top line, the Board's scope and Repos' `*` follow."""
    done = tmp_path / "done"
    kraft_is(f"import pathlib, sys; pathlib.Path({str(done)!r}).touch(); {ECHO}")
    connecting = how == "form-connect"

    def connected() -> bool:
        return done.exists() == connecting

    async def resolve_repo(cwd=None):
        return "/r" if connected() else None

    async def repos():
        return [REPOS[0]] if connected() else []

    async def probe_repo(path=None, *, detect=True, test_command=None):
        return PROBED

    console = board([], repo=None if connecting else "/r")
    monkeypatch.setattr(client, "resolve_repo", resolve_repo)
    monkeypatch.setattr(client, "repos", repos)
    monkeypatch.setattr(client, "probe_repo", probe_repo)

    def top(app) -> str:
        return _text(app, "#where")

    async def scenario(app, pilot):
        await _settled(console, app, pilot)
        await pilot.press("4")
        await console.until(
            pilot, lambda: len(_rows(app)) == (0 if connecting else 1) or _rows(app)
        )
        if connecting:
            await pilot.press("c")
            await console.until(pilot, lambda: _said(app) == PROPOSED)
            app.screen.query_one("#connect-go", Button).press()
        else:
            await pilot.press("slash")
            app.query_one(Prompt).value = "/repo disconnect /r"
            await pilot.press("enter")
            await console.until(pilot, lambda: isinstance(app.screen, Confirm))
            await pilot.press("shift+tab", "enter")
        await console.until(pilot, lambda: console.output(app)[-1:] == ["exit 0"])
        want = "/r" if connecting else None
        await console.until(
            pilot,
            lambda: (
                app.scope == want and (_rows(app) or [""])[0][:2] == ("* " if connecting else "No")
            ),
        )
        return app.scope, top(app), _rows(app)[0][:2]

    got = console.run(scenario)
    assert got == (("/r", "/r", "* ") if connecting else (None, "all repos", "No"))


@pytest.mark.parametrize("theme", ["kraft-dark", "kraft-light"])
def test_the_connect_forms_unfocused_titles_and_mark_are_secondary_text(board, monkeypatch, theme):
    """Textual left them dark on the dialog's surface; they take the muted
    text colour, which the theme's contrast floor covers on that surface."""

    async def probe_repo(path=None, *, detect=True, test_command=None):
        return PROBED

    async def repos():
        return []

    monkeypatch.setattr(client, "probe_repo", probe_repo)
    monkeypatch.setattr(client, "repos", repos)
    console = board([])

    async def scenario(app, pilot):
        app.theme = theme
        await _settled(console, app, pilot)
        await pilot.press("4", "c")
        await console.until(pilot, lambda: _said(app) == PROPOSED)
        box, mark = app.screen.query_one("#connect-test", Input), app.screen.query_one(Checkbox)
        muted = Color.parse(app.theme_variables["kraft-muted"])
        return (
            box.styles.border_title_color == muted,
            mark.get_component_styles("toggle--button").color == muted,
        )

    assert console.run(scenario) == (True, True)
