"""The Server view (spec §7.3): how the server is, `server.log` under it, and
the keys that reload the config here or run doctor, restart and update in the
output panel. Nothing here reaches a real server or installs anything: the
view is handed its health, the release feed and the reload are faked at
`kraft.client`, and every `kraft` child is a `python -c` echo (`kraft_is`)."""

from __future__ import annotations

import asyncio

import pytest
from support.console_board import _text

from kraft import client, update
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
    ("healths", "releases", "lines"),
    [
        ([HEALTH], [RELEASE], OK_LINES),
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
        ),
        (
            [HELD],
            [FEED_DOWN, RELEASE],
            [*OK_LINES[:2], "Storage  3.1G of 20G · held  ·  Add-ons  1 update waiting"],
        ),
        ([HEALTH, None], [RELEASE], [OK_LINES[0].replace("● ok", "● no answer"), *OK_LINES[1:]]),
    ],
    ids=["ok", "degraded", "held-and-the-feed-read-again", "no-answer-keeps-the-last"],
)
def test_the_summary_says_how_the_server_is(console, monkeypatch, healths, releases, lines):
    """Spec §7.3: from `/health` and the release feed, each reason it is
    degraded spelled out in the server's words, `[/]` and all. A feed that
    failed at start is read again when the view is shown; a server that stops
    answering leaves its last answer on screen, saying so (spec §8)."""
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
        return _text(app, "#server-summary").split("\n")

    assert console.run(scenario) == lines


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
    ],
    ids=[
        "d-doctor",
        "R-restarts-once-told",
        "shift-r-asks-and-esc-runs-nothing",
        "U-updates-and-restarts",
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
            )
            await pilot.press(*keys[1:])
            await console.until(pilot, lambda: not isinstance(app.screen, Confirm))
        if ran:
            await console.until(pilot, lambda: console.output(app)[-1:] == ["exit 0"])
        await pilot.pause()
        return question, console.output(app)

    assert console.run(scenario) == ((asked, True) if asked else None, ran)


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
    _quiet(monkeypatch)

    async def no_one_running(status=None, **kwargs):
        return []

    monkeypatch.setattr(client, "list_work_items", no_one_running)

    async def scenario(app, pilot):
        app.version = "0.0.1"  # what this console started on
        monkeypatch.setattr(update, "installed", lambda: installed)
        await pilot.press("2", "U")
        await console.until(pilot, lambda: isinstance(app.screen, Confirm))
        await pilot.press("shift+tab", "enter")
        await console.until(pilot, lambda: console.output(app)[-1:] == ["exit 0"])
        await pilot.press("escape")
        await pilot.pause()
        return app.updated, app.query_one("#updated").display

    assert console.run(scenario) == (banner, banner)


def test_a_hidden_server_draws_nothing(console, monkeypatch):
    """A hidden view starts no work: a health answer while another view is
    shown is kept, not drawn, and shown view draws it."""
    _quiet(monkeypatch)

    async def scenario(app, pilot):
        app._live_health(HEALTH)
        await pilot.pause()
        hidden = _text(app, "#server-summary")
        await pilot.press("2")
        await pilot.pause()
        return hidden, _text(app, "#server-summary").startswith("Server ● ok")

    assert console.run(scenario) == ("", True)
