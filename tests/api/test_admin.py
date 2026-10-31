"""`/api/apply` (W13 H): what is saved but not running, the one reload that
clears the disk kind, the restart door, and the `apply_changed` live message."""

from __future__ import annotations

import os
import plistlib
import sys
import threading
from pathlib import Path

import pytest
import yaml

from kraft import apply as apply_mod
from kraft import forges as forges_mod
from kraft import storage
from kraft.cli import admin

pytestmark = pytest.mark.api_client(default_setup=False)


@pytest.fixture(autouse=True)
def _no_env_override(monkeypatch):
    monkeypatch.delenv("KRAFT_HOST", raising=False)
    monkeypatch.delenv("KRAFT_PORT", raising=False)
    monkeypatch.delenv("XPC_SERVICE_NAME", raising=False)
    monkeypatch.delenv("INVOCATION_ID", raising=False)


def pending(client) -> dict:
    return client.get("/api/apply").json()


def ids(items) -> list[str]:
    return [i["id"] for i in items]


def put_file(templates_dir, name, text):
    (templates_dir / name).write_text(text)


# ── what is pending ──


def test_nothing_is_pending_on_a_fresh_server(client):
    assert pending(client) == {"restart": [], "reload": [], "managed": False}


@pytest.mark.parametrize("managed", [True, False])
def test_the_answer_says_whether_a_restart_can_be_offered(client, monkeypatch, managed):
    monkeypatch.setattr("kraft.apply.managed", lambda: managed)
    assert pending(client)["managed"] is managed
    assert client.post("/api/apply/reload").json()["managed"] is managed


def test_a_saved_port_that_differs_from_the_bound_one_needs_a_restart(client, templates_dir):
    client.put("/api/access", json={"port": 9123})
    got = pending(client)["restart"]
    assert ids(got) == ["access.port"]
    assert got[0]["file"] == "access.yaml"
    # Saving the port the server is on again clears it.
    client.put("/api/access", json={"port": client.app.state.bound_port})
    assert pending(client)["restart"] == []


def test_an_env_override_means_a_restart_changes_nothing(client, monkeypatch):
    client.put("/api/access", json={"port": 9123})
    monkeypatch.setenv("KRAFT_PORT", "8765")
    assert pending(client)["restart"] == []


def test_a_changed_file_is_pending_by_its_bytes_not_its_mtime(client, templates_dir):
    policy = templates_dir / "policy.yaml"
    before = policy.read_text() if policy.exists() else ""
    put_file(templates_dir, "policy.yaml", before)  # same bytes, new mtime
    os.utime(policy, (1, 1))
    assert pending(client)["reload"] == []

    put_file(templates_dir, "policy.yaml", before + "\n# edited by hand\n")
    got = pending(client)["reload"]
    assert ids(got) == ["disk:policy.yaml"]
    assert "problem" not in got[0]


@pytest.mark.parametrize(
    ("file", "text", "running", "problem"),
    [
        ("policy.yaml", "defaults: [not, a, mapping]\n", None, ""),
        # the running accounts are the baseline, not forges.yaml as it is on disk
        ("forges.yaml", "accounts: {}\n", ["a"], "'a' names no account"),
    ],
    ids=["policy-not-a-mapping", "forges-drops-an-account-a-repo-names"],
)
def test_a_file_that_would_not_load_carries_its_problem(
    client, templates_dir, file, text, running, problem
):
    if running:
        client.app.state.forges = forges_mod.ForgesFile(
            accounts={
                n: forges_mod.ForgeAccount(
                    forge="github",
                    web_url="https://github.com",
                    client="api",
                    token=forges_mod.TokenSource(**{"from": "file", "file": "/s/tok"}),
                )
                for n in running
            }
        )
        (templates_dir / "repos.yaml").write_text(
            "repos:\n  - path: /r\n    forge: github\n    forge_account: a\n"
        )
    put_file(templates_dir, file, text)
    (item,) = pending(client)["reload"]
    assert item["id"] == f"disk:{file}" and item["problem"] and problem in item["problem"]


def test_a_hand_edited_workers_file_is_pending_and_a_broken_one_carries_its_problem(
    client, templates_dir
):
    put_file(templates_dir, "workers.yaml", "workers:\n  boxed:\n    ssh: boxed\n")
    assert ids(pending(client)["reload"]) == ["disk:workers.yaml"]
    put_file(templates_dir, "workers.yaml", "workers:\n  boxed:\n    capacity: 1\n")
    (item,) = pending(client)["reload"]
    assert item["id"] == "disk:workers.yaml" and "exactly one of" in item["problem"]


def test_a_chain_file_added_on_disk_is_pending(client, templates_dir):
    put_file(templates_dir, "chains/extra.yaml", "id: extra\nnodes: []\n")
    assert "disk:chains/extra.yaml" in ids(pending(client)["reload"])


# ── the reload ──


def test_reload_rereads_intake_and_clears_what_it_loaded(client, templates_dir):
    put_file(templates_dir, "intake.yaml", "enabled: true\ninterval_s: 77\n")
    assert ids(pending(client)["reload"]) == ["disk:intake.yaml"]

    after = client.post("/api/apply/reload")
    assert after.status_code == 200
    assert after.json() == {"restart": [], "reload": [], "managed": False}
    assert client.app.state.intake["interval_s"] == 77


def test_reload_that_adds_a_storage_limit_measures_at_once(client, templates_dir, monkeypatch):
    """Nothing measured while no limit was set, so the new limit would be
    judged against a stale figure until the next ten-minute tick."""
    walked = threading.Event()

    def measure(base, stop=None):
        walked.set()
        return storage.Usage("2026-01-01T00:00:00+00:00", 0, {}, {})

    monkeypatch.setattr(storage, "measure", measure)
    policy = templates_dir / "policy.yaml"
    limit = "\nstorage:\n  worktrees:\n    limit: 10G\n"
    put_file(templates_dir, "policy.yaml", policy.read_text() + limit)

    assert client.post("/api/apply/reload").status_code == 200

    assert client.app.state.policy.storage_limit_bytes == 10 * 1024**3
    # The reload kicks the walk and answers without waiting for it.
    assert walked.wait(10)


def test_templates_reload_is_the_same_reload(client, templates_dir):
    put_file(templates_dir, "intake.yaml", "enabled: true\ninterval_s: 88\n")
    assert client.post("/api/templates/reload").status_code == 200
    assert client.app.state.intake["interval_s"] == 88
    assert pending(client)["reload"] == []


def test_a_refused_file_stays_pending_with_its_problem(client, templates_dir):
    put_file(templates_dir, "policy.yaml", "defaults: [not, a, mapping]\n")
    after = client.post("/api/apply/reload").json()
    assert ids(after["reload"]) == ["disk:policy.yaml"] and after["reload"][0]["problem"]


# ── the restart door ──


def _installed(monkeypatch, tmp_path, platform):
    """The service files `kraft admin install-service` writes, and the env var a
    process that service started has."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(sys, "platform", platform)
    if platform == "darwin":
        label = plistlib.loads(admin._write_launchd_plist("kraft").read_bytes())["Label"]
        monkeypatch.setenv("XPC_SERVICE_NAME", label)
    else:
        assert admin._write_systemd_unit("kraft").name
        monkeypatch.setenv("INVOCATION_ID", "abc")


@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_a_server_the_installed_service_started_restarts_with_a_202(
    client, monkeypatch, tmp_path, platform
):
    _installed(monkeypatch, tmp_path, platform)
    ran = []
    monkeypatch.setattr(client.app.state, "restart_runner", ran.append, raising=False)
    r = client.post("/api/apply/restart")
    assert r.status_code == 202
    assert ran == [client.app.state]


def test_confirming_with_no_update_waiting_is_a_409(client):
    r = client.post("/api/update/confirm")
    assert (r.status_code, r.json()["detail"]) == (409, "no update is waiting to be confirmed")


def test_the_apply_chips_restart_lifts_a_failed_update(client, monkeypatch, tmp_path):
    """A person asking again tries what failed afresh (`when_free.clear_failure`)."""
    from kraft import when_free

    _installed(monkeypatch, tmp_path, "linux")
    monkeypatch.setattr(client.app.state, "restart_runner", lambda st: None, raising=False)
    when_free.record_outcome(client.app.state, status="failed", key="v2.3.0", detail="x")
    assert when_free.failed_key(client.app.state) == "v2.3.0"

    assert client.post("/api/apply/restart").status_code == 202

    assert when_free.failed_key(client.app.state) is None


@pytest.mark.parametrize(
    ("failure", "says"),
    [(OSError("no such file: kraft"), "no such file: kraft"), (SystemExit(2), "2")],
    ids=["the-executable-cannot-start", "the-runner-exits"],
)
def test_a_restart_that_cannot_start_is_a_500_naming_why(
    client, monkeypatch, tmp_path, failure, says
):
    _installed(monkeypatch, tmp_path, "linux")

    def runner(st):
        raise failure

    monkeypatch.setattr(client.app.state, "restart_runner", runner, raising=False)
    r = client.post("/api/apply/restart")
    assert r.status_code == 500
    assert r.json()["detail"] == f"could not start the restart: {says}"


@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_a_server_started_from_a_terminal_is_told_to_restart_it_there(
    client, monkeypatch, tmp_path, platform
):
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.delenv("XPC_SERVICE_NAME", raising=False)
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    ran = []
    monkeypatch.setattr(client.app.state, "restart_runner", ran.append, raising=False)
    r = client.post("/api/apply/restart")
    assert (r.status_code, r.json()["detail"]) == (409, "started from a terminal: restart it there")
    assert ran == []


def test_a_service_label_that_is_not_ours_is_not_managed(client, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setenv("XPC_SERVICE_NAME", "com.someone.else")
    ran = []
    monkeypatch.setattr(client.app.state, "restart_runner", ran.append, raising=False)
    assert client.post("/api/apply/restart").status_code == 409
    assert ran == []


def test_the_restart_is_a_detached_kraft_admin_restart_logged_to_the_server_log(
    client, monkeypatch
):
    seen = {}
    monkeypatch.setattr(admin, "_kraft_executable", lambda: "/bin/kraft")
    monkeypatch.setattr(apply_mod.subprocess, "Popen", lambda cmd, **kw: seen.update(cmd=cmd, **kw))
    apply_mod.spawn_restart(client.app.state)
    assert seen["cmd"] == ["/bin/kraft", "admin", "restart"]
    assert seen["start_new_session"] is True
    assert seen["stdout"].name.endswith("server.log")


def test_saving_access_never_restarts(client, monkeypatch):
    ran = []
    monkeypatch.setattr(client.app.state, "restart_runner", ran.append, raising=False)
    client.put("/api/access", json={"port": 9123})
    assert ran == []


# ── apply_changed ──


def test_apply_changed_goes_out_when_the_pending_ids_change_and_not_otherwise(
    client, templates_dir
):
    bc = client.app.state.broadcaster
    live = bc.register(live=True)
    app = client.app
    put_file(templates_dir, "intake.yaml", "enabled: true\ninterval_s: 61\n")

    apply_mod.notify(app)
    frame = live.queue.get_nowait()
    assert (frame["type"], frame["payload"]) == ("apply_changed", {"restart": 0, "reload": 1})
    apply_mod.notify(app)
    assert live.queue.empty()

    client.post("/api/apply/reload")
    assert live.queue.get_nowait()["payload"] == {"restart": 0, "reload": 0}


def test_saving_access_announces_the_restart_item(client):
    live = client.app.state.broadcaster.register(live=True)
    client.put("/api/access", json={"port": 9123})
    assert live.queue.get_nowait()["payload"] == {"restart": 1, "reload": 0}


def test_publishing_a_draft_announces_what_is_left_pending(client, templates_dir):
    live = client.app.state.broadcaster.register(live=True)
    put_file(templates_dir, "chains/extra.yaml", "id: extra\nnodes: []\n")
    base = "/api/drafts/intake/intake"
    client.post(f"{base}/ops", json={"ops": [{"op": "set_intake", "patch": {"interval_s": 90}}]})
    assert client.post(f"{base}/publish").status_code == 200
    got = []
    while not live.queue.empty():
        got.append(live.queue.get_nowait())
    assert [f["payload"] for f in got if f["type"] == "apply_changed"] == [
        {"restart": 0, "reload": 1}
    ]
    assert yaml.safe_load((templates_dir / "intake.yaml").read_text())["interval_s"] == 90


_TWO_DEFAULTS = (
    "accounts:\n"
    "  a: {forge: gitlab, web_url: 'https://g.example', default_for_host: true}\n"
    "  b: {forge: gitlab, web_url: 'https://g.example', default_for_host: true}\n"
)


def test_a_hand_edited_forges_yaml_is_reloaded_and_a_broken_one_degrades_health(client):
    st = client.app.state
    path = st.templates_dir / "forges.yaml"
    path.write_text("accounts:\n  a: {forge: github, web_url: 'https://github.com'}\n")
    assert client.post("/api/templates/reload").json()["refused_forges"] is None
    assert list(st.forges.accounts) == ["a"]
    path.write_text(_TWO_DEFAULTS)
    refused = client.post("/api/templates/reload").json()["refused_forges"]
    assert refused and "forges.yaml" in refused
    assert list(st.forges.accounts) == ["a"]
    health = client.get("/api/health").json()
    assert health["status"] == "degraded" and health["invalid_forges"] == refused
    path.write_text("accounts: {}\n")
    assert client.post("/api/templates/reload").json()["refused_forges"] is None
    assert client.get("/api/health").json()["invalid_forges"] is None


_B = "b: {forge: github, web_url: 'https://github.com'}"


@pytest.mark.parametrize(
    "running, accounts, applied, says, refused_it, passthrough",
    [
        pytest.param(
            ["a"],
            _B,
            ["a"],
            "'a' names no account",
            True,
            [],
            id="new-mismatch-is-refused",
        ),
        pytest.param(
            ["a"],
            "a: {forge: github, web_url: 'https://github.com'}\n  "
            "b: {forge: github, web_url: 'https://github.com', client: api, "
            "token: {from: env, env: CORP_GL}}",
            ["a"],
            "env_passthrough names CORP_GL",
            True,
            ["CORP_GL"],
            id="new-env-source-a-repo-passes-through-is-refused",
        ),
        # applied: the old mismatch is named on /health, and the reload is no refusal
        pytest.param(
            [],
            _B,
            ["b"],
            "'a' names no account",
            False,
            [],
            id="old-mismatch-does-not-block-an-unrelated-edit",
        ),
        # nothing runs while forges.yaml has never loaded: any file that loads is taken
        pytest.param(
            None,
            "a: {forge: gitlab, web_url: 'https://gitlab.example'}",
            ["a"],
            "'a' says forge: gitlab",
            False,
            [],
            id="unreadable-since-startup-takes-a-file-that-loads",
        ),
    ],
)
def test_a_forges_yaml_that_leaves_a_repo_without_its_account_is_refused_on_reload(
    client, monkeypatch, running, accounts, applied, says, refused_it, passthrough
):
    from kraft.adapters.forge import credentials

    monkeypatch.setattr(credentials, "_TAKEN", {})
    monkeypatch.setattr(credentials, "_SECRET_FILES", ())
    monkeypatch.setenv("CORP_GL", "tok-0123456789")
    st = client.app.state
    st.forges = (
        None
        if running is None
        else forges_mod.ForgesFile(
            accounts={
                n: forges_mod.ForgeAccount(
                    forge="github",
                    web_url="https://github.com",
                    client="api",
                    token=forges_mod.TokenSource(**{"from": "file", "file": "/s/tok"}),
                )
                for n in running
            }
        )
    )
    (st.templates_dir / "repos.yaml").write_text(
        "repos:\n  - path: /r\n    forge: github\n    forge_account: a\n"
        f"    env_passthrough: {passthrough}\n"
    )
    (st.templates_dir / "forges.yaml").write_text(f"accounts:\n  {accounts}\n")
    refused = client.post("/api/templates/reload").json()["refused_forges"]
    assert (refused is not None) is refused_it
    assert says in st.invalid_forges
    assert st.forges is not None and list(st.forges.accounts) == applied
    # A refused edit that drops a running account keeps its token file protected.
    if refused_it:
        assert credentials.secret_files() == (Path("/s/tok"),)
    # Taken even when the edit is refused: no child may inherit a token the file names.
    assert ("CORP_GL" not in os.environ) is ("CORP_GL" in accounts)


@pytest.mark.parametrize(
    ("state", "expires_on", "status"),
    [("ok", None, "ok"), ("refused", "2099-01-01", "degraded")],
    ids=["ok", "refused"],
)
def test_health_reports_a_tokens_state_and_never_who_it_is(client, state, expires_on, status):
    from kraft.adapters.forge import probe

    client.app.state.forge_probes["a"] = probe.Probe(
        "a", "2026-01-01T00:00:00+00:00", state, "bad tok-0123456789",
        login="octocat", scopes=("repo",), expires_on=expires_on,
    )  # fmt: skip
    body = client.get("/api/health").json()
    assert body["status"] == status
    assert body["forge_tokens"] == {"a": {"state": state, "expires_on": expires_on}}
    for secret in ("octocat", "repo", "tok-0123456789"):
        assert secret not in str(body["forge_tokens"])


def test_reload_forgets_cached_tokens(client, monkeypatch):
    from kraft.adapters.forge import credentials

    seeded = {("corp", "{}"): "tok-0123456789"}
    monkeypatch.setattr(credentials, "_TOKENS", seeded)
    st = client.app.state
    (st.templates_dir / "forges.yaml").write_text("accounts: {}\n")
    client.post("/api/templates/reload")
    assert seeded == {}
