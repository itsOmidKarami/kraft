"""`/api/workers`: managing machines, and the dial-in WebSocket that
authenticates by a per-machine token and nothing else."""

from __future__ import annotations

import json

import pytest
from starlette.websockets import WebSocketDisconnect

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
        "groups": [],
        "repos": None,
        "last_seen": None,
        "kraft_version": "direct",
        "give_up_after": "never",
    }
    assert client.delete("/api/workers/local").status_code == 404


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


def test_removing_a_machine_gives_up_the_runs_it_held(client, monkeypatch):
    gone = []

    async def machine_removed(name):
        gone.append(name)

    monkeypatch.setattr(client.app.state.workers_hub.runs, "machine_removed", machine_removed)
    client.post("/api/workers", json={"name": "home"})
    client.delete("/api/workers/home")
    assert gone == ["home"]


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
    ],
    ids=["list", "add", "remove"],
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
