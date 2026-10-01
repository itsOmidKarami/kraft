"""What sets a cap below policy, and a chain's caps under the policy draft
(W15 A.4): the Policy page's "set below policy" column and its Preview."""

from __future__ import annotations

URL = "/api/drafts/policy/policy"


def set_value(scope, key, value):
    return {"op": "set_value", "scope": scope, "key": key, "value": value}


def resolved(client, *batch):
    r = client.post(f"{URL}/ops", json={"ops": list(batch)})
    assert r.status_code == 200, r.text
    return r.json()["result"]


def below(body, level, cap):
    return body["resolved"]["limits"]["caps"][level][cap]["below"]


def test_a_library_component_that_sets_a_cap_is_listed_below_policy_with_its_source(client):
    body = resolved(client)
    tasks = below(body, "tasks", "time_cap_minutes")
    implementer = next(b for b in tasks if b["via"] == "library:tasks.implementer")
    assert (implementer["layer"], implementer["chain"], implementer["value"]) == (
        "library",
        "default",
        120,
    )
    assert implementer["exceeds"] is False
    # Every cap answers a list, empty when nothing below sets it.
    assert below(body, "nodes", "token_budget") == []


def test_a_maximum_under_what_a_scope_sets_marks_it_exceeding(client):
    body = resolved(client, set_value("limits", "maxima.tasks.time_cap_minutes", 90))
    implementer = next(
        b
        for b in below(body, "tasks", "time_cap_minutes")
        if b["via"] == "library:tasks.implementer"
    )
    assert implementer["exceeds"] is True


def test_the_preview_gives_each_scopes_caps_under_the_draft_and_changes_nothing(client):
    client.post(
        f"{URL}/ops", json={"ops": [set_value("limits", "maxima.tasks.time_cap_minutes", 90)]}
    )
    r = client.get(f"{URL}/preview", params={"chain": "default"})
    assert r.status_code == 200, r.text
    scopes = r.json()["scopes"]
    assert scopes[0]["kind"] == "chain" and scopes[0]["level"] == "work_item"
    task = next(
        s
        for s in scopes
        if s["kind"] == "task" and s["caps"]["time_cap_minutes"]["source"].startswith("library:")
    )
    cap = task["caps"]["time_cap_minutes"]
    assert (cap["value"], cap["exceeds"]) == (120, True)
    assert cap["maximum"] == {"value": 90, "level": "tasks"}
    # A read: the draft still has the one op's change.
    assert [c["path"] for c in client.get(URL).json()["result"]["changes"]] == [
        "maxima.tasks.time_cap_minutes"
    ]


def test_the_preview_of_an_unknown_chain_is_404(client):
    assert client.get(f"{URL}/preview", params={"chain": "nope"}).status_code == 404
    assert client.get(f"{URL}/preview").status_code == 422
