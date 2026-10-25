"""`/api/workers`: managing machines, and the dial-in WebSocket that
authenticates by a per-machine token and nothing else."""

from __future__ import annotations

import asyncio
import json

import pytest
from starlette.websockets import WebSocketDisconnect
from support.api import _seed_session
from support.remote import Peer, attach

from kraft import auth as auth_mod
from kraft import store
from kraft.config import read_yaml
from kraft.remote import protocol, registry


def _machines(client):
    """The listed machines: everything but the local worker, which is always first."""
    listed = client.get("/api/workers").json()["workers"]
    assert listed[0]["name"] == "local"
    return listed[1:]


def _file(client):
    return read_yaml(client.app.state.templates_dir / "workers.yaml")


def test_adding_a_dial_in_machine_returns_its_token_once_and_stores_only_the_hash(client):
    reply = client.post("/api/workers", json={"name": "home", "capacity": 2})
    assert reply.status_code == 201, reply.text
    body = reply.json()
    token = body["token"]
    assert token.startswith("kwt_") and token in body["serve_command"]
    stored = _file(client)["workers"]["home"]
    assert stored["token_sha256"] == registry.hash_token(token) and token not in json.dumps(
        _file(client)
    )
    listed = _machines(client)
    assert [(w["name"], w["mode"], w["online"], w["capacity"]) for w in listed] == [
        ("home", "dial-in", False, 2)
    ]
    assert token not in client.get("/api/workers").text


@pytest.mark.parametrize(
    ("payload", "status", "text"),
    [
        ({"name": "any"}, 422, "reserved"),
        ({"name": "Bad Name"}, 422, "name"),
        ({"name": "x", "capacity": 0}, 422, "capacity"),
        ({"name": "x", "repos": ["no-such-repo"]}, 422, "no-such-repo"),
        ({"name": "x", "ssh": "-oProxyCommand=evil"}, 422, "ssh"),
    ],
    ids=["reserved", "bad-name", "capacity", "unknown-repo", "ssh-option"],
)
def test_a_bad_machine_is_refused_and_nothing_is_written(client, payload, status, text):
    reply = client.post("/api/workers", json=payload)
    assert reply.status_code == status and text in reply.text
    assert not (client.app.state.templates_dir / "workers.yaml").exists()


def test_the_local_worker_is_listed_first_and_cannot_be_removed(client):
    (local, *others) = client.get("/api/workers").json()["workers"]
    assert others == []
    assert local == {
        "name": "local",
        "mode": "local",
        "online": True,
        "reason": None,
        "slots_used": 0,
        "capacity": None,
        "configured_capacity": None,
        "enabled": True,
        "ssh": None,
        "groups": [],
        "repos": None,
        "last_seen": None,
        "kraft_version": "direct",
        "give_up_after": "never",
        "holders": [],
        "remove_blocked": (
            "the local worker is part of this Kraft and cannot be removed; disable it instead"
        ),
    }
    removed = client.delete("/api/workers/local")
    assert removed.status_code == 409 and removed.json()["detail"] == local["remove_blocked"]


def test_the_local_workers_running_count_comes_from_the_session_rows(client, monkeypatch):
    monkeypatch.setattr(store, "running_local_sessions", lambda conn: 3)
    assert client.get("/api/workers").json()["workers"][0]["slots_used"] == 3


def test_a_machine_added_without_a_capacity_writes_none_and_its_row_says_who_sets_the_limit(client):
    client.post("/api/workers", json={"name": "home"})
    assert "capacity" not in _file(client)["workers"]["home"]
    (row,) = _machines(client)
    assert (row["capacity"], row["capacity_source"], row["machine_capacity"]) == (
        1,
        "default",
        None,
    )


def test_the_local_worker_row_carries_the_limit_workers_yaml_sets(client):
    (client.app.state.templates_dir / "workers.yaml").write_text("local:\n  capacity: 3\n")
    assert client.post("/api/templates/reload").json()["refused_workers"] is None
    assert client.get("/api/workers").json()["workers"][0]["capacity"] == 3


def test_a_local_block_survives_adding_and_removing_a_machine(client):
    path = client.app.state.templates_dir / "workers.yaml"
    path.write_text("local:\n  capacity: 2\n")
    client.post("/api/workers", json={"name": "home"})
    assert _file(client)["local"] == {"capacity": 2}
    client.delete("/api/workers/home")
    assert _file(client)["local"] == {"capacity": 2}


def test_groups_are_written_kept_and_checked_against_machine_names(client):
    path = client.app.state.templates_dir / "workers.yaml"
    path.write_text("local:\n  capacity: 2\n  groups: [gpu]\n")
    reply = client.post("/api/workers", json={"name": "a", "groups": ["gpu", "linux"]})
    assert reply.status_code == 201, reply.text
    assert client.post("/api/workers", json={"name": "b"}).status_code == 201
    assert _file(client)["workers"]["a"]["groups"] == ["gpu", "linux"]
    assert "groups" not in _file(client)["workers"]["b"]
    # A machine named like a group, or a group named like a machine: 422, not 500.
    named_gpu = client.post("/api/workers", json={"name": "gpu"})
    assert named_gpu.status_code == 422 and "gpu" in named_gpu.text
    grouped_b = client.post("/api/workers", json={"name": "c", "groups": ["b"]})
    assert grouped_b.status_code == 422 and "'b'" in grouped_b.text
    client.delete("/api/workers/b")
    assert _file(client)["workers"]["a"]["groups"] == ["gpu", "linux"]
    assert _file(client)["local"] == {"capacity": 2, "groups": ["gpu"]}


def test_the_rows_carry_each_machines_groups_and_the_local_workers_from_local(client):
    (client.app.state.templates_dir / "workers.yaml").write_text("local:\n  groups: [gpu]\n")
    assert client.post("/api/templates/reload").json()["refused_workers"] is None
    client.post("/api/workers", json={"name": "a", "groups": ["gpu", "linux"]})
    client.post("/api/workers", json={"name": "b"})
    rows = {w["name"]: w["groups"] for w in client.get("/api/workers").json()["workers"]}
    assert rows == {"local": ["gpu"], "a": ["gpu", "linux"], "b": []}


def test_a_name_in_use_is_a_409(client):
    assert client.post("/api/workers", json={"name": "home"}).status_code == 201
    assert client.post("/api/workers", json={"name": "home"}).status_code == 409


def test_removing_a_machine_forgets_it_and_404s_when_unknown(client):
    client.post("/api/workers", json={"name": "home"})
    assert client.delete("/api/workers/home").json() == {"removed": "home"}
    assert _machines(client) == []
    assert client.delete("/api/workers/home").status_code == 404


def test_adding_an_ssh_machine_connects_once_and_reports_why_it_could_not(client):
    client.app.state.workers_hub._ssh = ("no-such-ssh-binary",)
    reply = client.post("/api/workers", json={"name": "boxed", "ssh": "boxed"})
    assert reply.status_code == 201
    body = reply.json()
    assert "token" not in body
    assert body["probe"]["connected"] is False
    assert "could not run ssh" in body["probe"]["detail"]
    assert _file(client)["workers"]["boxed"]["ssh"] == "boxed"  # kept: it may just be asleep


@pytest.mark.api_client(
    edit_templates=lambda d: (d / "workers.yaml").write_text("workers:\n  a:\n    capacity: 1\n")
)
def test_a_workers_file_that_does_not_load_runs_no_machines_and_is_named(client):
    assert client.app.state.invalid_workers and "exactly one of" in client.app.state.invalid_workers
    assert _machines(client) == []


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("get", "/api/workers", None),
        ("post", "/api/workers", {"name": "x"}),
        ("delete", "/api/workers/x", None),
        ("patch", "/api/workers/x", {"enabled": False}),
        ("post", "/api/workers/x/rotate-token", None),
    ],
    ids=["list", "add", "remove", "edit", "rotate-token"],
)
def test_a_kraft_session_cannot_manage_machines(client, method, path, body):
    reply = getattr(client, method)(
        path, headers={"X-Kraft-Session-Id": "w1/s1"}, **({"json": body} if body else {})
    )
    assert reply.status_code == 403


def test_a_hand_edit_is_picked_up_by_reload_and_a_broken_one_is_kept_out(client):
    path = client.app.state.templates_dir / "workers.yaml"
    path.write_text(f"workers:\n  boxed:\n    token_sha256: {'a' * 64}\n")
    assert client.post("/api/templates/reload").json()["refused_workers"] is None
    assert [w["name"] for w in _machines(client)] == ["boxed"]
    path.write_text("workers:\n  boxed:\n    capacity: 0\n")
    refused = client.post("/api/templates/reload").json()["refused_workers"]
    assert refused and "capacity" in refused
    assert client.app.state.invalid_workers == refused
    assert [w["name"] for w in _machines(client)] == ["boxed"]


def test_adding_a_machine_disabled_writes_it(client):
    client.post("/api/workers", json={"name": "home", "enabled": False})
    assert _file(client)["workers"]["home"]["enabled"] is False
    assert _machines(client)[0]["enabled"] is False


# ── editing, enabling, rotating, removing ──


def _seed(client, workers, local=None):
    """workers.yaml written by hand and loaded: `workers` is name -> fields (a dial-in token is
    added unless `ssh` is). The hub's ssh is a binary that is not there, so nothing dials out."""
    client.app.state.workers_hub._ssh = ("no-such-ssh-binary",)
    doc = {
        "workers": {
            n: {**({} if "ssh" in f else {"token_sha256": registry.hash_token(f"kwt_{n}")}), **f}
            for n, f in workers.items()
        }
    }
    if local is not None:
        doc["local"] = local
    import yaml

    (client.app.state.templates_dir / "workers.yaml").write_text(yaml.safe_dump(doc))
    assert client.post("/api/templates/reload").json()["refused_workers"] is None


def _row(client, name):
    return next(w for w in client.get("/api/workers").json()["workers"] if w["name"] == name)


@pytest.mark.parametrize(
    ("start", "patch", "stored", "row"),
    [
        ({}, {"enabled": False}, {"enabled": False}, {"enabled": False}),
        ({"enabled": False}, {"enabled": True}, {"enabled": None}, {"enabled": True}),
        ({}, {"capacity": 3}, {"capacity": 3}, {"capacity": 3, "configured_capacity": 3}),
        (
            {"capacity": 3},
            {"capacity": None},
            {"capacity": None},
            {"capacity": 1, "configured_capacity": None, "capacity_source": "default"},
        ),
        (
            {"groups": ["a"]},
            {"groups": ["b", "c"]},
            {"groups": ["b", "c"]},
            {"groups": ["b", "c"]},
        ),
        ({"groups": ["a"]}, {"groups": []}, {"groups": None}, {"groups": []}),
        ({"repos": ["elsewhere"]}, {"repos": None}, {"repos": None}, {"repos": None}),
        ({}, {"give_up_after": "never"}, {"give_up_after": "never"}, {"give_up_after": "never"}),
        ({"ssh": "old"}, {"ssh": "new"}, {"ssh": "new"}, {"ssh": "new"}),
    ],
    ids=[
        "disable",
        "enable",
        "capacity",
        "capacity-null",
        "groups-replace",
        "groups-empty",
        "repos-null",
        "give-up-after",
        "ssh-host",
    ],
)
def test_editing_a_machine_changes_what_was_sent_and_writes_the_file(
    client, start, patch, stored, row
):
    _seed(client, {"home": start})
    reply = client.patch("/api/workers/home", json=patch)
    assert reply.status_code == 200, reply.text
    entry = _file(client)["workers"]["home"]
    assert {k: entry.get(k) for k in stored} == stored
    assert {k: reply.json()["worker"][k] for k in row} == row
    assert {k: _row(client, "home")[k] for k in row} == row


def test_an_empty_edit_is_a_200_and_leaves_the_file_alone(client):
    _seed(client, {"home": {"capacity": 2}})
    path = client.app.state.templates_dir / "workers.yaml"
    before, stamp = path.read_text(), path.stat().st_mtime_ns
    reply = client.patch("/api/workers/home", json={})
    assert reply.status_code == 200 and reply.json()["worker"]["capacity"] == 2
    assert path.read_text() == before and path.stat().st_mtime_ns == stamp


@pytest.mark.parametrize(
    ("name", "patch", "status", "text"),
    [
        ("local", {"ssh": "box"}, 422, "the local worker takes only enabled, capacity and groups"),
        ("local", {"repos": ["x"]}, 422, "not repos"),
        ("dials", {"ssh": "box"}, 422, "dials in; it has no ssh host"),
        ("dials", {"enabled": None}, 422, "enabled cannot be null"),
        ("dials", {"give_up_after": None}, 422, "give_up_after cannot be null"),
        ("dials", {"groups": None}, 422, "groups cannot be null"),
        ("dials", {"capacity": 0}, 422, "capacity"),
        ("dials", {"give_up_after": "soon"}, 422, "give_up_after"),
        ("dials", {"repos": ["no-such-repo"]}, 422, "no-such-repo"),
        ("dials", {"repos": []}, 422, "at least one repo"),
        ("dials", {"groups": ["other"]}, 422, "also a machine name"),
        ("nobody", {"enabled": False}, 404, "no worker named 'nobody'"),
    ],
    ids=[
        "local-ssh",
        "local-repos",
        "dial-in-ssh",
        "enabled-null",
        "give-up-null",
        "groups-null",
        "capacity-zero",
        "bad-duration",
        "unknown-repo",
        "no-repos",
        "group-named-like-a-machine",
        "unknown-machine",
    ],
)
def test_a_bad_edit_is_refused_in_words_and_the_file_is_unchanged(
    client, name, patch, status, text
):
    _seed(client, {"dials": {}, "other": {}})
    path = client.app.state.templates_dir / "workers.yaml"
    before = path.read_text()
    reply = client.patch(f"/api/workers/{name}", json=patch)
    assert reply.status_code == status and text in str(reply.json()["detail"])
    assert path.read_text() == before


def test_the_local_workers_edit_writes_only_the_local_block_and_answers_its_row(client):
    _seed(client, {"home": {"capacity": 2}})
    reply = client.patch(
        "/api/workers/local", json={"enabled": False, "capacity": 2, "groups": ["farm"]}
    )
    assert reply.status_code == 200, reply.text
    assert (reply.json()["worker"]["name"], reply.json()["worker"]["enabled"]) == ("local", False)
    assert _file(client)["local"] == {"enabled": False, "capacity": 2, "groups": ["farm"]}
    assert _file(client)["workers"]["home"]["capacity"] == 2
    assert not client.app.state.workers_hub.free("home", [])  # offline, but the file still loads
    assert (
        client.patch("/api/workers/local", json={"capacity": None}).json()["worker"]["capacity"]
        is None
    )
    assert "capacity" not in _file(client)["local"]
    assert client.patch("/api/workers/local", json={"groups": ["home"]}).status_code == 422


def _claim(client, name, key):
    """A slot held on `name` for step `key`, as a launch holds it before its session row runs."""
    return client.app.state.workers_hub.adopt(name, key)


def _item(client, repo, title):
    return client.post("/api/work-items", json={"repo": str(repo), "title": title}).json()["id"]


def test_a_host_change_is_refused_while_a_step_holds_the_machine(client):
    _seed(client, {"boxed": {"ssh": "old"}})
    _claim(client, "boxed", "w1/s1")
    refused = client.patch("/api/workers/boxed", json={"ssh": "new"})
    assert refused.status_code == 409
    assert "Change its host once they end." in refused.json()["detail"]
    assert _file(client)["workers"]["boxed"]["ssh"] == "old"
    assert client.patch("/api/workers/boxed", json={"ssh": "old", "capacity": 2}).status_code == 200


def test_rotating_a_token_stops_the_old_one_and_drops_the_connection(client):
    token = client.post("/api/workers", json={"name": "home"}).json()["token"]
    hub = client.app.state.workers_hub
    client.portal.call(lambda: attach(hub, Peer(), "home", caps=frozenset(), token=token))
    assert client.get("/api/workers").json()["workers"][1]["online"] is True
    old_hash = _file(client)["workers"]["home"]["token_sha256"]

    reply = client.post("/api/workers/home/rotate-token")
    assert reply.status_code == 200, reply.text
    body = reply.json()
    assert body["token"] != token and body["token"] in body["serve_command"]
    assert body["worker"]["name"] == "home" and "note" in body
    stored = registry.WorkersFile.load(client.app.state.templates_dir / "workers.yaml")
    assert stored.workers["home"].token_sha256 != old_hash
    assert registry.find_by_token(stored, body["token"]) == "home"
    assert registry.find_by_token(stored, token) is None

    async def offline():
        while hub.snapshot()[0]["online"]:  # `_drop` closes the link in a background task
            await asyncio.sleep(0.01)

    client.portal.call(lambda: asyncio.wait_for(offline(), 5))


@pytest.mark.parametrize(
    ("name", "status"),
    [("boxed", 409), ("local", 409), ("nobody", 404)],
    ids=["ssh", "local", "unknown"],
)
def test_only_a_dial_in_machine_has_a_token_to_rotate(client, name, status):
    _seed(client, {"boxed": {"ssh": "box"}})
    assert client.post(f"/api/workers/{name}/rotate-token").status_code == status


def test_a_machine_that_holds_a_step_cannot_be_removed_even_before_its_session_runs(client, repo):
    _seed(client, {"home": {}})
    wid = _item(client, repo, "Build the thing")
    claim = _claim(client, "home", f"{wid}/s1")
    row = _row(client, "home")
    assert row["holders"] == [{"id": wid, "title": "Build the thing", "steps": 1}]
    refused = client.delete("/api/workers/home")
    assert refused.status_code == 409 and refused.json()["detail"] == row["remove_blocked"]
    assert "home is running 1 step of Build the thing." in row["remove_blocked"]
    assert "kraft admin worker disable home" in row["remove_blocked"]
    assert "home" in _file(client)["workers"]
    client.app.state.workers_hub.release(claim)
    assert client.delete("/api/workers/home").json() == {"removed": "home"}


def test_a_session_row_alone_holds_a_machine_and_the_claim_is_not_counted_twice(client, repo):
    _seed(client, {"home": {}})
    wid = _item(client, repo, "Build the thing")
    _seed_session(client, wid, session_id="s1", hook_point="build.main.x", runner="home")
    _seed_session(client, wid, session_id="s2", hook_point="build.main.y", runner="home")
    _claim(client, "home", f"{wid}/s1")
    assert _row(client, "home")["holders"] == [{"id": wid, "title": "Build the thing", "steps": 2}]
    assert "2 steps of Build the thing" in client.delete("/api/workers/home").json()["detail"]


def test_the_refusal_names_three_items_then_counts_the_rest(client, repo):
    _seed(client, {"home": {}})
    for i in range(5):
        _claim(client, "home", f"gone{i}/s1")  # items that no longer exist: their id stands in
    detail = client.delete("/api/workers/home").json()["detail"]
    assert "home is running 5 steps of gone0, gone1, gone2 and 2 more." in detail


def test_a_machine_with_a_step_waiting_for_it_can_be_removed_and_gives_no_run_up(client):
    _seed(client, {"home": {}})
    row = _row(client, "home")
    assert row["holders"] == [] and row["remove_blocked"] is None
    assert client.delete("/api/workers/home").json() == {"removed": "home"}


# ── the dial-in WebSocket ──

REMOTE = ("203.0.113.9", 50000)
pytestmark_remote = pytest.mark.api_client(peer=REMOTE, host="0.0.0.0")


def _register(client, token="kwt_machine", name="home"):
    """The machine `home` with `token`, put straight into the running hub: the
    peer here is not on this machine, so it cannot call the REST routes."""
    client.app.state.workers_hub.apply(
        registry.WorkersFile.model_validate(
            {"workers": {name: {"token_sha256": registry.hash_token(token), "repos": None}}}
        )
    )
    return token


def _password(client, monkeypatch, password="pw"):
    st = client.app.state
    monkeypatch.setattr(
        st, "access", {**st.access, "password_hash": auth_mod.hash_password(password)}
    )


def _hello(token, protocol_version=protocol.PROTOCOL_VERSION):
    return json.dumps(
        {"t": "hello", "protocol": protocol_version, "kraft": "2.3.0", "token": token}
    )


def _rows(client):
    return client.app.state.workers_hub.snapshot()


@pytestmark_remote
def test_a_machine_with_its_token_connects_without_any_login_cookie(client, monkeypatch):
    _password(client, monkeypatch)
    token = _register(client)
    assert client.get("/api/workers").status_code == 401  # a person needs to log in...
    with client.websocket_connect("/api/workers/connect") as ws:  # ...a machine does not
        ws.send_text(_hello(token))
        welcome = json.loads(ws.receive_text())
        assert welcome["t"] == "welcome" and welcome["name"] == "home"
        assert _rows(client)[0]["online"] is True


@pytestmark_remote
@pytest.mark.parametrize(
    "token",
    ["kwt_wrong", "", 123, {"a": 1}, ["kwt_x"]],
    ids=["wrong-token", "no-token", "int-token", "object-token", "list-token"],
)
def test_a_wrong_or_missing_token_is_refused(client, monkeypatch, token):
    _password(client, monkeypatch)
    _register(client)
    with client.websocket_connect("/api/workers/connect") as ws:
        ws.send_text(_hello(token))
        assert json.loads(ws.receive_text())["t"] == "refused"
    assert not _rows(client)[0]["online"]


@pytestmark_remote
def test_a_machine_on_a_newer_protocol_is_refused_with_both_versions(client, monkeypatch):
    _password(client, monkeypatch)
    token = _register(client)
    with client.websocket_connect("/api/workers/connect") as ws:
        ws.send_text(_hello(token, protocol_version=99))
        refused = json.loads(ws.receive_text())
    assert refused["t"] == "refused"
    assert (refused["server_protocol"], refused["machine_protocol"]) == (
        protocol.PROTOCOL_VERSION,
        99,
    )
    row = _rows(client)[0]
    assert not row["online"] and "machine speaks 99" in row["reason"]


@pytestmark_remote
def test_the_route_does_not_open_the_perimeter(client):
    """A server with no password refuses a non-local peer outright, token or not."""
    token = _register(client)
    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect("/api/workers/connect") as ws:
            ws.send_text(_hello(token))
            ws.receive_text()
    assert closed.value.code == 1008
