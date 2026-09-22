"""A stop's `suggested_action` (Kraft-s7c04.27) reaches the detail payload,
bounded like `stop_reason`, and `kraft view show` prints the one command that
takes it."""

import os
import sqlite3
from pathlib import Path

import pytest

from kraft import cli, events

SKIP = {"action": "skip", "reason": "the branch has no MR; a retry would re-push it"}


def _append(wid, event_type, payload):
    conn = sqlite3.connect(Path(os.environ["KRAFT_RUN_DIR"]) / "orchestrator.db")
    try:
        events.append(conn, wid, event_type, payload)
        conn.commit()
    finally:
        conn.close()


def _stop(wid, suggested):
    payload = {"node_id": "verify", "reason": "task failed in node verify"}
    _append(wid, "work_item_needs_human", {**payload, "suggested_action": suggested})


def test_the_current_stop_s_suggestion_is_on_the_detail_payload(client, repo):
    wid = client.post(
        "/api/work-items", json={"repo": str(repo), "title": "t", "chain_template": "quick-task"}
    ).json()["id"]
    detail = lambda: client.get(f"/api/work-items/{wid}").json()["suggested_action"]  # noqa: E731
    assert detail() is None

    _stop(wid, SKIP)
    assert detail() == SKIP

    # Superseded like `stop_reason`: once a person acted, it is not offered again.
    _append(wid, "work_item_retried", {"node_id": "verify"})
    assert detail() is None


@pytest.mark.parametrize(
    ("suggested", "command"),
    [
        (SKIP, "kraft item skip {wid}"),
        ({"action": "retry", "reason": "CI is down"}, "kraft item retry {wid}"),
        ({"action": "abandon", "reason": "already on main"}, "kraft item abandon --yes {wid}"),
    ],
    ids=["skip", "retry", "abandon"],
)
def test_show_prints_the_suggestion_and_its_command(
    app, capsys, make_item, repo, suggested, command
):
    wid = make_item(repo)
    _stop(wid, suggested)

    cli.main(["view", "show", wid])
    out = capsys.readouterr().out
    assert "task failed in node verify" in out
    assert suggested["reason"] in out
    assert f"run: {command.format(wid=wid)}" in out
