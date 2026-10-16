"""The Item's tabs (spec §6, §7.2): `[` and `]`, and the Activity."""

from __future__ import annotations

import asyncio

import pytest
from support.console_board import GATE, RUNNING, _detail, _iso, _opened, _overview, _serve, _settled
from textual.widgets import RichLog

from kraft import client, render
from kraft.console.screens import item as item_view


def _event(seq: int, kind: str, minutes_ago: float, **payload) -> dict:
    return {
        "seq": seq,
        "work_item_id": GATE["id"],
        "type": kind,
        "node_id": payload.pop("node", None),
        "payload": payload,
        "created_at": _iso(minutes_ago),
    }


HISTORY = [
    _event(1, "work_item_created", 90),
    _event(2, "plan_progress", 80),
    _event(3, "node_started", 70, node="spec"),
    _event(4, "gate_requested", 65, gate="spec_approval"),
    _event(5, "gate_rejected", 12, gate="spec_approval", note="vague [/]"),
]


def _serve_events(monkeypatch, history: list[dict], hold=None) -> list[dict]:
    """`client.events` over `history`, as the server pages it; returns the reads.
    `hold` maps a `before_seq` to an Event its read waits for."""
    reads: list[dict] = []

    async def events(work_item_id=None, after_seq=0, *, limit=None, before_seq=None):
        if before_seq in (hold or {}):
            await hold[before_seq].wait()
        reads.append({"after_seq": after_seq, "limit": limit, "before_seq": before_seq})
        if before_seq is not None:
            return [e for e in history if e["seq"] < before_seq][-limit:]
        return [e for e in history if e["seq"] > after_seq][:limit]

    monkeypatch.setattr(client, "events", events)
    return reads


def _activity(app) -> list[str]:
    return [line.text.rstrip() for line in app.query_one("#activity", RichLog).lines]


def test_brackets_step_through_the_tabs_and_a_shows_the_overview_first(board, monkeypatch):
    """Spec §6: `[` and `]` step through the tabs, round the end; `a` on
    another tab shows the Overview and approves nothing until pressed there."""
    calls: list[tuple] = []
    _serve(monkeypatch, _detail(GATE), calls=calls)
    _serve_events(monkeypatch, HISTORY)
    console = board([GATE])

    async def scenario(app, pilot):
        await _opened(console, app, pilot, GATE["id"])
        seen = []
        for key in ("right_square_bracket", "left_square_bracket", "left_square_bracket"):
            await pilot.press(key)
            seen.append(app.view.tab)
        await pilot.press("a")
        seen.append((app.view.tab, list(calls)))
        await pilot.press("a")
        await console.until(pilot, lambda: calls)
        return seen, calls

    seen, calls = console.run(scenario)
    # Round the start to the last tab.
    assert seen == ["activity", "overview", item_view.TABS[-1][0], ("overview", [])]
    assert calls == [("approve_gate", ("spec_approval", "a91c03fe", "d1g3st"), {})]


def test_the_activity_reads_newest_first_and_end_reads_earlier(board, monkeypatch):
    """Spec §7.2: the item's events in the web's words, newest first and
    paged: bookkeeping left out, `[/]` as typed, End at the bottom reads the
    page before."""
    _serve(monkeypatch, _detail(GATE), calls=[])
    reads = _serve_events(monkeypatch, HISTORY)
    monkeypatch.setattr(item_view, "PAGE", 3)
    console = board([GATE])

    async def scenario(app, pilot):
        await _opened(console, app, pilot, GATE["id"])
        await pilot.press("right_square_bracket")
        await console.until(pilot, lambda: _activity(app))
        first = _activity(app)
        await pilot.press("end")
        await console.until(pilot, lambda: len(reads) == 2)
        await console.until(pilot, lambda: "filed" in " ".join(_activity(app)))
        return first, _activity(app), reads

    first, then, reads = console.run(scenario)
    assert first == [
        "    12m  spec_approval · rejected: vague [/]",
        " 1h 05m  spec_approval is waiting for you",
        " 1h 10m  spec started",
        "End loads earlier events.",
    ]
    assert then == [*first[:3], " 1h 30m  filed"]
    assert reads == [
        {"after_seq": 0, "limit": 3, "before_seq": item_view.NEWEST},
        {"after_seq": 0, "limit": 3, "before_seq": 3},
    ]


@pytest.mark.parametrize(
    ("event", "reads", "top"),
    [
        (_event(6, "node_completed", 0, node="spec"), 2, "    now  spec finished"),
        (_event(6, "node_started", 0, node="x") | {"work_item_id": "elsewhere"}, 1, None),
    ],
    ids=["its-own", "another-items"],
)
def test_a_live_event_adds_its_line_to_the_activity(board, monkeypatch, event, reads, top):
    """A live event about this item reads only what came after the newest
    line; another item's reads nothing."""
    _serve(monkeypatch, _detail(GATE), calls=[])
    history = list(HISTORY)
    got = _serve_events(monkeypatch, history)
    console = board([GATE])

    async def scenario(app, pilot):
        await _opened(console, app, pilot, GATE["id"])
        await pilot.press("right_square_bracket")
        await console.until(pilot, lambda: _activity(app))
        history.append(event)
        app.view.rows_changed([GATE], [event])
        await console.until(pilot, lambda: len(got) == reads)
        await pilot.pause()
        return [r["after_seq"] for r in got], _activity(app)[0]

    after, first = console.run(scenario)
    assert after == [0, 5][:reads]
    assert first == (top or "    12m  spec_approval · rejected: vague [/]")


SESSIONS = [
    {"id": "s1", "node_id": "implement", "hook_point": "implement.main.author", "status": "done"},
    {
        "id": "s2",
        "node_id": "verification",
        "hook_point": "verification.review.code_review",
        "status": "running",
    },
]
LONG = "reviewing [/] " + "word " * 20


def _log(app) -> list[str]:
    return [line.text.rstrip() for line in app.query_one("#log", RichLog).lines]


def test_the_log_follows_the_newest_session_and_s_steps_to_older_ones(board, monkeypatch):
    """Spec §7.2: the newest session, followed to its end; `s` the next older
    one, offered only with more than one. A long line wraps at the window."""
    _serve(monkeypatch, _detail(RUNNING, worker_sessions=SESSIONS), calls=[])
    _serve_events(monkeypatch, [])

    async def stream_log(work_item_id, session_id, after_line=0):
        yield {"n": 0, "text": LONG.strip() if session_id == "s2" else "wrote code"}

    monkeypatch.setattr(client, "stream_log", stream_log)
    console = board([RUNNING])

    async def scenario(app, pilot):
        await _opened(console, app, pilot, RUNNING["id"])
        offered = app.view.check_action("session", ())
        await pilot.press("right_square_bracket", "right_square_bracket")
        await console.until(pilot, lambda: _log(app)[-1:] == ["session ended"])
        newest = _log(app)
        await pilot.press("s")
        await console.until(pilot, lambda: "wrote code" in _log(app))
        await console.until(pilot, lambda: _log(app)[-1:] == ["session ended"])
        older = _log(app)
        await pilot.press("s")  # at the oldest: round to the newest
        await console.until(pilot, lambda: _log(app)[0].startswith("verification"))
        await console.until(pilot, lambda: _log(app)[-1:] == ["session ended"])
        return offered, app.view.check_action("session", ()), newest, older, _log(app) == newest

    offered, on_log, newest, older, round_again = console.run(scenario, size=(60, 30))
    assert round_again
    assert (offered, on_log) == (False, True)
    head = "verification.review.code_review · running · session 1 of 2"
    assert " ".join(" ".join(newest[:-1]).split()) == f"{head} {LONG.strip()}"
    assert max(len(line) for line in newest) <= 60
    assert older == ["implement.main.author · done · session 2 of 2", "wrote code", "session ended"]


def test_a_log_nobody_sees_is_not_followed(board, monkeypatch):
    """Leaving the Log tab, or the Item, stops following the session."""
    _serve(monkeypatch, _detail(RUNNING, worker_sessions=SESSIONS[1:]), calls=[])
    _serve_events(monkeypatch, [])

    async def stream_log(work_item_id, session_id, after_line=0):
        yield {"n": 0, "text": "first"}
        await asyncio.Event().wait()  # a session that never ends
        yield {}

    monkeypatch.setattr(client, "stream_log", stream_log)
    console = board([RUNNING])

    def live(app) -> list:
        return [w for w in app.view.workers if w.group == "log" and not w.is_finished]

    async def scenario(app, pilot):
        await _opened(console, app, pilot, RUNNING["id"])
        seen = []
        for leave in (["left_square_bracket"], ["1"]):  # another tab; the Board
            app.view.show_tab("log")
            await console.until(pilot, lambda: live(app) and "first" in _log(app))
            await pilot.press(*leave)
            await pilot.pause()
            seen.append(live(app))
            app.show("item")
            await pilot.pause()
        return seen

    assert console.run(scenario) == [[], []]


WARN = "warning"
LANDED = "diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old [/]\n+new [/]"
TRAILER = {
    "untracked": ["notes.txt"],
    "truncated": True,
    "diff_max_bytes": 100,
    "worktree_path": "/w",
}


def _diff(app) -> list[tuple[str, str]]:
    """Each line of the Diff, and the colour of its first character."""
    out = []
    for line in app.query_one("#diff", RichLog).lines:
        colour = next((seg.style.color for seg in line if seg.text.strip() and seg.style), None)
        out.append((line.text.rstrip(), colour.name if colour else ""))
    return out


@pytest.mark.parametrize(
    ("payload", "lines"),
    [
        (
            {"base_ref": "main", "landed": {"commits": ["c1", "c2"], "diff": LANDED}, "diff": ""}
            | TRAILER,
            [
                (render.landed_head({"landed": {"commits": ["c1", "c2"]}}), "muted"),
                ("diff --git a/x b/x", ""),
                ("--- a/x", ""),
                ("+++ b/x", ""),
                ("@@ -1 +1 @@", "info"),
                ("-old [/]", "error"),
                ("+new [/]", "success"),
                ("", ""),
                (render.UNCOMMITTED_HEAD, "muted"),
                (render.NOTHING_UNCOMMITTED, "muted"),
                ("", ""),
                ("untracked (not in the diff above):", "muted"),
                ("  notes.txt", ""),
                ("", ""),
                # Wrapped at the window's width, as every long line is.
                (
                    "WARNING: diff truncated by the server at 100 bytes — this is not the whole",
                    WARN,
                ),
                ("change. Read the rest in /w.", WARN),
            ],
        ),
        ({"base_ref": "main", "diff": "+only"}, [("+only", "success")]),
        ({"base_ref": "main", "diff": ""}, [(render.NO_CHANGES, "muted")]),
        ({"base_ref": None}, [(render.NO_BASELINE, "muted")]),
        (
            client.status_error(409, "the worktree is gone [/]"),
            [(client.reason_of(str(client.status_error(409, "the worktree is gone [/]"))), "")],
        ),
    ],
    ids=["landed-uncommitted-trailer", "uncommitted-only", "nothing", "no-baseline", "gone"],
)
def test_the_diff_shows_what_landed_then_what_did_not_then_its_trailer(
    board, monkeypatch, payload, lines
):
    """Spec §7.2 and §4.3: the landed head and the CLI's own trailer words,
    built as Rich text in the web's add and delete colours; `[/]` as typed;
    a worktree that is gone is the server's own error."""
    _serve(monkeypatch, _detail(RUNNING), calls=[])
    _serve_events(monkeypatch, [])

    async def diff(work_item_id=None, ignore_whitespace=False):
        if isinstance(payload, Exception):
            raise payload
        return payload

    monkeypatch.setattr(client, "diff", diff)
    console = board([RUNNING])

    async def scenario(app, pilot):
        await _opened(console, app, pilot, RUNNING["id"])
        await pilot.press("left_square_bracket")
        await console.until(pilot, lambda: len(_diff(app)) >= len(lines))
        names = {
            app.theme_variables[k].lower(): k.removeprefix("kraft-")
            for k in ("kraft-muted", "kraft-info", "success", "error", "warning")
        }
        return [(text, names.get(colour.lower(), colour)) for text, colour in _diff(app)]

    assert console.run(scenario) == lines


def test_the_diff_reads_again_only_when_shown_and_its_row_changed(board, monkeypatch):
    """A change to the item's row while the Diff is shown reads it again; the
    same row, or any change while the Item is hidden, reads nothing."""
    _serve(monkeypatch, _detail(RUNNING), calls=[])
    _serve_events(monkeypatch, [])
    reads: list[str] = []

    async def diff(work_item_id=None, ignore_whitespace=False):
        reads.append(work_item_id)
        return {"base_ref": "main", "diff": "+x"}

    monkeypatch.setattr(client, "diff", diff)
    console = board([RUNNING])
    moved = RUNNING | {"updated_at": "2026-10-16T00:00:00+00:00"}

    async def scenario(app, pilot):
        await _opened(console, app, pilot, RUNNING["id"])
        view = app.view
        await pilot.press("left_square_bracket")  # round to the Diff
        await console.until(pilot, lambda: reads)
        view.rows_changed([RUNNING], [{"seq": 1}])
        await pilot.pause()
        same = len(reads)
        app.show("server")
        view.rows_changed([moved], [{"seq": 2}])
        await pilot.pause()
        hidden = len(reads)
        app.show("item")
        view.rows_changed([moved], [{"seq": 3}])
        await console.until(pilot, lambda: len(reads) == 2)
        return same, hidden

    assert console.run(scenario) == (1, 1)


def _paged_back(board, monkeypatch, then):
    """Two events a page, a read of the page before seq 5 held, a live event 7,
    and `then` done to the Item before the held read is released; returns the
    seqs the Activity ends up holding."""
    _serve(monkeypatch, _detail(GATE), calls=[])
    history = [_event(n, "node_started", 10 - n, node=f"n{n}") for n in range(1, 7)]
    release = asyncio.Event()
    _serve_events(monkeypatch, history, hold={5: release})
    monkeypatch.setattr(item_view, "PAGE", 2)
    console = board([GATE])

    async def scenario(app, pilot):
        await _opened(console, app, pilot, GATE["id"])
        await pilot.press("right_square_bracket")
        await console.until(pilot, lambda: [e["seq"] for e in app.view.events] == [6, 5])
        await pilot.press("end")  # asks for the page before seq 5, which is held
        await pilot.pause()
        live = _event(7, "node_started", 0, node="n7")
        history.append(live)
        app.view.rows_changed([GATE], [live])
        await console.until(pilot, lambda: [e["seq"] for e in app.view.events][:1] == [7])
        if then:
            await then(app, pilot)
        release.set()
        await pilot.pause()
        await pilot.pause()
        return [e["seq"] for e in app.view.events]

    return console.run(scenario)


async def _away_and_back(app, pilot) -> None:
    await pilot.press("left_square_bracket", "right_square_bracket")
    await pilot.pause()


@pytest.mark.parametrize(
    ("then", "seqs"),
    [(None, [7, 6, 5, 4, 3]), (_away_and_back, [7, 6])],
    ids=["a-live-event-does-not-cancel-the-older-page", "leaving-the-tab-drops-it"],
)
def test_an_older_page_in_flight_is_neither_lost_nor_misplaced(board, monkeypatch, then, seqs):
    """End asks for the page before the oldest shown. A live event meanwhile
    leaves that read alone (nothing lost or doubled); leaving the tab drops it,
    since the visit rereads the newest page and the boundary moved."""
    assert _paged_back(board, monkeypatch, then) == seqs


def test_another_items_activity_answer_is_dropped(board, monkeypatch):
    """An Activity read still answering for the item shown before must not
    draw into the one opened since."""
    other = RUNNING
    _serve(monkeypatch, _detail(GATE), calls=[])
    release = asyncio.Event()
    asked: list[str] = []

    async def events(work_item_id=None, after_seq=0, *, limit=None, before_seq=None):
        asked.append(work_item_id)
        if work_item_id == GATE["id"]:
            await release.wait()
            return [_event(1, "work_item_created", 5)]
        return []

    monkeypatch.setattr(client, "events", events)
    console = board([GATE, other])

    async def scenario(app, pilot):
        await _opened(console, app, pilot, GATE["id"])
        await pilot.press("right_square_bracket")
        await console.until(pilot, lambda: asked)
        app.open_item(other["id"])
        await pilot.pause()
        release.set()
        await pilot.pause()
        await pilot.pause()
        return app.view.events, _activity(app)

    assert console.run(scenario) == ([], [])


@pytest.mark.parametrize(
    ("error", "gone", "said"),
    [
        (client.status_error(404, "no such item [/]"), True, []),
        (
            client.status_error(409, "refused [/]"),
            False,
            [client.reason_of(str(client.status_error(409, "refused [/]")))],
        ),
    ],
    ids=["not-found-says-gone", "refusal-is-toasted"],
)
def test_an_activity_read_that_fails_says_so(board, monkeypatch, error, gone, said):
    """A 404 is the gone notice; any other refusal is said in the server's words."""
    _serve(monkeypatch, _detail(GATE), calls=[])

    async def events(work_item_id=None, after_seq=0, *, limit=None, before_seq=None):
        raise error

    monkeypatch.setattr(client, "events", events)
    console = board([GATE])

    async def scenario(app, pilot):
        toasts = []
        app.notify_refusal = lambda exc: toasts.append(client.reason_of(str(exc)))
        await _opened(console, app, pilot, GATE["id"])
        await pilot.press("right_square_bracket")
        await console.until(pilot, lambda: app.view._dead or toasts)
        return app.view._dead, toasts

    assert console.run(scenario) == (gone, said)


@pytest.mark.parametrize("late", ["read-lands", "first-session-starts"])
def test_a_log_with_no_session_yet_follows_once_one_is_known(board, monkeypatch, late):
    """A Log shown before the item was read, or before its first session
    started, says so; when the read lands, or the item's row changes, it
    follows the session."""
    box = [_detail(RUNNING, worker_sessions=SESSIONS[1:] if late == "read-lands" else [])]
    _serve(monkeypatch, box[0], calls=[])
    _serve_events(monkeypatch, [])
    hold = asyncio.Event()

    async def get_work_item(work_item_id=None, *, full=False):
        if late == "read-lands":
            await hold.wait()
        return box[0]

    async def stream_log(work_item_id, session_id, after_line=0):
        yield {"n": 0, "text": "output"}

    monkeypatch.setattr(client, "get_work_item", get_work_item)
    monkeypatch.setattr(client, "stream_log", stream_log)
    console = board([RUNNING])
    moved = RUNNING | {"updated_at": "2026-10-16T00:00:00+00:00"}

    async def scenario(app, pilot):
        await _settled(console, app, pilot)
        app.open_item(RUNNING["id"])
        await pilot.press("right_square_bracket", "right_square_bracket")
        await console.until(pilot, lambda: _log(app) == ["No session has run yet."])
        if late == "read-lands":
            hold.set()
        else:
            await console.until(pilot, lambda: app.view.item is not None)
            box[0] = _detail(moved, worker_sessions=SESSIONS[1:])
            app.view.rows_changed([moved], [{"seq": 1}])
        await console.until(pilot, lambda: "output" in _log(app))
        return _log(app)

    assert console.run(scenario)[-1] == "session ended"


@pytest.mark.parametrize(
    ("sessions", "failure", "lines"),
    [
        (
            SESSIONS[1:],
            client.status_error(409, "the log is gone [/]"),
            [
                "verification.review.code_review · running · session 1 of 1",
                "waiting for output",
                client.reason_of(str(client.status_error(409, "the log is gone [/]"))),
            ],
        ),
        (
            SESSIONS[1:],
            None,
            [
                "verification.review.code_review · running · session 1 of 1",
                "waiting for output",
                "session ended",
            ],
        ),
    ],
    ids=["stream-refused", "one-session-offers-no-s"],
)
def test_the_log_says_what_it_cannot_follow(board, monkeypatch, sessions, failure, lines):
    """A refused stream is said in the server's words (`[/]` as typed); with one
    session, or none, `s` is not offered."""
    _serve(monkeypatch, _detail(RUNNING, worker_sessions=sessions), calls=[])
    _serve_events(monkeypatch, [])

    async def stream_log(work_item_id, session_id, after_line=0):
        if failure:
            raise failure
        return
        yield {}

    monkeypatch.setattr(client, "stream_log", stream_log)
    console = board([RUNNING])

    async def scenario(app, pilot):
        await _opened(console, app, pilot, RUNNING["id"])
        await pilot.press("right_square_bracket", "right_square_bracket")
        await console.until(pilot, lambda: _log(app) == lines)
        return app.view.check_action("session", ())

    assert console.run(scenario) is False


def _logging(app) -> list:
    return [w for w in app.query_one("#item").workers if w.group == "log" and not w.is_finished]


def _forever(monkeypatch) -> None:
    """A session that never ends, having said one line."""

    async def stream_log(work_item_id, session_id, after_line=0):
        yield {"n": 0, "text": "first"}
        await asyncio.Event().wait()
        yield {}

    monkeypatch.setattr(client, "stream_log", stream_log)


def _held_reads(monkeypatch, detail):
    """`get_work_item` that waits for `hold` once it is set; returns the hold and
    the count of reads that finished."""
    hold, done = asyncio.Event(), []
    hold.set()

    async def get_work_item(work_item_id=None, *, full=False):
        await hold.wait()
        done.append(1)
        return detail

    monkeypatch.setattr(client, "get_work_item", get_work_item)
    return hold, done


@pytest.mark.parametrize("leave", ["another-tab", "another-view"])
def test_a_reread_that_lands_out_of_sight_does_not_shred_the_document(board, monkeypatch, leave):
    """The Overview is drawn only while its pane is on screen (a hidden pane
    wraps at one cell): a read that lands on another tab or view leaves the
    document whole for when the reader comes back."""
    detail = _detail(GATE)
    _serve(monkeypatch, detail, calls=[])
    _serve_events(monkeypatch, [])
    hold, done = _held_reads(monkeypatch, detail)
    console = board([GATE])
    moved = GATE | {"updated_at": "2026-10-16T00:00:00+00:00"}

    async def scenario(app, pilot):
        await _opened(console, app, pilot, GATE["id"])
        hold.clear()
        before = len(done)
        app.view.rows_changed([moved], [{"seq": 1}])
        if leave == "another-tab":
            await pilot.press("right_square_bracket")
        else:
            app.show("server")
        await pilot.pause()
        hold.set()
        await console.until(pilot, lambda: len(done) > before)
        await pilot.pause()
        if leave == "another-tab":
            await pilot.press("left_square_bracket")
        else:
            app.show("item")
        await console.until(pilot, lambda: _overview(app)[:1] == ["line 0 [/]"])
        await pilot.pause()
        return _overview(app)

    lines = console.run(scenario)
    assert lines[:60] == [f"line {n} [/]" for n in range(60)]


def test_a_log_is_not_followed_by_a_read_landing_behind_the_board(board, monkeypatch):
    """A Log opened before the read, then left for another view: when the read
    lands there is nobody to follow for."""
    detail = _detail(RUNNING, worker_sessions=SESSIONS[1:])
    _serve(monkeypatch, detail, calls=[])
    _serve_events(monkeypatch, [])
    _forever(monkeypatch)
    hold, done = _held_reads(monkeypatch, detail)
    console = board([RUNNING])

    async def scenario(app, pilot):
        await _settled(console, app, pilot)
        hold.clear()
        app.open_item(RUNNING["id"])
        await pilot.press("right_square_bracket", "right_square_bracket")
        await console.until(pilot, lambda: _log(app) == ["No session has run yet."])
        app.show("server")
        await pilot.pause()  # the Item has heard it is hidden
        hold.set()
        await console.until(pilot, lambda: done)
        await pilot.pause()
        return _logging(app)

    assert console.run(scenario) == []


def test_a_log_stops_when_the_item_is_gone(board, monkeypatch):
    """Archived while its Log is followed: the 404 reread says so and the
    stream is let go."""
    _serve(monkeypatch, _detail(RUNNING, worker_sessions=SESSIONS[1:]), calls=[])
    _serve_events(monkeypatch, [])
    _forever(monkeypatch)
    console = board([RUNNING])

    async def scenario(app, pilot):
        await _opened(console, app, pilot, RUNNING["id"])
        app.view.show_tab("log")
        await console.until(pilot, lambda: _logging(app) and "first" in _log(app))

        async def gone(work_item_id=None, *, full=False):
            raise client.status_error(404, "gone")

        monkeypatch.setattr(client, "get_work_item", gone)
        app.view.rows_changed([RUNNING | {"updated_at": "2026-10-16T00:00:00+00:00"}], [])
        await console.until(pilot, lambda: app.view._dead)
        await pilot.pause()
        return _logging(app)

    assert console.run(scenario) == []


def test_a_log_follows_again_when_the_item_is_shown_again(board, monkeypatch):
    """Hidden (another view, or a terminal too small) the Log was let go; shown
    again with the Log tab current, it follows again."""
    _serve(monkeypatch, _detail(RUNNING, worker_sessions=SESSIONS[1:]), calls=[])
    _serve_events(monkeypatch, [])
    _forever(monkeypatch)
    console = board([RUNNING])

    async def scenario(app, pilot):
        await _opened(console, app, pilot, RUNNING["id"])
        app.view.show_tab("log")
        await console.until(pilot, lambda: _logging(app))
        app.show("server")
        await pilot.pause()
        hidden = _logging(app)
        app.show("item")
        await console.until(pilot, lambda: _logging(app))
        return hidden

    assert console.run(scenario) == []


@pytest.mark.parametrize(
    "then",
    [
        lambda view, app: view.show_tab("diff"),
        lambda view, app: app.show("server"),
    ],
    ids=["another-tab", "another-view"],
)
def test_a_tab_started_late_checks_it_is_still_wanted(board, monkeypatch, then):
    """The read a tab starts runs after the next refresh; a tab or view change
    in the same frame must find it not started."""
    _serve(monkeypatch, _detail(RUNNING, worker_sessions=SESSIONS[1:]), calls=[])
    _serve_events(monkeypatch, [])
    _forever(monkeypatch)
    console = board([RUNNING])

    async def scenario(app, pilot):
        await _opened(console, app, pilot, RUNNING["id"])
        app.view.show_tab("log")
        then(app.view, app)
        await pilot.pause()
        await pilot.pause()
        return _logging(app)

    assert console.run(scenario) == []


def test_end_after_coming_back_does_not_page_from_the_last_visits_boundary(board, monkeypatch):
    """Leave the Activity with the oldest event 3, let two arrive, come back and
    press End before the first page answers: the page before 3 must not be
    glued under the fresh first page (7 to 4 would be gone, silently)."""
    _serve(monkeypatch, _detail(GATE), calls=[])
    history = [_event(n, "node_started", 10 - n, node=f"n{n}") for n in range(1, 7)]
    first, older = asyncio.Event(), asyncio.Event()
    first.set()
    _serve_events(monkeypatch, history, hold={item_view.NEWEST: first, 3: older})
    monkeypatch.setattr(item_view, "PAGE", 2)
    console = board([GATE])

    def seqs(app):
        return [e["seq"] for e in app.view.events]

    async def scenario(app, pilot):
        await _opened(console, app, pilot, GATE["id"])
        await pilot.press("right_square_bracket")
        await console.until(pilot, lambda: seqs(app) == [6, 5])
        older.set()
        await pilot.press("end")
        await console.until(pilot, lambda: seqs(app) == [6, 5, 4, 3])
        await pilot.press("left_square_bracket")
        history.extend(_event(n, "node_started", 0, node=f"n{n}") for n in (7, 8))
        first.clear()
        older.clear()
        await pilot.press("right_square_bracket")
        await pilot.pause()
        await pilot.press("end")  # asks for the page before 3, while the first is held
        await pilot.pause()
        first.set()
        await console.until(pilot, lambda: seqs(app) == [8, 7])
        older.set()
        await pilot.pause()
        await pilot.pause()
        return seqs(app)

    assert console.run(scenario) == [8, 7]
