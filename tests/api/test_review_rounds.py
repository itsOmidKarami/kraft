import time

from kraft import events, store


def test_threads_of_a_round(client, repo):
    """`?gate=&round=` answers with the threads `gate_rounds` counts for that round;
    a thread submitted after the round ended is in none of its window."""
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

    url = f"/api/work-items/{wid}/threads"
    gate = "plan_approval"
    assert [
        t["comments"][0]["body"] for t in client.get(url, params={"gate": gate, "round": 1}).json()
    ] == ["in round one"]
    assert client.get(url, params={"gate": gate, "round": 2}).status_code == 404
    assert client.get(url, params={"round": 1}).status_code == 400
    assert len(client.get(url, params={"gate": gate}).json()) == 2  # gate alone: as today
