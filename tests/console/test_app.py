"""The console's frame: the top line, the views and their keys, small
windows, the no-server state, and `kraft console`'s own refusals."""

from __future__ import annotations

import asyncio
import tomllib
from fnmatch import fnmatch
from pathlib import Path

import pytest
from support.console_board import _text
from support.harness import connected_repo
from textual.app import App
from textual.binding import Binding
from textual.widgets import ContentSwitcher, Footer, HelpPanel, Input

from kraft import cli, client
from kraft.console import app as console_app
from kraft.console import live
from kraft.console.app import TABS, KraftConsole
from kraft.console.dialogs import Confirm, Note
from kraft.console.live import Link
from kraft.console.prompt import Prompt
from kraft.console.screens import VIEWS, View

OTHER_INSTANCE = client.ClientError(
    "kraft: the server at x is a different instance", kind=client.Failure.OTHER_INSTANCE
)
UNREACHABLE = client.transport._unreachable()


def test_an_event_reads_the_board_again(console, stream, tmp_path):
    """The proof the rest stands on: `run_test()`, the real API in process
    and a faked event stream, in one loop. An item filed after the console
    opened appears once an event says something changed."""
    repo = connected_repo(tmp_path)

    async def scenario(app, pilot):
        assert app.link is Link.LIVE
        assert app.rows == []
        created = await client.create_work_item("seen live", repo=str(repo))
        stream.send({"seq": 1_000, "type": "work_item_created", "work_item_id": created["id"]})
        await console.until(pilot, lambda: app.rows and app.health)
        return created["id"], app.rows, _text(app, "#tabs")

    wid, rows, tabs = console.run(scenario)
    assert [row["id"] for row in rows] == [wid]
    assert tabs.startswith("Kraft ● live")
    assert "Server ok" in tabs


@pytest.mark.parametrize(
    ("refusal", "host", "said", "start"),
    [
        (client.transport._unreachable, None, "No server at 127.0.0.1:", True),
        (
            lambda: client.status_error(401, "a token this server does not know"),
            None,
            "The server refused this machine's token (run/mcp-token).",
            False,
        ),
        (
            lambda: client.status_error(500, "database disk image is malformed"),
            None,
            "The server could not show the board: 500: database disk image is malformed",
            False,
        ),
        (client.transport._unreachable, "10.1.2.3", "No server at 10.1.2.3:", False),
    ],
    ids=["no-server", "token-refused", "server-error", "no-server-elsewhere"],
)
def test_a_server_the_console_cannot_use_is_said_on_every_view(
    console, monkeypatch, refusal, host, said, start
):
    """No view is drawn over a server that is not there, and a refused token
    is never reported as no server: one sends a person to start a server that
    is already running. A server that answers every board read with an error
    is said in its own words, not "reconnecting" forever. "Start server" is
    offered only for a server this machine can start."""

    async def board_rows(**kwargs):
        raise refusal()

    monkeypatch.setattr(client, "board_rows", board_rows)
    monkeypatch.setattr(live, "BACKOFF_S", (0.1,))
    if host:
        monkeypatch.setenv("KRAFT_HOST", host)

    async def scenario(app, pilot):
        seen = []
        for name in TABS:
            app.show(name)
            await pilot.pause()
            seen.append((app.query_one("#down").display, app.query_one(ContentSwitcher).display))
        button = app.query_one_optional("#start")
        return (
            app.link,
            _text(app, "#down"),
            _text(app, "#tabs"),
            seen,
            bool(button and button.display),
        )

    link, down, tabs, seen, offered = console.run(scenario)
    assert link is Link.DOWN
    assert down.startswith(said)
    assert "reconnecting" in tabs
    assert seen == [(True, False)] * len(TABS)
    assert offered is start


@pytest.mark.parametrize(
    "live_first", [True, False], ids=["live-under-the-open-panel", "live-as-it-closes"]
)
def test_enter_on_no_server_starts_one_on_its_own(console, kraft_is, monkeypatch, live_first):
    """Spec §7.3: the focused button runs `kraft admin start --detach` in the
    output panel, which returns once the server answers; the console then
    reconnects at once rather than at the end of its backoff."""
    kraft_is("import sys; print(' '.join(sys.argv[1:]))")

    async def gone(**kwargs):
        raise client.transport._unreachable()

    # The server is up once the scenario says so: the live loop's next read,
    # due 0.5s after the first, must find it there, or it says DOWN again.
    up, reads = [], []

    async def board_rows(**kwargs):
        reads.append(bool(up))
        if not up:
            raise client.transport._unreachable()
        return [], 0

    monkeypatch.setattr(client, "board_rows", board_rows)
    monkeypatch.setattr(client, "health", gone)
    retried = []
    wake = live.Live.retry
    monkeypatch.setattr(live.Live, "retry", lambda self: retried.append(True))

    async def scenario(app, pilot):
        def server_back() -> None:
            up.append(True)
            app._live_link(Link.LIVE, None)

        await pilot.pause()
        focused = app.focused.id
        await pilot.press("enter")
        await console.until(pilot, lambda: console.output(app)[-1:] == ["exit 0"])
        # The server answers before the panel closes, or as it does: either way
        # the frame's remembered focus must not be the button that is gone.
        if live_first:
            server_back()
        await pilot.press("escape")
        if not live_first:
            server_back()
        # The loop reads again now, not whenever its backoff ends.
        wake(app.live)
        await console.until(pilot, lambda: True in reads)
        await pilot.pause()
        return focused, console.output(app), retried, app.focused is app.view

    assert console.run(scenario) == (
        "start",
        ["kraft admin start --detach", "admin start --detach", "exit 0"],
        [True],
        # The button is gone once the server is: the view's keys work again.
        True,
    )


@pytest.mark.parametrize(
    ("opened_in", "count"),
    [(None, "Board 6"), ("/r", "Board 4")],
    ids=["all-repos", "in-a-repo"],
)
def test_the_board_tab_counts_what_needs_you_where_it_was_opened(
    console, monkeypatch, opened_in, count
):
    """The count a person reads before opening the board: items that need
    them, in the repo the console was opened in (spec §5.4)."""
    # Needs you also holds a failure and an item paused mid-chain, as on the web.
    rows = [
        {"id": f"w{n}", "display_status": status, "repo": repo, "current_node_id": node}
        for n, (status, repo, node) in enumerate(
            [
                ("needs_you", "/r", "plan"),
                ("needs_you", "/r", "plan"),
                ("needs_you", "/other", "plan"),
                ("running", "/r", "plan"),
                ("failed", "/r", "plan"),
                ("paused", "/r", "plan"),
                ("paused", "/other", None),
                ("failed", "/other", "plan"),
            ]
        )
    ]

    async def board_rows(**kwargs):
        return rows, 0

    async def resolve_repo(cwd=None):
        return opened_in

    monkeypatch.setattr(client, "board_rows", board_rows)
    monkeypatch.setattr(client, "resolve_repo", resolve_repo)

    async def scenario(app, pilot):
        await console.until(pilot, lambda: count in _text(app, "#tabs"))
        return _text(app, "#where")

    assert console.run(scenario).startswith(opened_in or "all repos")


@pytest.mark.parametrize(
    ("answer", "word"),
    [("wait", "Server …"), ("none", "Server down"), ("ok", "Server ok")],
    ids=["not-yet", "no-answer", "answered"],
)
def test_the_server_tab_does_not_say_down_before_health_answers(console, monkeypatch, answer, word):
    async def health():
        if answer == "wait":
            await asyncio.Event().wait()
        if answer == "none":
            raise client.ClientError("no server", kind=client.Failure.UNREACHABLE)
        return {"status": "ok"}

    monkeypatch.setattr(client, "health", health)

    async def scenario(app, pilot):
        await pilot.pause(0.1)
        return _text(app, "#tabs")

    assert word in console.run(scenario)


def test_a_refusal_is_shown_in_the_servers_own_words(console):
    """A toast reads as the server wrote it, `[/]` and all: with markup on,
    a title like that is a markup error that ends the console."""

    async def scenario(app, pilot):
        refused = "kraft 409: [/] is not a title"
        app.notify_refusal(client.ClientError(refused, kind=client.Failure.REFUSED, status=409))
        await pilot.pause()
        return [str(toast.render()) for toast in app.screen.query("Toast")]

    assert console.run(scenario, notifications=True) == ["409: [/] is not a title"]


def test_a_wheel_carries_every_file_the_console_reads():
    """Spec §4.1: without a package-data entry a wheel ships no `kraft.tcss`,
    and an installed console fails at start where a checkout works."""
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    shipped = tomllib.loads(pyproject.read_text())["tool"]["setuptools"]["package-data"]["kraft"]
    package = Path(console_app.__file__).parent
    files = [
        path.relative_to(package.parent).as_posix()
        for path in package.rglob("*")
        if path.is_file() and path.suffix not in (".py", ".pyc")
    ]
    assert files
    assert [f for f in files if not any(fnmatch(f, pattern) for pattern in shipped)] == []


@pytest.mark.parametrize(
    ("start", "keys", "view", "running"),
    [
        ("board", "2", "server", True),
        ("board", "3", "addons", True),
        ("board", "4", "repos", True),
        ("server", "1", "board", True),
        ("item", "escape", "board", True),
        ("item", "q", "board", True),
        ("server", "escape", "server", True),
        ("server", "q", "server", False),
    ],
    ids=["2", "3", "4", "1", "item-esc", "item-q", "top-esc", "top-q-quits"],
)
def test_the_keys_move_between_views(console, start, keys, view, running):
    """Every key acts on the view it lands on, so focus follows the view;
    `q` goes back from the Item and quits only from a top view."""

    async def scenario(app, pilot):
        app.show(start)
        await pilot.press(keys)
        return app.view.id, app.focused is app.view, app.is_running

    assert console.run(scenario) == (view, True, running)


def test_question_mark_opens_and_closes_every_key(console):
    async def scenario(app, pilot):
        await pilot.press("question_mark")
        opened = bool(app.screen.query(HelpPanel))
        await pilot.press("question_mark")
        return opened, bool(app.screen.query(HelpPanel))

    assert console.run(scenario) == (True, False)


@pytest.mark.parametrize(
    ("size", "no_server", "shown"),
    [
        # (the tiny message, the narrow `? keys`, the no-server message or its button)
        ((59, 24), False, (True, False, False)),
        ((80, 15), False, (True, False, False)),
        ((60, 16), False, (False, True, False)),
        ((79, 24), False, (False, True, False)),
        ((80, 24), False, (False, False, False)),
        ((59, 24), True, (True, False, False)),
        ((79, 24), True, (False, True, True)),
    ],
    ids=["59-wide", "15-high", "60x16", "79-wide", "80x24", "59-wide-no-server", "79-no-server"],
)
def test_a_small_window_asks_to_be_larger_and_a_narrow_one_keeps_one_key(
    console, monkeypatch, size, no_server, shown
):
    """Under 60x16 nothing else is drawn, whatever state the link is in, so
    the frame says so instead of drawing it clipped; under 80 columns the
    footer keeps only `? keys`."""
    if no_server:

        async def board_rows(**kwargs):
            raise client.transport._unreachable()

        monkeypatch.setattr(client, "board_rows", board_rows)

    async def scenario(app, pilot):
        await pilot.resize_terminal(*size)
        await pilot.pause()
        return (
            app.query_one("#tiny").display and app.query_one("#tiny").region.area > 0,
            app.query_one("#narrow-keys").display and not app.query_one(Footer).display,
            app.query_one("#down").display or app.query_one("#start").display,
        )

    assert console.run(scenario) == shown


def test_the_palette_switches_kraft_looks_not_textuals(console):
    """Textual's theme picker lists its own themes, none of them Kraft's."""

    async def scenario(app, pilot):
        commands = {c.title: c for c in app.get_system_commands(app.screen)}
        commands["Light"].callback()
        await pilot.pause()
        return sorted(commands), app.theme, app.theme_variables["kraft-side"]

    titles, theme, side = console.run(scenario)
    assert "Theme" not in titles
    assert {"Dark", "Light"} <= set(titles)
    assert (theme, side) == ("kraft-light", console_app.theme.LIGHT["side"])


@pytest.mark.parametrize("name", list(VIEWS))
def test_no_key_does_two_things_on_one_view(name):
    """Spec §6: no key means two things on one view, and no upper and lower
    pair (`R` and `r`) shares one. `R,shift+r` is one binding: kitty's name
    for the same key."""
    actions: dict[str, set[str]] = {}
    for owner in (App, KraftConsole, VIEWS[name]):
        for binding in Binding.make_bindings(owner.BINDINGS):
            key = binding.key.removeprefix("shift+").lower()
            actions.setdefault(key, set()).add(binding.action)
    assert {key: found for key, found in actions.items() if len(found) > 1} == {}


@pytest.mark.parametrize(
    ("env", "ttys", "health", "said"),
    [
        ({"KRAFT_WORK_ITEM_ID": "w1"}, (True, True), None, "a worker does not open the console"),
        ({}, (False, True), None, "console needs a terminal"),
        ({}, (True, False), None, "console needs a terminal"),
        ({}, (True, True), OTHER_INSTANCE, "is a different instance"),
        ({}, (True, True), UNREACHABLE, None),
    ],
    ids=["worker", "stdin-piped", "stdout-piped", "other-instance", "no-server-opens"],
)
def test_kraft_console_refuses_where_it_cannot_work(monkeypatch, capsys, env, ttys, health, said):
    """Spec §3. With no server answering it still opens, and says so inside."""
    opened = []
    monkeypatch.setattr(KraftConsole, "run", lambda self: opened.append(self))
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr("sys.stdin.isatty", lambda: ttys[0], raising=False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: ttys[1], raising=False)
    asked = []

    async def refuse():
        asked.append(1)
        if health is not None:
            raise health
        return {}

    monkeypatch.setattr(client, "health", refuse)
    if said is None:
        with pytest.raises(SystemExit) as left:
            cli.main(["console"])
        assert (left.value.code, len(opened)) == (0, 1)
        return
    with pytest.raises(SystemExit) as caught:
        cli.main(["console"])
    assert caught.value.code == 1
    assert said in capsys.readouterr().err
    assert opened == []
    # A worker or a pipe is refused before any call to the server.
    assert bool(asked) == (health is not None)


def test_kraft_console_exits_with_the_codes_textual_ended_with(monkeypatch):
    """A crash inside the app must not read as success to the shell."""
    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True, raising=False)
    monkeypatch.setattr(client, "health", lambda: asyncio.sleep(0, {}))
    monkeypatch.setattr(KraftConsole, "run", lambda self: setattr(self, "_return_code", 1))
    with pytest.raises(SystemExit) as left:
        cli.main(["console"])
    assert left.value.code == 1


@pytest.mark.parametrize(
    ("home", "path", "shown"),
    [
        ("/Users/omid", "/Users/omid", "~"),
        ("/Users/omid", "/Users/omid/work/x", "~/work/x"),
        ("/Users/omid", "/Users/omidkarami/x", "/Users/omidkarami/x"),
        ("/Users/omid", "/srv/Users/omid/x", "/srv/Users/omid/x"),
    ],
    ids=["home", "under-home", "sibling-prefix", "elsewhere"],
)
def test_the_scope_writes_home_as_a_tilde_only_at_a_path_boundary(monkeypatch, home, path, shown):
    monkeypatch.setenv("HOME", home)
    assert console_app.home_relative(path) == shown


@pytest.mark.parametrize(
    ("size", "palette_open"),
    [((59, 24), True), ((59, 24), False)],
    ids=["resized-under-the-palette", "resized-plain"],
)
def test_a_resize_under_the_palette_still_marks_the_frame(console, size, palette_open):
    """Ctrl+P pushes a screen; the frame's size classes belong to the frame,
    not to whatever is on top."""

    async def scenario(app, pilot):
        if palette_open:
            await pilot.press("ctrl+p")
            await pilot.pause()
        await pilot.resize_terminal(*size)
        await pilot.pause()
        if palette_open:
            await pilot.press("escape")
            await pilot.pause()
        return app.query_one("#tiny").display and app.query_one("#tiny").region.area > 0

    assert console.run(scenario) is True


def test_the_tabs_win_the_top_line_at_80_columns(console, monkeypatch):
    """A long repo path, version and update note must not wrap the tabs."""

    async def health():
        return {"status": "degraded"}

    async def resolve_repo(cwd=None):
        return "/Users/someone/projects/a/very/long/repository/path/that/goes/on"

    monkeypatch.setattr(client, "health", health)
    monkeypatch.setattr(client, "resolve_repo", resolve_repo)

    async def scenario(app, pilot):
        app.update_waiting = True
        await console.until(pilot, lambda: app.scope and "degraded" in _text(app, "#tabs"))
        app._draw_top()
        await pilot.pause()
        tabs = app.query_one("#tabs")
        return tabs.region.width >= len(_text(app, "#tabs")), "Repos" in _text(app, "#tabs")

    assert console.run(scenario) == (True, True)


def test_a_link_change_under_the_palette_still_marks_the_frame(console):
    async def scenario(app, pilot):
        await pilot.press("ctrl+p")
        await pilot.pause()
        app._live_link(Link.DOWN, None)
        await pilot.press("escape")
        await pilot.pause()
        return app.query_one("#down").display, app.query_one(ContentSwitcher).display

    assert console.run(scenario) == (True, False)


#: Each dialog the console pushes over its frame, opened.
DIALOGS = {
    "output": lambda app: app.action_output(),
    "confirm": lambda app: app.push_screen(Confirm("kraft admin stop", [])),
}


@pytest.mark.parametrize("dialog", list(DIALOGS))
def test_live_updates_reach_the_frame_under_a_dialog(console, monkeypatch, dialog):
    """A dialog is a pushed screen. The board, health and the no-server state
    belong to the frame underneath it, and reach it all the same."""
    seen = set()
    # Each view that has its own hook, and the base for the rest.
    for owner in {View, *VIEWS.values()}:
        monkeypatch.setattr(
            owner, "rows_changed", lambda self, rows, events: seen.add(("rows", self.id))
        )
        monkeypatch.setattr(
            owner, "health_changed", lambda self, health: seen.add(("health", self.id))
        )

    async def scenario(app, pilot):
        DIALOGS[dialog](app)
        await pilot.pause()
        seen.clear()
        app._live_event([{"id": "a1", "display_status": "needs_you"}], [])
        app._live_health({"status": "degraded"})
        app._live_link(Link.DOWN, None)
        on_top = app.screen is not app.default_screen
        await pilot.press("escape")
        await pilot.pause()
        return on_top, _text(app, "#tabs"), app.query_one("#down").display

    on_top, tabs, down = console.run(scenario)
    assert on_top
    assert seen == {(hook, name) for hook in ("rows", "health") for name in VIEWS}
    assert "Board 1" in tabs
    assert "Server degraded" in tabs
    assert down


@pytest.mark.parametrize(
    ("steps", "on_input", "ends_on"),
    [
        ([Link.DOWN], False, "start"),
        ([Link.DOWN], True, "input"),
        ([Link.DOWN, Link.LIVE], False, "view"),
        ([Link.DOWN], "in-view", "start"),
    ],
    ids=[
        "from-the-view",
        "typing-keeps-the-focus",
        "down-and-live-in-one-tick",
        "from-in-the-view",
    ],
)
def test_start_server_takes_the_focus_only_from_the_view(console, steps, on_input, ends_on):
    """The button never pulls focus from where a person types; and a link that
    drops and recovers in one tick ends on the view, not on a button that is gone."""

    async def scenario(app, pilot):
        typing = Input()
        if on_input == "in-view":  # a focusable child of the view, as PR 3 adds
            await app.view.mount(typing)
            app.default_screen.set_focus(typing)
        elif on_input:  # the real prompt, which sits outside the view
            typing = app.query_one(Prompt)
            app.default_screen.set_focus(typing)
        for link in steps:
            app._live_link(link, None)
        await pilot.pause()
        focused = app.default_screen.focused
        return {typing: "input", app.view: "view", app.query_one("#start"): "start"}.get(focused)

    assert console.run(scenario) == ends_on


#: Each dialog that hands back an answer: how it is asked, its class, the
#: keys that answer it, and that answer.
ASKS = {
    "confirm": (lambda app: app.confirm("kraft admin stop"), Confirm, ["shift+tab", "enter"], True),
    "note": (
        lambda app: app.push_screen_wait(Note("Reject spec_approval", required=True)),
        Note,
        ["w", "h", "y", "enter"],
        "why",
    ),
}


@pytest.mark.parametrize("dialog", list(ASKS))
@pytest.mark.parametrize("panel_open_first", [True, False], ids=["panel-beneath", "panel-new"])
def test_a_dialog_survives_the_output_panel_being_asked_for(
    console, kraft_is, panel_open_first, dialog
):
    """Popping a modal drops its result callback: Ctrl+O and a new command must
    leave an open confirm or note to answer."""
    kraft_is("print('x')")
    ask, kind, keys, answer = ASKS[dialog]

    async def scenario(app, pilot):
        # The panel already open beneath it: the case a pop loop would walk through.
        if panel_open_first:
            await pilot.press("ctrl+o")
            await pilot.pause()
        asked = app.run_worker(ask(app))
        await console.until(pilot, lambda: isinstance(app.screen, kind))
        await pilot.press("ctrl+o")
        async with asyncio.timeout(5):
            await app.run_command(["view", "list"])
        await pilot.pause()
        if not isinstance(app.screen, kind):  # the panel opened above it: Esc closes it
            await pilot.press("escape")
            await pilot.pause()
        still = isinstance(app.screen, kind)
        await pilot.press(*keys)
        await console.until(pilot, lambda: asked.is_finished)
        return still, asked.result

    assert console.run(scenario) == (True, answer)
