"""`/api/workers`: managing machines, and the dial-in WebSocket that
authenticates by a per-machine token and nothing else."""

from __future__ import annotations

import json

import pytest
from starlette.websockets import WebSocketDisconnect

from kraft import auth as auth_mod
from kraft.config import read_yaml
from kraft.remote import protocol, registry


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
    listed = client.get("/api/workers").json()["workers"]
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


def test_a_name_in_use_is_a_409(client):
    assert client.post("/api/workers", json={"name": "home"}).status_code == 201
    assert client.post("/api/workers", json={"name": "home"}).status_code == 409


def test_removing_a_machine_forgets_it_and_404s_when_unknown(client):
    client.post("/api/workers", json={"name": "home"})
    assert client.delete("/api/workers/home").json() == {"removed": "home"}
    assert client.get("/api/workers").json() == {"workers": []}
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
    assert client.get("/api/workers").json() == {"workers": []}


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
    assert [w["name"] for w in client.get("/api/workers").json()["workers"]] == ["boxed"]
    path.write_text("workers:\n  boxed:\n    capacity: 0\n")
    refused = client.post("/api/templates/reload").json()["refused_workers"]
    assert refused and "capacity" in refused
    assert client.app.state.invalid_workers == refused
    assert [w["name"] for w in client.get("/api/workers").json()["workers"]] == ["boxed"]


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
@pytest.mark.parametrize("token", ["kwt_wrong", ""], ids=["wrong-token", "no-token"])
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
    assert (refused["server_protocol"], refused["machine_protocol"]) == (1, 99)
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
