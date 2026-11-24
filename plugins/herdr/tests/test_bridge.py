"""The bridge, against a fake `kraft` and a fake `herdr`."""

from __future__ import annotations

import json
import time

import pytest
from herdr_fakes import Fakes, result

import kraft_herdr

LIST = "view list"
EVENTS = "view events"


def row(item_id, status, title="a title", **extra):
    return {"id": item_id, "title": title, "display_status": status, **extra}


@pytest.fixture
def fakes(tmp_path, monkeypatch):
    f = Fakes(tmp_path / "fakes").install(monkeypatch)
    f.reply(
        "herdr", "workspace list", result(workspaces=[{"label": "Kraft", "workspace_id": "w1"}])
    )
    f.reply("herdr", "pane list", result(panes=[]))
    f.reply("herdr", "plugin pane open", result(plugin_pane={"pane": {"pane_id": "w1:p9"}}))
    f.reply("kraft", EVENTS, {"stdout": ""})
    return f


@pytest.fixture
def state_dir(tmp_path):
    return tmp_path / "state"


def bridge(state_dir, *, notify=False):
    return kraft_herdr.Bridge(kraft_herdr.Herdr(), "kraft", state_dir, notify=notify)


def board(fakes, *rows):
    fakes.reply("kraft", LIST, {"stdout": list(rows)})


def reports(fakes):
    """(pane, state, message) for every report-agent call."""
    out = []
    for argv in fakes.calls("herdr", "pane report-agent"):
        out.append((argv[2], argv[argv.index("--state") + 1], argv[argv.index("--message") + 1]))
    return out


def test_reconcile_opens_a_tab_per_live_item_and_reports_states(fakes, state_dir):
    """Three items, one of them finished and with a tab already: two live tabs
    are opened, and the finished one is told so."""
    state_dir.mkdir()
    (state_dir / "panes.json").write_text(
        json.dumps({"items": {"cccccccc1": {"pane": "w1:p3", "status": "running"}}})
    )
    fakes.reply(
        "herdr",
        "pane list",
        result(panes=[{"pane_id": "w1:p3", "label": "cccccccc old"}]),
    )
    fakes.reply(
        "herdr",
        "plugin pane open",
        [
            result(plugin_pane={"pane": {"pane_id": "w1:p1"}}),
            result(plugin_pane={"pane": {"pane_id": "w1:p2"}}),
        ],
    )
    board(
        fakes,
        row("aaaaaaaa1", "running", current_node_id="implementation"),
        row("bbbbbbbb1", "needs_you", pending_gate="plan_review"),
        row("cccccccc1", "done"),
    )
    bridge(state_dir).reconcile()

    opened = fakes.calls("herdr", "plugin pane open")
    assert len(opened) == 2
    assert {a[a.index("--env") + 1] for a in opened} == {
        "KRAFT_ITEM=aaaaaaaa1",
        "KRAFT_ITEM=bbbbbbbb1",
    }
    assert all("--no-focus" in a and a[a.index("--placement") + 1] == "tab" for a in opened)
    assert ("w1:p1", "working", "implementation") in reports(fakes)
    assert ("w1:p2", "blocked", "plan_review") in reports(fakes)
    assert reports(fakes).count(("w1:p3", "idle", "merged")) == 1
    renames = {tuple(a[2:]) for a in fakes.calls("herdr", "pane rename")}
    assert ("w1:p1", "aaaaaaaa a title") in renames


def test_every_report_carries_the_source_agent_and_an_increasing_seq(fakes, state_dir):
    board(fakes, row("aaaaaaaa1", "running"))
    b = bridge(state_dir)
    b.reconcile()
    b.reconcile()
    calls = fakes.calls("herdr", "pane report-agent")
    assert all(a[a.index("--source") + 1] == "custom:kraft" for a in calls)
    assert all(a[a.index("--agent") + 1] == "kraft" for a in calls)
    # Herdr restarts the tab's process itself from the command reported after `--`.
    assert all(a[a.index("--") + 3 :] == ["item", "aaaaaaaa1"] for a in calls)
    seqs = [int(a[a.index("--seq") + 1]) for a in calls]
    assert seqs == sorted(seqs) and seqs[0] > 1_700_000_000_000  # milliseconds, not seconds


def test_reconcile_readopts_tabs_by_label_after_restart(fakes, state_dir):
    """Herdr restarted: the panes are there under their labels, and panes.json
    names stale ids. No second set of tabs may open."""
    state_dir.mkdir()
    (state_dir / "panes.json").write_text(
        json.dumps({"items": {"aaaaaaaa1": {"pane": "w9:p9", "status": "running"}}})
    )
    fakes.reply(
        "herdr", "pane list", result(panes=[{"pane_id": "w1:p5", "label": "aaaaaaaa a title"}])
    )
    board(fakes, row("aaaaaaaa1", "running"))
    bridge(state_dir).reconcile()

    assert fakes.calls("herdr", "plugin pane open") == []
    assert reports(fakes)[0][0] == "w1:p5"
    assert (
        json.loads((state_dir / "panes.json").read_text())["items"]["aaaaaaaa1"]["pane"] == "w1:p5"
    )


def test_a_retitled_item_keeps_its_tab(fakes, state_dir):
    state_dir.mkdir()
    (state_dir / "panes.json").write_text(json.dumps({"items": {"aaaaaaaa1": {"pane": "w1:p5"}}}))
    fakes.reply("herdr", "pane list", result(panes=[{"pane_id": "w1:p5", "label": "aaaaaaaa old"}]))
    board(fakes, row("aaaaaaaa1", "running", title="new"))
    bridge(state_dir).reconcile()
    assert fakes.calls("herdr", "plugin pane open") == []
    assert [tuple(a[2:]) for a in fakes.calls("herdr", "pane rename")] == [
        ("w1:p5", "aaaaaaaa new")
    ]


def test_the_workspace_is_made_when_there_is_none(fakes, state_dir):
    fakes.reply("herdr", "workspace list", result(workspaces=[]))
    fakes.reply("herdr", "workspace create", result(workspace={"workspace_id": "w7"}))
    board(fakes)
    bridge(state_dir).reconcile()
    (create,) = fakes.calls("herdr", "workspace create")
    assert create[create.index("--label") + 1] == "Kraft" and "--no-focus" in create


def test_missing_item_is_reported_finished_once(fakes, state_dir):
    """Archived or abandoned, it drops out of the list: told once, never reopened."""
    fakes.reply("herdr", "pane list", result(panes=[]))
    board(fakes, row("aaaaaaaa1", "running"))
    fakes.reply(
        "herdr", "pane list", result(panes=[{"pane_id": "w1:p9", "label": "aaaaaaaa a title"}])
    )
    b = bridge(state_dir)
    b.reconcile()
    board(fakes)
    b.reconcile()
    b.reconcile()
    assert reports(fakes).count(("w1:p9", "idle", "archived")) == 1
    assert fakes.calls("herdr", "plugin pane open") == []
    # and it stays closed if it ever comes back on the list as ended
    board(fakes, row("aaaaaaaa1", "archived"))
    b.reconcile()
    assert reports(fakes).count(("w1:p9", "idle", "archived")) == 1


def test_a_finished_item_that_never_had_a_tab_gets_none(fakes, state_dir):
    board(fakes, row("aaaaaaaa1", "done"), row("bbbbbbbb1", "cancelled"))
    bridge(state_dir).reconcile()
    assert fakes.calls("herdr", "plugin pane open") == []
    assert reports(fakes) == []


def test_a_failing_item_does_not_stop_the_others(fakes, state_dir):
    fakes.reply(
        "herdr",
        "plugin pane open",
        [{"exit": 1, "stderr": "no"}, result(plugin_pane={"pane": {"pane_id": "w1:p2"}})],
    )
    board(fakes, row("aaaaaaaa1", "running"), row("bbbbbbbb1", "running"))
    bridge(state_dir).reconcile()
    assert [r[0] for r in reports(fakes)] == ["w1:p2"]


def test_second_bridge_exits_on_the_lock(state_dir):
    first = kraft_herdr.acquire_lock(state_dir)
    assert first is not None
    assert kraft_herdr.acquire_lock(state_dir) is None
    first.close()
    assert kraft_herdr.acquire_lock(state_dir) is not None


def test_stream_end_reconnects(fakes, state_dir, monkeypatch):
    """The server restarted and the stream ended: ask again, and read the board
    again, without a second set of tabs."""
    monkeypatch.setattr(kraft_herdr, "BACKOFF_MIN", 0)
    monkeypatch.setattr(kraft_herdr, "BACKOFF_MAX", 0)
    board(fakes, row("aaaaaaaa1", "running"))
    fakes.reply(
        "herdr",
        "pane list",
        [result(panes=[]), result(panes=[{"pane_id": "w1:p9", "label": "aaaaaaaa a title"}])],
    )
    bridge(state_dir).run(max_cycles=3)
    assert len(fakes.calls("kraft", EVENTS)) == 3
    assert len(fakes.calls("kraft", LIST)) == 3
    assert len(fakes.calls("herdr", "plugin pane open")) == 1


def test_events_trigger_a_reconcile_without_waiting_a_minute(fakes, state_dir, monkeypatch):
    """A line from the stream is the signal; the debounce only spaces them."""
    monkeypatch.setattr(kraft_herdr, "BACKOFF_MIN", 0)
    monkeypatch.setattr(kraft_herdr, "BACKOFF_MAX", 0)
    monkeypatch.setattr(kraft_herdr, "DEBOUNCE", 0.05)
    board(fakes, row("aaaaaaaa1", "running"))
    fakes.reply("kraft", EVENTS, {"stdout": '{"type": "node_started", "seq": 5}\n'})
    started = time.monotonic()
    bridge(state_dir).run(max_cycles=1)
    assert len(fakes.calls("kraft", LIST)) >= 1
    assert time.monotonic() - started < 10


@pytest.mark.parametrize("notify", [True, False])
def test_notify_on_needs_you_only_when_enabled(fakes, state_dir, notify):
    state_dir.mkdir()
    (state_dir / "panes.json").write_text(
        json.dumps({"items": {"aaaaaaaa1": {"pane": "w1:p9", "status": "running"}}})
    )
    fakes.reply(
        "herdr", "pane list", result(panes=[{"pane_id": "w1:p9", "label": "aaaaaaaa " + "t" * 100}])
    )
    fakes.reply("kraft", "view show", {"stdout": {"stop_reason": "r" * 400}})
    board(fakes, row("aaaaaaaa1", "needs_you", title="t" * 100))
    bridge(state_dir, notify=notify).reconcile()
    shown = fakes.calls("herdr", "notification show")
    if not notify:
        assert shown == [] and fakes.calls("kraft", "view show") == []
        return
    (call,) = shown
    assert len(call[2]) == 80 and len(call[call.index("--body") + 1]) == 240
    # a second pass with the item still needing you says nothing more
    bridge(state_dir, notify=notify).reconcile()
    assert len(fakes.calls("herdr", "notification show")) == 1


def test_an_item_first_seen_already_needing_you_is_not_announced(fakes, state_dir):
    """A bridge that has just started would otherwise notify for every item that
    was waiting before it was."""
    board(fakes, row("aaaaaaaa1", "needs_you"))
    bridge(state_dir, notify=True).reconcile()
    assert fakes.calls("herdr", "notification show") == []
