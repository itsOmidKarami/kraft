"""The item chain draft routes: a person's edits to one item's chain, kept
server-side, applied whole in one transaction or refused."""

from __future__ import annotations

from support.api import _set_status

from kraft.templates.models import MaterializedChain

OVERRIDE = {"op": "override", "path": "work_brief.main.author", "task_config": {"effort": "high"}}
ADD = {
    "op": "add_node",
    "after": "verification",
    "node": {"id": "again", "extends": "verification"},
}
SKIP = {"op": "skip", "path": "work_brief.main.author"}


def _filed(client, repo) -> str:
    """A `default` item filed paused: not started, so no op is passed."""
    body = {"title": "draft me", "repo": str(repo), "chain_template": "default"}
    return client.post("/api/work-items", json=body).json()["id"]


def _ids(view) -> list[str]:
    return [n["id"] for n in view["nodes"]]


def test_put_replaces_the_op_list_get_reads_it_and_an_empty_list_deletes_it(client, repo):
    wid = _filed(client, repo)
    url = f"/api/work-items/{wid}/draft"
    empty = client.get(url).json()
    assert (empty["ops"], empty["base_seq"]) == ([], None)
    cap = client.get(f"/api/work-items/{wid}").json()["budget_cap"]
    assert empty["checks"] == {"budget": {"spent_usd": cap["spent_usd"], "cap_usd": cap["cap_usd"]}}

    put = client.put(url, json={"ops": [OVERRIDE, ADD]})
    assert put.status_code == 200, put.text
    assert [op["passed"] for op in put.json()["ops"]] == [False, False]
    assert put.json()["problems"] == []
    assert _ids(put.json())[_ids(empty).index("verification") + 1] == "again"
    assert client.get(url).json() == put.json()

    assert client.put(url, json={"ops": [{"op": "rename"}]}).status_code == 422
    assert client.put(url, json={"ops": []}).json()["ops"] == []
    assert client.delete(url).status_code == 404
    client.put(url, json={"ops": [OVERRIDE]})
    assert client.delete(url).status_code == 204
    assert client.get(url).json()["ops"] == []


def test_apply_revises_the_chain_records_the_skip_and_drops_the_draft(client, repo):
    wid = _filed(client, repo)
    client.put(f"/api/work-items/{wid}/draft", json={"ops": [OVERRIDE, ADD, SKIP]})

    r = client.post(f"/api/work-items/{wid}/draft/apply")

    assert r.status_code == 200, r.text
    assert r.json()["ops"] == []
    item = client.get(f"/api/work-items/{wid}").json()
    assert "again" in [n["id"] for n in item["chain_definition"]["nodes"]]
    chain = MaterializedChain.from_json(item["materialized_chain"])
    [author] = [t for n in chain.chain.nodes for t in n.tasks() if t.path == SKIP["path"]]
    assert author.task.effort == "high"
    events = client.get(f"/api/work-items/{wid}/events").json()
    [revised] = [e["payload"] for e in events if e["type"] == "chain_revised"]
    assert (revised["source"], revised["gate"]) == ("draft", None)
    assert "+ again" in revised["diff"]
    [skipped] = [e["payload"] for e in events if e["type"] == "scope_skipped"]
    assert skipped["path"] == "work_brief.main.author"


def test_apply_after_the_item_moved_past_an_op_answers_409_and_writes_nothing(client, repo):
    """The item moves on between the request's own read and its write; the
    write transaction's re-read is what sees it."""
    wid = _filed(client, repo)
    client.put(f"/api/work-items/{wid}/draft", json={"ops": [ADD, OVERRIDE]})
    db = client.app.state.db
    write = db.write

    async def moved_first(fn):
        await write(
            lambda c: c.execute(
                "UPDATE work_items SET current_node_id = 'local_review' WHERE id = ?", (wid,)
            )
        )
        return await write(fn)

    db.write = moved_first
    try:
        r = client.post(f"/api/work-items/{wid}/draft/apply")
    finally:
        db.write = write

    assert r.status_code == 409, r.text
    assert r.json()["passed"] == [0, 1]
    events = client.get(f"/api/work-items/{wid}/events").json()
    assert not [e for e in events if e["type"] == "chain_revised"]
    assert len(client.get(f"/api/work-items/{wid}/draft").json()["ops"]) == 2


def test_apply_with_a_problem_answers_422(client, repo):
    wid = _filed(client, repo)
    client.put(
        f"/api/work-items/{wid}/draft", json={"ops": [{"op": "remove_node", "node": "nope"}]}
    )
    r = client.post(f"/api/work-items/{wid}/draft/apply")
    assert r.status_code == 422
    assert [p["op"] for p in r.json()["problems"]] == [0]


def test_an_ended_items_draft_is_refused(client, repo):
    wid = _filed(client, repo)
    client.put(f"/api/work-items/{wid}/draft", json={"ops": [OVERRIDE]})
    _set_status(wid, "completed")
    assert client.get(f"/api/work-items/{wid}/draft").status_code == 409
    assert client.post(f"/api/work-items/{wid}/draft/apply").status_code == 409
