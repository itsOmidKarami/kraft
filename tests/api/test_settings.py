"""The settings screens' documents over HTTP: theme and access control
(design 5e), intake, and policy (design 5d)."""

from __future__ import annotations

import pytest
import yaml

from kraft import config

#: No default repo entry for an unconnected repo (`support.api._client`): these read real config.
pytestmark = pytest.mark.api_client(default_setup=False)


# ── theme ──


def test_get_theme_defaults_to_nocturne_dark(client):
    body = client.get("/api/theme").json()
    assert body["palette"] == "nocturne"
    assert body["mode"] == "dark"
    assert body["density"] == "compact"
    assert body["board"] == {"group_by": "status", "show_done": 5, "open_in": "peek"}


def test_put_theme_round_trips_through_the_yaml(client, templates_dir):
    body = {
        "palette": "forest",
        "mode": "light",
        "density": "comfortable",
        "board": {"group_by": "repo", "show_done": 10, "open_in": "full"},
    }
    assert client.put("/api/theme", json=body).status_code == 200
    assert client.get("/api/theme").json() == body
    assert yaml.safe_load((templates_dir / "theme.yaml").read_text()) == body


def test_put_theme_defaults_density_and_board_when_omitted(client):
    resp = client.put("/api/theme", json={"palette": "nocturne", "mode": "dark"})
    assert resp.json()["density"] == "compact"
    assert resp.json()["board"] == {"group_by": "status", "show_done": 5, "open_in": "peek"}


def test_get_theme_fills_defaults_for_a_pre_existing_file(client, templates_dir):
    # An operator's theme.yaml from before this change — no density/board keys.
    (templates_dir / "theme.yaml").write_text("palette: rose\nmode: light\n")
    body = client.get("/api/theme").json()
    assert body == {
        "palette": "rose",
        "mode": "light",
        "density": "compact",
        "board": {"group_by": "status", "show_done": 5, "open_in": "peek"},
    }


@pytest.mark.parametrize(
    "body",
    [
        {"palette": "cerulean", "mode": "dark"},
        {"palette": "nocturne", "mode": "twilight"},
        {
            "palette": "forest",
            "mode": "dark",
            "board": {"group_by": "priority", "show_done": 5, "open_in": "peek"},
        },
    ],
    ids=["an-unknown-palette", "an-unknown-mode", "an-unknown-group-by"],
)
def test_put_theme_rejects_an_unknown_value(client, body):
    assert client.put("/api/theme", json=body).status_code == 422
    assert client.get("/api/theme").json()["palette"] == "nocturne"


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
    config.save_access(
        templates_dir / "access.yaml",
        {"bind": "0.0.0.0", "port": 9100, "password_hash": "scrypt$aa$bb"},
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
    config.save_access(templates_dir / "access.yaml", {"bind": "0.0.0.0", "port": 8765})
    with pytest.raises(SystemExit, match="no password is set"):
        _bind(templates_dir)


# ── intake: defaults, persistence, poller validation ──


def test_get_intake_returns_the_defaults_when_no_file_was_written(client):
    body = client.get("/api/intake").json()
    assert body["enabled"] is False
    assert body["interval_s"] == config.INTAKE_DEFAULT["interval_s"]


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
            "max_concurrent": 2,
            "priority_ceiling": 3,
            "repos": ["/repo-a"],
        },
    )
    assert saved.status_code == 200
    assert app.state.intake["interval_s"] == 60
    assert app.state.intake_task is not None
    assert yaml.safe_load((templates_dir / "intake.yaml").read_text())["max_concurrent"] == 2
    assert client.get("/api/intake").json()["repos"] == ["/repo-a"]

    # and turning it back off stops the poller rather than leaving a live timer
    client.put(
        "/api/intake",
        json={
            "enabled": False,
            "interval_s": 60,
            "max_concurrent": 2,
            "priority_ceiling": 3,
            "repos": [],
        },
    )
    assert app.state.intake_task is None


@pytest.mark.parametrize(
    "over",
    [
        {"interval_s": 5},  # below the floor the poller would clamp to anyway
        {"max_concurrent": 0},
        {"priority_ceiling": 5},
        {"priority_ceiling": -1},
    ],
)
def test_put_intake_rejects_a_setting_the_poller_would_not_honour(client, over):
    body = {
        "enabled": True,
        "interval_s": 60,
        "max_concurrent": 1,
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


def test_get_intake_carries_repo_pickup_stats(client, templates_dir):
    body = client.get("/api/intake").json()
    assert "repo_pickups" in body
    assert "recent_pickups" in body
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
        "defaults": {"max_attempts": 2},
        "maxima": {"token_budget": 1000, "allowed_tools": ["git"]},
    }
    (templates_dir / "policy.yaml").write_text(yaml.safe_dump(written))
    body = client.get("/api/policy").json()
    assert client.put("/api/policy", json=body).status_code == 200

    on_disk = yaml.safe_load((templates_dir / "policy.yaml").read_text())
    assert on_disk == {**written, "max_concurrent": 3}
    live = client.app.state.instance_policy
    assert (live.token_budget, live.allowed_tools, live.max_attempts) == (1000, ("git",), 2)


def test_a_save_that_omits_a_key_keeps_it_on_disk(client, templates_dir):
    """An older SPA build sends only what it knows; the rest stays."""
    (templates_dir / "policy.yaml").write_text(
        "loops: {}\ndefault: { attempts: 3, wall_clock_s: 3600 }\n"
        "maxima: { token_budget: 1000 }\nforge_cli_timeout_s: 300\n"
    )
    body = {"loops": {}, "default": {"attempts": 5, "wall_clock_s": 3600}}
    assert client.put("/api/policy", json=body).status_code == 200
    on_disk = yaml.safe_load((templates_dir / "policy.yaml").read_text())
    assert on_disk["maxima"] == {"token_budget": 1000}
    assert on_disk["forge_cli_timeout_s"] == 300
    assert on_disk["default"]["attempts"] == 5


def test_a_save_that_edits_a_key_the_form_does_not_name_applies_it(client, templates_dir):
    """The other half of Kraft-xh2x8: a key outside `PolicyBody`'s fields is
    not ignored either -- a save that changes `maxima:` must not answer 200
    and leave the old ceiling on disk."""
    body = client.get("/api/policy").json()
    body["maxima"] = {"token_budget": 2000}
    assert client.put("/api/policy", json=body).status_code == 200
    on_disk = yaml.safe_load((templates_dir / "policy.yaml").read_text())
    assert on_disk["maxima"] == {"token_budget": 2000}
    assert client.app.state.instance_policy.token_budget == 2000
