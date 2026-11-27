import time

from kraft import events, store


def test_threads_of_a_round(client, repo):
    """`?gate=&round=` answers with the threads `gate_rounds` counts for that round;
    a thread submitted after the round ended is in none of its window, and a draft
    in the window is in neither the list nor the count."""
    wid = client.post(
        "/api/work-items",
        json={"autostart": True, "repo": str(repo), "title": "t", "chain_template": "quick-task"},
    ).json()["id"]
    db = client.app.state.db

    def thread(c, body, line):
        store.create_thread(
            c, wid=wid, gate="plan_approval", anchor_sha="s", body=body, file_path="a.py",
            start_line=line, end_line=line, side="new", label="must_fix",
        )  # fmt: skip
        store.submit_review(
            c, wid=wid, gate="plan_approval", outcome="request_changes", summary="s",
            head_sha="h", base_sha="b",
        )  # fmt: skip

    def first(c):
        events.append(c, wid, "gate_requested", {"gate": "plan_approval"})
        thread(c, "in round one", 1)
        events.append(
            c, wid, "gate_rejected",
            {"gate": "plan_approval", "note": "n", "node": "plan", "by": "human"},
        )  # fmt: skip

    client.portal.call(db.write, first)
    time.sleep(0.01)
    client.portal.call(
        db.write, lambda c: thread(c, "after round one", 2)
    )  # submitted: not a draft

    def draft(c):  # after the submit above, which would send it; back-dated into round one's window
        tid = store.create_thread(
            c, wid=wid, gate="plan_approval", anchor_sha="s", body="a draft", file_path="a.py",
            start_line=5, end_line=5, side="new", label="must_fix",
        )  # fmt: skip
        c.execute(
            "UPDATE review_threads SET created_at = (SELECT created_at FROM events "
            "WHERE work_item_id = ? AND type = 'gate_rejected') WHERE id = ?",
            (wid, tid),
        )

    client.portal.call(db.write, draft)
    url = f"/api/work-items/{wid}/threads"
    gate = "plan_approval"
    round_one = client.get(url, params={"gate": gate, "round": 1}).json()
    assert [t["comments"][0]["body"] for t in round_one] == ["in round one"]
    counted = client.get(f"/api/work-items/{wid}").json()["gate_rounds"][gate][0]["threads"]
    assert counted["total"] == len(round_one)  # the detail's count and the list share one window
    assert client.get(url, params={"gate": gate, "round": 2}).status_code == 404
    assert client.get(url, params={"round": 1}).status_code == 400
    everything = client.get(url, params={"gate": gate}).json()  # gate alone: as today, drafts too
    assert len(everything) == 3
