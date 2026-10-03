"""The settings screens' documents over HTTP: theme and access control
(design 5e), intake, and policy (design 5d)."""

from __future__ import annotations

import functools
import os
import sqlite3
from pathlib import Path

import pytest
import yaml
from support.harness import connect_repo, make_repo

from kraft import config, store
from kraft.paths import RunDirs

#: No default repo entry for an unconnected repo (`support.api._client`): these read real config.
pytestmark = pytest.mark.api_client(default_setup=False)


# ── theme ──


def test_get_theme_defaults_to_nocturne_dark(client):
    # With no theme.yaml: nocturne's look in the new colour model, dark.
    body = client.get("/api/theme").json()
    assert "palette" not in body
    assert (body["surface"], body["accent"], body["colour_amount"]) == ("ink", "violet", "full")
    assert body["mode"] == "dark"
    assert body["density"] == "compact"
    assert body["board"] == {"group_by": "status", "show_done": 5, "open_in": "peek"}


def test_put_theme_round_trips_through_the_yaml(client, templates_dir):
    body = {
        "surface": "moss",
        "accent": "green",
        "colour_amount": "subtle",
        "mode": "light",
        "density": "comfortable",
        "board": {"group_by": "repo", "show_done": 10, "open_in": "full"},
    }
    assert client.put("/api/theme", json=body).status_code == 200
    assert client.get("/api/theme").json().items() >= body.items()
    assert yaml.safe_load((templates_dir / "theme.yaml").read_text()).items() >= body.items()


def test_put_theme_defaults_density_and_board_when_omitted(client):
    resp = client.put("/api/theme", json={"mode": "dark"})
    assert resp.json()["density"] == "compact"
    assert resp.json()["board"] == {"group_by": "status", "show_done": 5, "open_in": "peek"}


def test_a_board_grouped_by_template_reads_as_chain(client, templates_dir):
    """`template` named the chain grouping before 2.0; the file is read as it
    was, and the answer says `chain`."""
    (templates_dir / "theme.yaml").write_text("board: {group_by: template}\n")
    assert client.get("/api/theme").json()["board"]["group_by"] == "chain"


def test_get_theme_fills_defaults_for_a_pre_existing_file(client, templates_dir):
    # A palette written after startup (an old tab, a hand edit) is still read
    # until the next start converts it.
    (templates_dir / "theme.yaml").write_text("palette: rose\nmode: light\n")
    body = client.get("/api/theme").json()
    assert (
        body.items()
        >= {
            "surface": "ink",
            "accent": "violet",
            "mode": "light",
            "density": "compact",
            "board": {"group_by": "status", "show_done": 5, "open_in": "peek"},
        }.items()
    )


@pytest.mark.parametrize(
    "body",
    [
        {"palette": "cerulean", "mode": "dark"},
        {"surface": "nocturne"},
        {"mode": "twilight"},
        {"board": {"group_by": "priority", "show_done": 5, "open_in": "peek"}},
    ],
    ids=["an-unknown-palette", "an-unknown-surface", "an-unknown-mode", "an-unknown-group-by"],
)
def test_put_theme_rejects_an_unknown_value(client, templates_dir, body):
    assert client.put("/api/theme", json=body).status_code == 422
    assert not (templates_dir / "theme.yaml").exists()


def test_put_theme_merges_and_keeps_keys_the_body_leaves_out(client, templates_dir):
    # Every control sends only its own key.
    client.put("/api/theme", json={"mode": "light", "density": "comfortable"})
    resp = client.put("/api/theme", json={"surface": "moss"})
    assert resp.status_code == 200
    on_disk = yaml.safe_load((templates_dir / "theme.yaml").read_text())
    assert (on_disk["mode"], on_disk["density"], on_disk["surface"]) == (
        "light",
        "comfortable",
        "moss",
    )
    assert resp.json()["derived"] is False


def test_put_theme_full_object_keeps_the_v2_keys(client, templates_dir):
    # A shipped Appearance tab left open across the upgrade spreads the theme
    # it loaded and PUTs all of it, with its own palette.
    client.put("/api/theme", json={"surface": "slate", "accent": "rose", "colour_amount": "full"})
    loaded = client.get("/api/theme").json()
    assert client.put("/api/theme", json={**loaded, "palette": "forest"}).status_code == 200
    body = client.get("/api/theme").json()
    assert (body["surface"], body["accent"], body["colour_amount"]) == ("slate", "rose", "full")


def test_startup_converts_an_old_palette_and_keeps_the_look(tmp_path, monkeypatch, templates_dir):
    """The cutover's theme migration runs at startup (spec §11.3): the file
    loses `palette`, names the look it stood for, and keeps a copy."""
    from support.api import _client

    theme = templates_dir / "theme.yaml"
    theme.write_text("palette: forest\nmode: light\n")
    with _client(tmp_path, monkeypatch, templates_dir=templates_dir, default_setup=False) as client:
        body = client.get("/api/theme").json()
    assert (body["surface"], body["accent"], body["colour_amount"], body["mode"]) == (
        "moss",
        "green",
        "full",
        "light",
    )
    assert body["derived"] is False
    assert "palette" not in yaml.safe_load(theme.read_text())
    assert (templates_dir / "theme.yaml.pre-2.0").read_text() == "palette: forest\nmode: light\n"


def test_put_theme_echo_of_a_derived_get_stays_derived(client, templates_dir):
    loaded = client.get("/api/theme").json()
    assert loaded["derived"] is True
    client.put("/api/theme", json={**loaded, "palette": "forest"})
    assert "surface" not in yaml.safe_load((templates_dir / "theme.yaml").read_text())
    body = client.get("/api/theme").json()
    assert (body["surface"], body["accent"], body["derived"]) == ("moss", "green", True)


def test_put_theme_refuses_an_accent_at_mono(client, templates_dir):
    resp = client.put("/api/theme", json={"colour_amount": "mono", "accent": "blue"})
    assert resp.status_code == 422
    assert "mono has no accent" in resp.json()["detail"]
    assert not (templates_dir / "theme.yaml").exists()


def test_changing_theme_does_not_report_health_as_degraded(client):
    # theme.yaml has no 'id' key -- load_templates would read it as a broken
    # chain template unless it's in CONFIG_FILES (Kraft-w1ps).
    client.put("/api/theme", json={"palette": "forest", "mode": "light"})
    health = client.get("/api/health").json()
    assert health["status"] == "ok"
    assert health["invalid_templates"] == {}


# ── access control (design 5e): bind, password, the bind used at startup ──


def test_localhost_needs_no_password_and_says_so(client):
    body = client.get("/api/access").json()
    assert body["bind"] == "127.0.0.1"
    assert body["auth_required"] is False and body["password_set"] is False
    assert client.get("/api/work-items").status_code == 200


def test_access_suggests_this_machines_lan_names_for_allowed_hosts(client, monkeypatch):
    from kraft.api.routes import settings as settings_routes

    monkeypatch.setattr(settings_routes, "_outbound_address", lambda: "192.0.2.7")
    monkeypatch.setattr(settings_routes.socket, "gethostname", lambda: "MyBox.")
    # A bare host name is offered as its mDNS form, which a phone can resolve.
    assert client.get("/api/access").json()["lan_hosts"] == ["192.0.2.7", "mybox.local"]
    monkeypatch.setattr(settings_routes.socket, "gethostname", lambda: "mybox.example.com")
    assert client.get("/api/access").json()["lan_hosts"] == ["192.0.2.7", "mybox.example.com"]
    # Off the network, or named localhost: nothing a phone could use is offered.
    monkeypatch.setattr(settings_routes, "_outbound_address", lambda: "127.0.0.1")
    monkeypatch.setattr(settings_routes.socket, "gethostname", lambda: "localhost")
    assert client.get("/api/access").json()["lan_hosts"] == []


def test_binding_off_localhost_without_a_password_is_refused(client):
    r = client.put("/api/access", json={"bind": "0.0.0.0"})
    assert r.status_code == 422
    assert "password" in r.json()["detail"]
    assert client.get("/api/access").json()["bind"] == "127.0.0.1"


@pytest.mark.api_client(host="0.0.0.0")
def test_a_lan_bind_locks_the_api_until_a_login(client):
    enabled = client.put("/api/access", json={"bind": "0.0.0.0", "password": "hunter2"})
    assert enabled.status_code == 200
    assert enabled.json()["auth_required"] is True

    client.cookies.clear()
    # /access is itself behind the wall it just raised
    assert client.get("/api/access").status_code == 401
    assert client.get("/api/work-items").status_code == 401
    # health stays reachable so a monitor does not need a session
    assert client.get("/api/health").status_code == 200

    assert client.post("/api/login", json={"password": "wrong"}).status_code == 401
    assert client.post("/api/login", json={"password": "hunter2"}).status_code == 200
    assert client.get("/api/work-items").status_code == 200

    sessions = client.get("/api/sessions").json()["sessions"]
    assert len(sessions) == 1 and sessions[0]["current"] is True
    # the cookie's own value is never stored, only its hash
    cookie = client.cookies["kraft_session"]
    assert sessions[0]["id"] != cookie

    assert client.delete("/api/sessions/nope").status_code == 404
    # revoking your own session logs you straight back out
    assert client.delete(f"/api/sessions/{sessions[0]['id']}").status_code == 204
    assert client.get("/api/work-items").status_code == 401


@pytest.mark.api_client(host="0.0.0.0")
def test_changing_the_password_revokes_every_session(client):
    client.put("/api/access", json={"bind": "0.0.0.0", "password": "first"})
    client.post("/api/login", json={"password": "first"})
    assert client.get("/api/work-items").status_code == 200

    client.put("/api/access", json={"password": "second"})
    assert client.get("/api/work-items").status_code == 401
    assert client.post("/api/login", json={"password": "first"}).status_code == 401
    assert client.post("/api/login", json={"password": "second"}).status_code == 200


def test_the_password_is_stored_only_as_a_scrypt_hash(client, templates_dir):
    client.put("/api/access", json={"password": "hunter2"})
    raw = (templates_dir / "access.yaml").read_text()
    assert "hunter2" not in raw
    assert yaml.safe_load(raw)["password_hash"].startswith("scrypt$")


def test_the_configured_bind_is_what_the_server_starts_on(tmp_path, monkeypatch, templates_dir):
    from kraft.cli.admin import _bind

    monkeypatch.delenv("KRAFT_PORT", raising=False)
    monkeypatch.delenv("KRAFT_HOST", raising=False)
    config.Access(bind="0.0.0.0", port=9100, password_hash="scrypt$aa$bb").save(
        templates_dir / "access.yaml"
    )
    assert _bind(templates_dir) == ("0.0.0.0", 9100)
    monkeypatch.setenv("KRAFT_PORT", "1234")
    assert _bind(templates_dir) == ("0.0.0.0", 1234)


def test_an_unprotected_lan_bind_refuses_to_start(tmp_path, monkeypatch, templates_dir):
    """The dangerous configuration is a non-loopback bind with no password. The
    API keeps working without one so the first password *can* be set; the process
    refuses to come up on the wire in that state."""
    from kraft.cli.admin import _bind

    monkeypatch.delenv("KRAFT_PORT", raising=False)
    monkeypatch.delenv("KRAFT_HOST", raising=False)
    config.Access(bind="0.0.0.0", port=8765).save(templates_dir / "access.yaml")
    with pytest.raises(SystemExit, match="no password is set") as refused:
        _bind(templates_dir)
    # It names the command that starts Kraft on loopback, then the two ways to
    # set a password there: the Access screen, and the API.
    why = str(refused.value)
    assert "`kraft admin start --host 127.0.0.1`, then set one in Settings › Access" in why
    assert "curl -X PUT http://127.0.0.1:8765/api/access" in why


# ── intake: defaults, persistence, poller validation ──


def test_get_intake_returns_the_defaults_when_no_file_was_written(client):
    body = client.get("/api/intake").json()
    assert body["enabled"] is False
    assert body["interval_s"] == config.INTAKE_DEFAULT["interval_s"]


def test_intake_checks_are_newest_first_and_limited(client):
    conn = sqlite3.connect(RunDirs(Path(os.environ["KRAFT_RUN_DIR"])).db)
    for n in range(3):
        store.record_intake_check(
            conn, ready=n, started=[f"w{n}"], skipped=[{"bead_id": "B", "reason": "epic"}]
        )
    conn.commit()
    conn.close()
    rows = client.get("/api/intake/checks").json()
    assert [r["ready"] for r in rows] == [2, 1, 0]
    assert set(rows[0]) == {"id", "at", "ready", "started", "skipped"}
    assert rows[0]["started"] == ["w2"]
    assert [r["ready"] for r in client.get("/api/intake/checks?limit=2").json()] == [2, 1]
    assert client.get("/api/intake/checks?limit=101").status_code == 422
    assert client.get("/api/intake/checks?limit=0").status_code == 422


def test_put_intake_persists_and_applies_without_a_restart(client, templates_dir):
    """The poller task is replaced, not just the dict it reads: `interval_s` is
    read once at task start, so a live poller would keep the old interval."""
    app = client.app
    assert app.state.intake_task is None

    saved = client.put(
        "/api/intake",
        json={
            "enabled": True,
            "interval_s": 60,
            "priority_ceiling": 3,
            "repos": ["/repo-a"],
        },
    )
    assert saved.status_code == 200
    assert app.state.intake["interval_s"] == 60
    assert app.state.intake_task is not None
    assert "max_concurrent" not in yaml.safe_load((templates_dir / "intake.yaml").read_text())
    assert client.get("/api/intake").json()["repos"] == ["/repo-a"]

    # and turning it back off stops the poller rather than leaving a live timer
    client.put(
        "/api/intake",
        json={
            "enabled": False,
            "interval_s": 60,
            "priority_ceiling": 3,
            "repos": [],
        },
    )
    assert app.state.intake_task is None


@pytest.mark.parametrize(
    "over",
    [
        {"interval_s": 5},  # below the floor the poller would clamp to anyway
        {"priority_ceiling": 5},
        {"priority_ceiling": -1},
    ],
    ids=["interval-below-the-floor", "ceiling-above-p4", "negative-ceiling"],
)
def test_put_intake_rejects_a_setting_the_poller_would_not_honour(client, over):
    body = {
        "enabled": True,
        "interval_s": 60,
        "priority_ceiling": 2,
        "repos": [],
        **over,
    }
    assert client.put("/api/intake", json=body).status_code == 422


def test_put_intake_no_longer_requires_max_concurrent(client):
    body = client.put(
        "/api/intake",
        json={"enabled": False, "interval_s": 300, "priority_ceiling": 2, "repos": []},
    )
    assert body.status_code == 200, body.text


def test_get_intake_carries_repo_pickup_stats(client, templates_dir, tmp_path, bd, monkeypatch):
    """Per connected repo, how many beads `bd ready` would hand intake, and
    None for a repo bd cannot answer for rather than a 500 on the page."""
    from kraft.adapters import beads

    ready = connect_repo(bd.init(make_repo(tmp_path, "ready")), templates_dir)
    broken = connect_repo(make_repo(tmp_path, "broken"), templates_dir)
    for title in ("one", "two"):
        client.portal.call(functools.partial(beads.intake, title, cwd=str(ready)))
    real_ready = beads.ready

    async def ready_or_fail(*, cwd=None):
        if Path(cwd) == broken.resolve():
            raise RuntimeError("bd ready failed (exit 1)")
        return await real_ready(cwd=cwd)

    monkeypatch.setattr(beads, "ready", ready_or_fail)

    body = client.get("/api/intake").json()

    assert body["repo_pickups"] == {
        str(ready.resolve()): {"items": 2, "last_picked_up": None},
        str(broken.resolve()): {"items": None, "last_picked_up": None},
    }
    assert body["recent_pickups"] == []


# ── policy (design 5d): loops, findings, budget, and the keys the form does not name ──


def test_policy_put_rejects_a_cap_that_would_not_load(client, templates_dir):
    good = {
        "loops": {"verify_fix_loop": {"attempts": 5, "wall_clock_s": 60}},
        "default": {"attempts": 3, "wall_clock_s": 3600},
    }
    seed = yaml.safe_load((templates_dir / "policy.yaml").read_text())
    assert client.put("/api/policy", json=good).status_code == 200
    assert client.get("/api/policy").json()["loops"]["verify_fix_loop"]["attempts"] == 5
    # Merged over the file, not replacing it: the seed's other keys stay (Kraft-xh2x8).
    assert yaml.safe_load((templates_dir / "policy.yaml").read_text()) == {
        **seed,
        **good,
        "max_concurrent": 3,
    }

    bad = {"loops": {}, "default": {"attempts": 0, "wall_clock_s": 1}}
    assert client.put("/api/policy", json=bad).status_code == 422
    assert client.get("/api/policy").json()["default"]["attempts"] == 3


def test_get_policy_answers_the_loaded_concurrency_and_the_active_count(client, templates_dir):
    body = client.get("/api/policy").json()
    assert (body["max_concurrent"], body["active_count"]) == (3, 0)

    # A hand edit not yet reloaded does not change what the slots count against.
    path = templates_dir / "policy.yaml"
    path.write_text(path.read_text() + "\nmax_concurrent: 7\n")
    assert client.get("/api/policy").json()["max_concurrent"] == 3


_POLICY = {"loops": {}, "default": {"attempts": 3, "wall_clock_s": 3600}}


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("budget", {"work_item_usd": 20.0, "daily_usd": None}),
        ("rate_limit_retries", 8),
        ("archive", {"after_days": 14}),
        ("max_concurrent", 5),
    ],
    ids=["budget", "rate-limit-retries", "archive", "max-concurrent"],
)
def test_put_policy_persists_a_block(client, templates_dir, key, value):
    saved = client.put("/api/policy", json={**_POLICY, key: value})
    assert saved.status_code == 200, saved.text
    assert saved.json()[key] == value
    assert yaml.safe_load((templates_dir / "policy.yaml").read_text())[key] == value
    assert client.get("/api/policy").json()[key] == value


@pytest.mark.parametrize(
    ("key", "value"),
    [("budget", {"work_item_usd": -5}), ("max_concurrent", 0)],
    ids=["a-negative-budget", "a-zero-max-concurrent"],
)
def test_put_policy_rejects_a_bad_value(client, key, value):
    assert client.put("/api/policy", json={**_POLICY, key: value}).status_code == 422


@pytest.mark.parametrize(
    "tail",
    [
        "findings:\n  loop_severities: [critical]\n",
        "rate_limit_retries: 8\n"
        "triggers:\n"
        "  - cron: '0 9 * * 1'\n"
        "    repo: /r\n"
        "    chain: default\n"
        "    title: weekly sweep\n",
        "budget:\n  work_item_usd: 20.0\n  daily_usd: null\n",
    ],
    ids=["findings", "rate-limit-retries-and-triggers", "budget"],
)
def test_saving_the_policy_preserves_a_block_it_does_not_edit(client, templates_dir, tail):
    """A save from the policy screen must not silently erase a block it does not edit."""
    head = "loops: {}\ndefault: { attempts: 3, wall_clock_s: 3600 }\n"
    (templates_dir / "policy.yaml").write_text(head + tail)
    kept = yaml.safe_load(tail)
    body = client.get("/api/policy").json()
    assert {key: body[key] for key in kept} == kept
    body["default"]["attempts"] = 5
    assert client.put("/api/policy", json=body).status_code == 200
    on_disk = yaml.safe_load((templates_dir / "policy.yaml").read_text())
    assert {key: on_disk[key] for key in kept} == kept
    assert on_disk["default"]["attempts"] == 5


def test_put_policy_without_a_budget_key_still_works(client):
    """Backward compatibility: an older UI build PUTs no budget."""
    body = {"loops": {}, "default": {"attempts": 3, "wall_clock_s": 3600}}
    assert client.put("/api/policy", json=body).status_code == 200


def test_get_policy_reports_max_concurrent_default(client):
    assert client.get("/api/policy").json()["max_concurrent"] == 3


def test_a_get_then_put_round_trip_keeps_every_key_and_refreshes_the_ceiling(client, templates_dir):
    """Kraft-xh2x8: a save rewrites policy.yaml from what it was sent. A key the
    screen doesn't edit -- V1 `defaults:`/`maxima:`, `forge_cli_timeout_s`,
    `auto_review_attempts` -- must survive it, and the running daemon must
    hold the ceiling the file now says, not the one it read at startup."""
    written = {
        "loops": {},
        "default": {"attempts": 3, "wall_clock_s": 3600},
        "forge_cli_timeout_s": 300,
        "auto_review_attempts": 2,
        # The caps are nested per level (Ruling 211), and stay nested.
        "defaults": {"max_attempts": 2, "tasks": {"time_cap_minutes": 90}},
        "maxima": {"work_item": {"token_budget": 1000}, "allowed_tools": ["git"]},
    }
    (templates_dir / "policy.yaml").write_text(yaml.safe_dump(written))
    body = client.get("/api/policy").json()
    assert client.put("/api/policy", json=body).status_code == 200

    on_disk = yaml.safe_load((templates_dir / "policy.yaml").read_text())
    assert on_disk == {**written, "max_concurrent": 3}
    live = client.app.state.instance_policy
    assert (live.at_level("work_item").token_budget, live.allowed_tools, live.max_attempts) == (
        1000,
        ("git",),
        2,
    )
    assert live.at_level("tasks").time_cap_minutes == 90


def test_a_save_that_omits_a_key_keeps_it_on_disk(client, templates_dir):
    """An older SPA build sends only what it knows; the rest stays."""
    (templates_dir / "policy.yaml").write_text(
        "loops: {}\ndefault: { attempts: 3, wall_clock_s: 3600 }\n"
        "maxima: { work_item: { token_budget: 1000 } }\nforge_cli_timeout_s: 300\n"
    )
    body = {"loops": {}, "default": {"attempts": 5, "wall_clock_s": 3600}}
    assert client.put("/api/policy", json=body).status_code == 200
    on_disk = yaml.safe_load((templates_dir / "policy.yaml").read_text())
    assert on_disk["maxima"] == {"work_item": {"token_budget": 1000}}
    assert on_disk["forge_cli_timeout_s"] == 300
    assert on_disk["default"]["attempts"] == 5


def test_a_save_that_edits_a_key_the_form_does_not_name_applies_it(client, templates_dir):
    """The other half of Kraft-xh2x8: a key outside `PolicyBody`'s fields is
    not ignored either -- a save that changes `maxima:` must not answer 200
    and leave the old ceiling on disk."""
    body = client.get("/api/policy").json()
    body["maxima"] = {"work_item": {"token_budget": 2000}}
    assert client.put("/api/policy", json=body).status_code == 200
    on_disk = yaml.safe_load((templates_dir / "policy.yaml").read_text())
    assert on_disk["maxima"] == {"work_item": {"token_budget": 2000}}
    assert client.app.state.instance_policy.maxima.work_item.token_budget == 2000


def test_a_save_with_a_flat_cap_is_refused_naming_its_level_and_writes_nothing(
    client, templates_dir
):
    """Ruling 211: a cap default or maximum is per level. The flat form never
    shipped in a release, so a save naming one is refused, pointing at the
    nested form, rather than written for the next load to refuse."""
    before = (templates_dir / "policy.yaml").read_text()
    body = client.get("/api/policy").json()
    body["defaults"] = {"time_cap_minutes": 30}

    refused = client.put("/api/policy", json=body)

    assert refused.status_code == 422
    assert "use defaults.tasks.time_cap_minutes" in refused.json()["detail"]
    assert (templates_dir / "policy.yaml").read_text() == before
