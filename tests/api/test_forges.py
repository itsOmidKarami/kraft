"""`/api/forges`: naming forge accounts, and asking whether their CLI is signed in."""

from __future__ import annotations

import subprocess

import pytest
from support.harness import connect_repo

from kraft.adapters.forge import git as forge_git
from kraft.adapters.forge.git import CliUnavailable
from kraft.config import read_yaml

GL = {"name": "corp", "forge": "gitlab", "web_url": "https://code.corp.example/"}


def _file(client):
    return client.app.state.templates_dir / "forges.yaml"


def test_add_list_remove_round_trip(client):
    reply = client.post("/api/forges", json={**GL, "default_for_host": True})
    assert reply.status_code == 201, reply.text
    assert reply.json()["api_url"] == "https://code.corp.example/api/v4"
    assert read_yaml(_file(client))["accounts"]["corp"] == {
        "forge": "gitlab",
        "web_url": "https://code.corp.example",
        "default_for_host": True,
    }
    assert _file(client).stat().st_mode & 0o777 == 0o600
    assert list(client.get("/api/forges").json()["accounts"]) == ["corp"]
    assert "corp" in client.app.state.forges.accounts
    assert client.delete("/api/forges/corp").status_code == 204
    assert client.get("/api/forges").json() == {"accounts": {}}
    assert client.delete("/api/forges/corp").status_code == 404


@pytest.mark.parametrize(
    ("second", "status", "text"),
    [
        (GL, 409, "already exists"),
        ({**GL, "name": "plain", "web_url": "http://code.corp.example"}, 422, "https"),
        (
            {**GL, "name": "other", "default_for_host": True},
            422,
            "default_for_host",
        ),
        ({**GL, "name": "Bad Name"}, 422, "name"),
    ],
    ids=["duplicate-409", "http-422", "second-default-422", "bad-name-422"],
)
def test_a_refused_add_writes_nothing(client, second, status, text):
    client.post("/api/forges", json={**GL, "default_for_host": True})
    before = _file(client).read_text()
    reply = client.post("/api/forges", json=second)
    assert reply.status_code == status and text in reply.text
    assert _file(client).read_text() == before


def test_remove_of_an_account_a_repo_names_is_409_listing_it(client, repo):
    client.post("/api/forges", json=GL)
    connect_repo(repo, forge_account="corp")
    reply = client.delete("/api/forges/corp")
    assert reply.status_code == 409
    assert reply.json()["repos"] == [str(repo.resolve())]
    assert "corp" in read_yaml(_file(client))["accounts"]


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("get", "/api/forges", None),
        ("post", "/api/forges", GL),
        ("delete", "/api/forges/x", None),
        ("post", "/api/forges/x/test", None),
    ],
    ids=["list", "add", "remove", "test"],
)
def test_a_kraft_session_is_refused(client, method, path, body):
    reply = getattr(client, method)(
        path, headers={"X-Kraft-Session-Id": "w1/s1"}, **({"json": body} if body else {})
    )
    assert reply.status_code == 403


def _done(code, out="", err=""):
    return subprocess.CompletedProcess([], code, out, err)


@pytest.mark.parametrize(
    ("forge", "answer", "argv", "ok", "text"),
    [
        (
            "github",
            _done(0, err="Logged in to github.com"),
            ["gh", "auth", "status", "-h", "code.corp.example"],
            True,
            "Logged in",
        ),
        (
            "gitlab",
            _done(1, out="x", err="not logged in"),
            ["glab", "auth", "status", "--hostname", "code.corp.example"],
            False,
            "xnot logged in",
        ),
        (
            "gitlab",
            CliUnavailable("glab is not installed or not on PATH"),
            None,
            False,
            "not installed",
        ),
    ],
    ids=["gh-ok", "glab-failed", "cli-missing"],
)
def test_test_runs_the_cli_auth_status_for_the_host(
    client, monkeypatch, forge, answer, argv, ok, text
):
    calls = []

    async def run_cli(repo, args, **kw):
        calls.append(args)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(forge_git, "run_cli", run_cli)
    client.post("/api/forges", json={**GL, "forge": forge})
    body = client.post("/api/forges/corp/test").json()
    assert body["ok"] is ok and text in body["output"]
    assert calls == ([argv] if argv else [calls[0]])
    assert client.post("/api/forges/nope/test").status_code == 404
