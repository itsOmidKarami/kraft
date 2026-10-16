"""The Server view (spec §7.3): how the server is, `server.log` under it, and
the keys that reload the config here or run doctor, restart and update in the
output panel. Nothing here reaches a real server or installs anything: the
view is handed its health, the release feed and the reload are faked at
`kraft.client`, and every `kraft` child is a `python -c` echo (`kraft_is`)."""

from __future__ import annotations

import asyncio

import pytest
from support.console_board import _text
from textual.widgets import Button, RichLog

from kraft import client, update
from kraft.console import tail
from kraft.console.dialogs import Confirm

ECHO = "import sys; print(' '.join(sys.argv[1:]))"
HEALTH = {
    "status": "ok",
    "version": "2.4.0",
    "installed": "2.4.0",
    "uptime_s": 11520,
    "pid": 4120,
    "bind": "127.0.0.1",
    "port": 8765,
    "invalid_templates": {},
    "invalid_policy": None,
    "invalid_intake": None,
    "index": {"embeddings": {"state": "ready"}, "errors": []},
    # 3.1G and 20G as `render.human_size` writes them.
    "storage": {"state": "ok", "used_bytes": 3_328_599_655, "limit_bytes": 20 * 1024**3},
    "addon_updates": {"tools@c": {"outcome": "held"}, "lint@c": {"outcome": "updated"}},
}
DEGRADED = {
    **HEALTH,
    "status": "degraded",
    "installed": "2.5.0",
    "uptime_s": 59,
    "invalid_templates": {"chain broken": "no [/] such node"},
    "invalid_policy": "bad key",
    "index": {"embeddings": {"state": "not installed"}, "errors": ["disk [/] full"]},
    "storage": None,
    "addon_updates": {},
}
HELD = {**HEALTH, "storage": {**HEALTH["storage"], "state": "held"}}
RELEASE = {"installed": "2.4.0", "latest": "v2.5.0", "behind": True}
UP_TO_DATE = {"installed": "2.4.0", "latest": "v2.4.0", "behind": False}
FEED_DOWN = client.ClientError("the release feed did not answer", kind=client.Failure.UNREACHABLE)
OK_LINES = [
    "Server ● ok · v2.4.0 (2.5.0 available) · up 3h 12m · pid 4120 · 127.0.0.1:8765",
    "Config  templates ✓  policy ✓  intake ✓  ·  Index  ready",
    "Storage  3.1G of 20G  ·  Add-ons  1 update waiting",
]
ACTIVE = [{"id": "a1b2c3d4", "title": "Fix the [/] parser", "current_node_id": "implement"}]


def _quiet(monkeypatch, releases=(UP_TO_DATE,)) -> list:
    """No health poll answers, so each test hands the view its health; the
    release feed answers `releases` in turn, the last one again after.
    Returns the feed's calls."""
    calls: list[None] = []

    async def health():
        await asyncio.Event().wait()

    async def update_available(channel=None):
        calls.append(None)
        answer = releases[min(len(calls), len(releases)) - 1]
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(client, "health", health)
    monkeypatch.setattr(client, "update_available", update_available)
    return calls


def _reading_release(app) -> bool:
    return any(w.group == "release" and not w.is_finished for w in app.workers)


@pytest.mark.parametrize(
    ("healths", "releases", "lines", "reads"),
    [
        ([HEALTH], [RELEASE], OK_LINES, 1),
        (
            [DEGRADED],
            [FEED_DOWN],
            [
                "Server ● degraded · v2.4.0 (2.5.0 installed, R finishes it) · up 59s"
                " · pid 4120 · 127.0.0.1:8765",
                "Config  templates ✗  policy ✗  intake ✓  ·  Index  not installed",
                "Storage  not limited  ·  Add-ons  none waiting",
                "  invalid template: chain broken: no [/] such node",
                "  invalid policy: bad key",
                "  index error: disk [/] full",
            ],
            3,
        ),
        (
            [HELD],
            [FEED_DOWN, RELEASE],
            [*OK_LINES[:2], "Storage  3.1G of 20G · held  ·  Add-ons  1 update waiting"],
            2,
        ),
        (
            [HEALTH, None],
            [RELEASE],
            [OK_LINES[0].replace("● ok", "● no answer"), *OK_LINES[1:]],
            1,
        ),
    ],
    ids=["ok", "degraded", "held-and-the-feed-read-again", "no-answer-keeps-the-last"],
)
def test_the_summary_says_how_the_server_is(console, monkeypatch, healths, releases, lines, reads):
    """Spec §7.3: from `/health` and the release feed, each reason it is
    degraded spelled out in the server's words, `[/]` and all. A feed that
    failed at start is read again each time the view is shown, one that
    answered once is never read again; a server that stops answering leaves
    its last answer on screen, saying so (spec §8)."""
    calls = _quiet(monkeypatch, releases)

    async def scenario(app, pilot):
        await console.until(pilot, lambda: calls)  # the read after the first paint
        for health in healths:
            app._live_health(health)
        await pilot.press("2")
        await console.until(
            pilot,
            lambda: (app.release is not None or len(calls) == 2) and not _reading_release(app),
        )
        await pilot.pause()
        said = _text(app, "#server-summary").split("\n")
        await pilot.press("1", "2")  # shown a second time
        await console.until(pilot, lambda: not _reading_release(app))
        await pilot.pause()
        return said, len(calls)

    assert console.run(scenario) == (lines, reads)


@pytest.mark.parametrize(
    ("keys", "asked", "ran"),
    [
        (["d"], None, ["kraft admin doctor", "admin doctor", "exit 0"]),
        (
            ["R", "shift+tab", "enter"],
            "kraft admin restart",
            ["kraft admin restart", "admin restart", "exit 0"],
        ),
        (["shift+r", "escape"], "kraft admin restart", []),
        (
            ["U", "shift+tab", "enter"],
            "kraft admin update --restart",
            ["kraft admin update --restart", "admin update --restart", "exit 0"],
        ),
        (
            ["shift+u", "shift+tab", "enter"],
            "kraft admin update --restart",
            ["kraft admin update --restart", "admin update --restart", "exit 0"],
        ),
    ],
    ids=[
        "d-doctor",
        "R-restarts-once-told",
        "shift-r-asks-and-esc-runs-nothing",
        "U-updates-and-restarts",
        "shift-u-updates-and-restarts",
    ],
)
def test_the_servers_keys_run_their_verbs_after_the_consoles_confirm(
    console, kraft_is, monkeypatch, keys, asked, ran
):
    """`d` runs doctor at once; `R` and `U` ask first, listing the running
    items their restart ends (spec §7.3, §7.8), and `U` is never passed `-y`.
    The child is an echo: no server restarts and nothing is installed."""
    kraft_is(ECHO)
    _quiet(monkeypatch)

    async def list_work_items(status=None, **kwargs):
        return ACTIVE

    monkeypatch.setattr(client, "list_work_items", list_work_items)

    async def scenario(app, pilot):
        await pilot.press("2", keys[0])
        question = None
        if asked:
            await console.until(pilot, lambda: isinstance(app.screen, Confirm))
            await pilot.pause()
            listing = str(app.screen.query_one("#listing").render())
            question = (
                str(app.screen.query_one("#question").render()),
                "Fix the [/] parser" in listing,
                str(app.screen.query_one("#yes", Button).label),
            )
            await pilot.press(*keys[1:])
            await console.until(pilot, lambda: not isinstance(app.screen, Confirm))
        if ran:
            await console.until(pilot, lambda: console.output(app)[-1:] == ["exit 0"])
        await pilot.pause()
        return question, console.output(app)

    # The button that goes on names the verb (`runner.CONFIRMS`), never "Go on".
    verb = {"kraft admin restart": "Restart", "kraft admin update --restart": "Update"}
    assert console.run(scenario) == ((asked, True, verb[asked]) if asked else None, ran)


@pytest.mark.parametrize(
    ("answer", "toast", "error"),
    [
        (
            {
                "valid": ["a", "b"],
                "invalid_templates": {},
                "refused_policy": None,
                "refused_intake": None,
            },
            "reloaded 2 template(s) and policy.yaml and intake.yaml",
            False,
        ),
        (
            {
                "valid": ["a"],
                "invalid_templates": {"chain x": "no [/] node"},
                "refused_policy": "bad",
                "refused_intake": None,
            },
            "reloaded 1 template(s) and intake.yaml\n  invalid: chain x: no [/] node\n"
            "  refused: policy.yaml: bad (the running policy is kept)",
            True,
        ),
        (
            client.status_error(422, "the server refused the reload [/]"),
            "422: the server refused the reload [/]",
            True,
        ),
    ],
    ids=["all-taken", "some-refused", "refused-by-the-server"],
)
def test_l_reloads_and_says_what_the_cli_says(console, monkeypatch, answer, toast, error):
    """`l` is `kraft admin reload` read here: the CLI's sentence, from
    `kraft.answers`, in the error colour when it left anything refused."""
    _quiet(monkeypatch)

    async def reload_templates():
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(client, "reload_templates", reload_templates)

    async def scenario(app, pilot):
        await pilot.press("2", "l")
        await console.until(pilot, lambda: app.screen.query("Toast"))
        shown = app.screen.query("Toast").first()
        return str(shown.render()), shown.has_class("-error")

    assert console.run(scenario, notifications=True) == (toast, error)


@pytest.mark.parametrize(
    ("installed", "banner"),
    [("0.0.1", False), ("0.0.2", True)],
    ids=["same-version", "another-version"],
)
def test_an_update_that_installed_another_kraft_says_restart_the_console(
    console, kraft_is, monkeypatch, installed, banner
):
    """Spec §7.3: the console runs the modules it started on, so a new install
    is said until it restarts, and `updated` closes add-on install for PR 6.
    The update itself is an echo."""
    kraft_is(ECHO)
    calls = _quiet(monkeypatch)

    async def no_one_running(status=None, **kwargs):
        return []

    monkeypatch.setattr(client, "list_work_items", no_one_running)

    async def scenario(app, pilot):
        await console.until(pilot, lambda: calls)
        before = len(calls)
        app.version = "0.0.1"  # what this console started on
        monkeypatch.setattr(update, "installed", lambda: installed)
        await pilot.press("2", "U")
        await console.until(pilot, lambda: isinstance(app.screen, Confirm))
        await pilot.press("shift+tab", "enter")
        await console.until(pilot, lambda: console.output(app)[-1:] == ["exit 0"])
        await pilot.press("escape")
        await pilot.pause()
        await console.until(pilot, lambda: len(calls) > before)  # "update available" may be over
        return app.updated, app.query_one("#updated").display

    assert console.run(scenario) == (banner, banner)


def test_a_hidden_server_draws_nothing(console, monkeypatch):
    """A hidden view starts no work: a health answer while another view is
    shown is kept, not drawn, and the shown view draws it."""
    _quiet(monkeypatch)

    async def scenario(app, pilot):
        app._live_health(HEALTH)
        await pilot.pause()
        hidden = _text(app, "#server-summary")
        await pilot.press("2")
        await pilot.pause()
        return hidden, _text(app, "#server-summary").startswith("Server ● ok")

    assert console.run(scenario) == ("", True)


def _log(app) -> list[str]:
    return [line.text.rstrip() for line in app.query_one("#server-log", RichLog).lines]


def _write(*lines: str) -> None:
    """Lines the server wrote to its `server.log`, in the test's run dir."""
    path = tail.server_log()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as log:
        log.write("".join(f"{line}\n" for line in lines))


def test_the_log_follows_until_scrolled_up_and_end_follows_again(console, monkeypatch):
    """Spec §7.3: a line moves the log to its end only while it is at its end,
    so a reader scrolled up stays where they are; End follows again."""
    monkeypatch.setattr(tail, "POLL_S", 0.01)
    _quiet(monkeypatch)
    _write(*(f"line {n} [/]" for n in range(60)))

    async def scenario(app, pilot):
        await pilot.press("2")
        await console.until(pilot, lambda: _log(app)[-1:] == ["line 59 [/]"])
        log = app.query_one("#server-log", RichLog)
        following = _text(app, "#server-rule")
        await pilot.press("pageup")
        await console.until(pilot, lambda: "End follows" in _text(app, "#server-rule"))
        held_at = log.scroll_y
        _write("late")
        await console.until(pilot, lambda: _log(app)[-1:] == ["late"])
        await pilot.pause()
        stayed = log.scroll_y == held_at
        await pilot.press("end")
        await console.until(pilot, lambda: "following" in _text(app, "#server-rule"))
        _write("later")
        await console.until(pilot, lambda: _log(app)[-1:] == ["later"])
        await pilot.pause()
        return following, stayed, log.is_vertical_scroll_end

    assert console.run(scenario) == ("server.log · following · f filter", True, True)


def test_f_filters_the_log_and_esc_brings_every_line_back(console, monkeypatch):
    """`f` keeps the lines holding the text, any case, as they come too;
    Esc clears it, shows every kept line again and gives the keys back."""
    monkeypatch.setattr(tail, "POLL_S", 0.01)
    _quiet(monkeypatch)
    _write("boot ok", "error: disk [/]", "listening", "Error again")

    async def scenario(app, pilot):
        await pilot.press("2")
        await console.until(pilot, lambda: len(_log(app)) == 4)
        await pilot.press("f", *"error")
        await console.until(pilot, lambda: len(_log(app)) == 2)
        filtered, rule = _log(app), _text(app, "#server-rule")
        _write("error late", "fine late")
        await console.until(pilot, lambda: "fine late" in app.view.lines)
        late = _log(app)
        await pilot.press("escape")
        await console.until(pilot, lambda: len(_log(app)) == 6)
        return filtered, rule, late, _log(app)[-1], app.focused is app.view

    assert console.run(scenario) == (
        ["error: disk [/]", "Error again"],
        "server.log · filter: error · following · f filter",
        ["error: disk [/]", "Error again", "error late"],
        "fine late",
        True,
    )


def test_a_hidden_server_reads_no_log(console, monkeypatch):
    """The view stays mounted behind the others: hidden, it stops following
    `server.log`; shown again, it starts over from the last lines."""
    monkeypatch.setattr(tail, "POLL_S", 0.01)
    _quiet(monkeypatch)
    _write("first")

    def following(app) -> bool:
        return any(w.group == "tail" and w.is_running for w in app.workers)

    async def scenario(app, pilot):
        await pilot.press("2")
        await console.until(pilot, lambda: following(app) and _log(app) == ["first"])
        await pilot.press("1")
        await pilot.pause()
        hidden = following(app)
        _write("second")
        await pilot.press("2")
        await console.until(pilot, lambda: _log(app) == ["first", "second"])
        return hidden

    assert console.run(scenario) is False


def test_a_hidden_server_follows_nothing(console, monkeypatch):
    """`_follow` asked to run while the view is hidden (the refresh it waited
    for came after another view was shown) opens no tail; shown, it starts over."""
    _quiet(monkeypatch)
    opened = []

    async def follow(path, *, backlog=0):
        opened.append(backlog)
        return
        yield

    monkeypatch.setattr(tail, "follow", follow)

    async def scenario(app, pilot):
        server = app.query_one("#server")
        await server._follow()
        hidden = list(opened)
        await pilot.press("2")
        await console.until(pilot, lambda: opened)
        return hidden, opened[0]

    assert console.run(scenario) == ([], 200)


def test_a_hiding_server_lets_go_of_a_feed_read(console, monkeypatch):
    """Hidden, the view reads nothing: a feed read still waiting is cancelled,
    so its answer, when it comes, is not kept."""
    _quiet(monkeypatch)
    answer = asyncio.Event()
    calls = []

    async def update_available(channel=None):
        calls.append(None)
        if len(calls) == 1:
            raise FEED_DOWN
        await answer.wait()
        return RELEASE

    monkeypatch.setattr(client, "update_available", update_available)

    async def scenario(app, pilot):
        await console.until(pilot, lambda: calls)
        await pilot.press("2")
        await console.until(pilot, lambda: _reading_release(app))
        await pilot.press("1")
        await pilot.pause()
        answer.set()
        await pilot.pause()
        return app.release

    assert console.run(scenario) is None


@pytest.mark.parametrize(
    "unreadable",
    [FEED_DOWN, {"installed": "2.4.0", "latest": None, "behind": None}],
    ids=["failed", "behind-null"],
)
def test_a_feed_the_server_could_not_read_is_not_an_answer(console, monkeypatch, unreadable):
    """`/update` answers 200 with `behind: null` when the feed was down; that
    is a failed read, so the next show asks again."""
    calls = _quiet(monkeypatch, [unreadable, RELEASE])

    async def scenario(app, pilot):
        await console.until(pilot, lambda: calls)
        await pilot.pause()
        unread = app.release, app.update_waiting
        await pilot.press("2")
        await console.until(pilot, lambda: app.release is not None)
        return unread, app.release

    assert console.run(scenario) == ((None, False), RELEASE)


def test_a_release_that_arrives_while_the_server_is_shown_is_drawn_at_once(console, monkeypatch):
    """The read at start can land after the person opened the Server: the
    summary says so then, not at the next health poll."""
    _quiet(monkeypatch)
    answer = asyncio.Event()
    calls = []

    async def update_available(channel=None):
        calls.append(None)
        if len(calls) == 1:
            await answer.wait()
            return RELEASE
        raise FEED_DOWN

    monkeypatch.setattr(client, "update_available", update_available)

    async def scenario(app, pilot):
        await console.until(pilot, lambda: calls)
        app._live_health(HEALTH)
        await pilot.press("2")
        await console.until(pilot, lambda: len(calls) == 2 and not _reading_release(app))
        before = "available" in _text(app, "#server-summary")
        answer.set()
        await console.until(pilot, lambda: "(2.5.0 available)" in _text(app, "#server-summary"))
        return before

    assert console.run(scenario) is False


def test_a_filter_key_leaves_a_scrolled_up_reader_where_they_are(console, monkeypatch):
    """Typing the filter redraws the log: a reader who scrolled up keeps the
    offset they scrolled to, and one who follows keeps following."""
    monkeypatch.setattr(tail, "POLL_S", 0.01)
    _quiet(monkeypatch)
    _write(*(f"line {n}" for n in range(60)))

    async def scenario(app, pilot):
        await pilot.press("2")
        log = app.query_one("#server-log", RichLog)
        await console.until(pilot, lambda: len(_log(app)) == 60)
        await pilot.press("f", "l")
        await console.until(pilot, lambda: app.view.filter_text == "l")
        await console.until(pilot, lambda: log.is_vertical_scroll_end)
        await pilot.pause()
        followed = log.is_vertical_scroll_end
        await pilot.press("escape", "pageup")
        await console.until(pilot, lambda: "End follows" in _text(app, "#server-rule"))
        at = log.scroll_y
        await pilot.press("f", "l")
        await console.until(pilot, lambda: app.view.filter_text == "l")
        await pilot.pause()
        return followed, log.scroll_y == at, "End follows" in _text(app, "#server-rule")

    assert console.run(scenario) == (True, True, True)


def test_a_failed_read_after_an_update_forgets_the_old_release(console, kraft_is, monkeypatch):
    """The release read before the update says "update available"; when the
    read after it fails, that is no longer known, so the Server asks again."""
    kraft_is(ECHO)
    calls = _quiet(monkeypatch, [RELEASE, FEED_DOWN])

    async def no_one_running(status=None, **kwargs):
        return []

    monkeypatch.setattr(client, "list_work_items", no_one_running)

    async def scenario(app, pilot):
        await console.until(pilot, lambda: app.release is not None)
        await pilot.press("2", "U")
        await console.until(pilot, lambda: isinstance(app.screen, Confirm))
        await pilot.press("shift+tab", "enter")
        await console.until(pilot, lambda: len(calls) == 2)
        await console.until(pilot, lambda: console.output(app)[-1:] == ["exit 0"])
        await pilot.press("escape")
        await pilot.pause()
        return app.release, app.update_waiting

    assert console.run(scenario) == (None, False)
