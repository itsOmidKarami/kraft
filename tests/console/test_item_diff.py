"""The Item's Diff tab (spec §7.2, §4.3): what landed, then what did not,
then the trailer; read again when its row changes, drawn a part at a time."""

from __future__ import annotations

import asyncio

import pytest
from support.console_board import RUNNING, _detail, _opened, _serve, _serve_events
from textual.widgets import RichLog

from kraft import client, render

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
                ("diff --git a/x b/x", "muted"),
                ("--- a/x", "muted"),
                ("+++ b/x", "muted"),
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


def test_a_huge_diff_lets_keys_through_while_it_draws(board, monkeypatch):
    """Drawn a part at a time, not all at once: a key gets through, the draw left behind stops."""
    _serve(monkeypatch, _detail(RUNNING), calls=[])
    _serve_events(monkeypatch, [])
    huge = {"base_ref": "main", "diff": "\n".join(f"+line {n}" for n in range(20_000))}
    monkeypatch.setattr(client, "diff", lambda *args, **kwargs: asyncio.sleep(0, huge))
    console = board([RUNNING])

    async def scenario(app, pilot):
        await _opened(console, app, pilot, RUNNING["id"])
        log, pressed = app.query_one("#diff", RichLog), []
        write = log.write

        def first_line(*args, **kwargs):  # the key comes while the draw has just begun
            log.write = write
            pressed.append(asyncio.create_task(pilot.press("right_square_bracket")))
            return write(*args, **kwargs)

        log.write = first_line
        await pilot.press("left_square_bracket")  # round to the Diff
        await console.until(pilot, lambda: pressed and pressed[0].done())
        return app.view.tab, len(log.lines) < 20_000

    assert console.run(scenario) == ("overview", True)


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
        await pilot.pause()  # the show's own read settles first
        shown = len(reads)
        view.rows_changed([moved], [{"seq": 3}])
        await console.until(pilot, lambda: len(reads) > shown)
        return same, hidden

    assert console.run(scenario) == (1, 1)


def test_a_diff_read_again_unchanged_is_kept_as_drawn(board, monkeypatch):
    """A running item's row changes often: a diff that reads the same is not
    cleared and drawn again (the reader's place kept); one that changed is."""
    _serve(monkeypatch, _detail(RUNNING), calls=[])
    _serve_events(monkeypatch, [])
    body = {"diff": "\n".join(f"+line {n}" for n in range(200))}
    reads: list[str] = []

    async def diff(work_item_id=None, ignore_whitespace=False):
        reads.append(work_item_id)
        return {"base_ref": "main", **body}

    monkeypatch.setattr(client, "diff", diff)
    console = board([RUNNING])

    async def scenario(app, pilot):
        await _opened(console, app, pilot, RUNNING["id"])
        log, clears = app.query_one("#diff", RichLog), []
        clear = log.clear
        log.clear = lambda: (clears.append(1), clear())[1]
        await pilot.press("left_square_bracket")  # round to the Diff
        await console.until(pilot, lambda: len(log.lines) >= 200)
        log.scroll_to(y=50, animate=False, immediate=True)
        drawn = len(clears)

        async def reread(n):
            before = len(reads)
            app.view.rows_changed(
                [RUNNING | {"updated_at": f"2026-10-16T0{n}:00:00"}], [{"seq": n}]
            )
            await console.until(pilot, lambda: len(reads) > before)
            await console.until(
                pilot, lambda: all(w.is_finished for w in app.workers if w.group == "tab")
            )
            return len(clears) - drawn, log.scroll_y, len(log.lines)

        same = await reread(1)
        body["diff"] = "+changed"
        return same, await reread(2)

    same, changed = console.run(scenario)
    assert (same, changed) == ((0, 50, 200), (1, 0, 1))
