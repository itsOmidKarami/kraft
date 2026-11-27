"""The remote Server verbs (spec §7.3): `R` restarts and `U` updates a
`--server` console through the API, never a `kraft` child on this machine.
Split out of `test_server.py`, which the 800-line budget no longer holds with
these here. The health poll and the release feed are faked at `kraft.client`
(`_quiet`), and every `kraft` child is a `python -c` echo (`kraft_is`)."""

from __future__ import annotations

import pytest
from textual.widgets import Button

from console.test_server import ACTIVE, RELEASE, UP_TO_DATE, _quiet
from kraft import client
from kraft.console import tail
from kraft.console.dialogs import Confirm

#: The release feed answered and a release is already marked on the server.
MARKED = {**RELEASE, "marked": {"tag": "v2.5.0", "by": "you"}}
#: No newer release inside the installed major, so the newer major over all is
#: all there is to mark.
MAJOR_ONLY = {"installed": "2.4.0", "latest": "v3.0.0", "latest_in_major": "v2.4.0", "behind": True}
#: A release inside the installed major sits beside the newer major: the web UI
#: offers this one first (updateWords.ts `offers()`), and so does `U`.
IN_MAJOR = {"installed": "2.4.0", "latest": "v3.0.0", "latest_in_major": "v2.5.0", "behind": True}
#: The release feed did not answer: not behind, and not up to date either.
NO_ANSWER = {"installed": "2.4.0", "latest": None, "behind": None}
#: What the server answers when it refuses the mark.
REFUSED = client.ClientError("v2.5.0: the mark is not allowed here", kind=client.Failure.REFUSED)
#: What the confirm says about the release a row marks: the sentence it opens
#: with, plus a line for a major version when the row crosses one.
STOCK = ("This installs the newest Kraft release.",)
IN_MAJOR_WORDS = ("This installs v2.5.0, the newest release in the installed major.",)
MAJOR_WORDS = ("This installs the newest Kraft release.", "v3.0.0 is a new major version.")


@pytest.mark.parametrize(
    ("answer", "toast"),
    [
        ({"restarting": True}, "Restarting the server"),
        (
            client.status_error(409, "started from a terminal: restart it there"),
            "409: started from a terminal: restart it there",
        ),
    ],
    ids=["restarts", "409-as-given"],
)
def test_a_remote_restart_asks_then_calls_the_api(
    console, monkeypatch, remote_server, kraft_is, answer, toast
):
    kraft_is("print('CHILD')")
    _quiet(monkeypatch)

    async def active_items():
        return ACTIVE

    async def restart_server():
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(client, "active_items", active_items)
    monkeypatch.setattr(client, "restart_server", restart_server)

    async def scenario(app, pilot):
        await pilot.press("2", "R")
        await console.until(pilot, lambda: isinstance(app.screen, Confirm))
        await pilot.press("shift+tab", "enter")
        await console.until(pilot, lambda: not isinstance(app.screen, Confirm))
        await pilot.pause()
        return [n.message for n in app._notifications], console.output(app)

    toasts, out = console.run(scenario, notifications=True)
    assert any(toast in t for t in toasts) and "CHILD" not in out


@pytest.mark.parametrize(
    ("release", "keys", "asked", "yes", "called", "toast", "want", "refuse"),
    [
        (
            RELEASE,
            ["shift+tab", "enter"],
            "Install v2.5.0 on the server when it is free?",
            "Update",
            [("mark", "v2.5.0", None)],
            "v2.5.0 will be installed on the server when it is free",
            STOCK,
            None,
        ),
        (
            RELEASE,
            ["escape"],
            "Install v2.5.0 on the server when it is free?",
            "Update",
            [],
            None,
            STOCK,
            None,
        ),
        (UP_TO_DATE, [], None, None, [], "kraft 2.4.0 is up to date", (), None),
        (
            MARKED,
            ["shift+tab", "enter"],
            "Cancel the marked install of v2.5.0?",
            "Cancel it",
            [("unmark",)],
            "v2.5.0 will not be installed",
            ("v2.5.0 would be installed on the server when Kraft is free.",),
            None,
        ),
        (
            IN_MAJOR,
            ["shift+tab", "enter"],
            "Install v2.5.0 on the server when it is free?",
            "Update",
            [("mark", "v2.5.0", None)],
            "v2.5.0 will be installed on the server when it is free",
            IN_MAJOR_WORDS,
            None,
        ),
        (
            MAJOR_ONLY,
            ["shift+tab", "enter"],
            "Install v3.0.0 on the server when it is free?",
            "Update",
            [("mark", "v3.0.0", None)],
            "v3.0.0 will be installed on the server when it is free",
            MAJOR_WORDS,
            None,
        ),
        (NO_ANSWER, [], None, None, [], "The release feed did not answer", (), None),
        (
            RELEASE,
            ["shift+tab", "enter"],
            "Install v2.5.0 on the server when it is free?",
            "Update",
            [("mark", "v2.5.0", None)],
            "v2.5.0: the mark is not allowed here",
            STOCK,
            REFUSED,
        ),
    ],
    ids=[
        "behind-and-confirmed-marks-the-tag",
        "behind-and-refused-calls-nothing",
        "up-to-date-calls-nothing",
        "marked-and-confirmed-drops-the-mark",
        "newer-in-the-installed-major-marks-that-one",
        "only-a-newer-major-marks-it-and-says-so",
        "no-answer-from-the-feed-says-so-and-calls-nothing",
        "a-refused-mark-is-shown-as-given",
    ],
)
def test_a_remote_update_marks_the_release_or_takes_the_mark_back(
    console,
    monkeypatch,
    remote_server,
    kraft_is,
    release,
    keys,
    asked,
    yes,
    called,
    toast,
    want,
    refuse,
):
    """`U` in a console with `--server` goes through the API as the web UI
    does: behind, it asks and marks the newest release inside the installed
    major -- only a newer major is marked when nothing newer exists inside it,
    and the confirm says so; refused, it marks nothing; a call the server
    refuses is shown in its words; up to date it says so with no call; a
    release already marked is offered back. No `kraft` child starts here, so
    nothing installs on this machine."""
    kraft_is("print('CHILD')")
    _quiet(monkeypatch, [release])
    calls: list[tuple] = []

    async def mark_update(tag, channel=None):
        calls.append(("mark", tag, channel))
        if refuse is not None:
            raise refuse
        return {"blocked": []}

    async def unmark_update():
        calls.append(("unmark",))
        return {}

    monkeypatch.setattr(client, "mark_update", mark_update)
    monkeypatch.setattr(client, "unmark_update", unmark_update)

    async def scenario(app, pilot):
        await pilot.press("2", "U")
        if asked:
            await console.until(pilot, lambda: isinstance(app.screen, Confirm))
            await console.until(
                pilot, lambda: asked in str(app.screen.query_one("#question").render())
            )
            assert str(app.screen.query_one("#yes", Button).label) == yes
            listing = str(app.screen.query_one("#listing").render())
            assert all(line in listing for line in want)
            assert ("is a new major version." in listing) == any(
                "new major version" in word for word in want
            )
            await pilot.press(*keys)
            await console.until(pilot, lambda: not isinstance(app.screen, Confirm))
        if toast:
            await console.until(pilot, lambda: any(toast in n.message for n in app._notifications))
        await pilot.pause()
        return [n.message for n in app._notifications], list(calls), console.output(app)

    toasts, made, out = console.run(scenario, notifications=True)
    assert made == called and (not toast or any(toast in t for t in toasts))
    assert "CHILD" not in out


def test_a_remote_server_screen_offers_update_but_hides_doctor_and_the_log(
    console, monkeypatch, remote_server
):
    """`U` goes through the API, so it is offered; `d` would run `kraft` here
    and the log, its filter and its scrolling are the server's disk."""
    _quiet(monkeypatch)
    followed = []

    async def follow(path, *, backlog=0):
        followed.append(backlog)
        return
        yield

    monkeypatch.setattr(tail, "follow", follow)

    async def scenario(app, pilot):
        await pilot.press("2")
        await pilot.pause()
        view = app.query_one("#server")
        keys = {label for _, label in app.screen_keys()}
        return (
            keys >= {"reload", "restart", "update"},
            keys & {"doctor", "filter"},
            view.query_one("#server-log").display,
            followed,
        )

    assert console.run(scenario) == (True, set(), False, [])
