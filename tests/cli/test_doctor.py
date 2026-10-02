"""`kraft doctor` — one line per check, exit 1 on any failure."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import yaml
from support.harness import connected_repo, make_repo

from kraft import auth, capabilities, cli, client, doctor, harness

# `app` fixture: tests/conftest.py. It wires client.transport.http() to the ASGI app.


def _names(rows):
    return [row["name"] for row in rows]


def _by_name(rows, name):
    return next(row for row in rows if row["name"] == name)


def _prime(tmp_path):
    """One call through the app, so its lifespan has created the run dir and the
    MCP token the way a real first start does."""
    asyncio.run(client.health())
    return tmp_path / "run" / auth.MCP_TOKEN_FILE


def test_doctor_on_a_live_instance_reaches_every_check(app, tmp_path):
    _prime(tmp_path)
    rows = asyncio.run(doctor.run_checks())
    assert _by_name(rows, "server")["ok"]
    for name in (
        "health",
        "templates",
        "access.yaml",
        "detectors.yaml",
        "pidfile",
        "mcp token",
        "agent: claude",
        "mcp server",
        "shell completion",
        "bd",
        "worktrees",
    ):
        assert name in _names(rows)


@pytest.mark.parametrize(
    ("text", "ok", "said"),
    [
        (None, True, "not present"),
        ("detectors:\n  - {id: earthly, tier: runner, files: [Earthfile]}\n", True, "1 detector"),
        ("detectorz: []\n", False, "detectors.yaml"),
    ],
    ids=["absent", "valid", "broken"],
)
def test_doctor_reads_the_operators_detectors_file(app, tmp_path, text, ok, said):
    _prime(tmp_path)
    templates = Path(os.environ["KRAFT_TEMPLATES_DIR"])
    if text is not None:
        (templates / "detectors.yaml").write_text(text)
    row = _by_name(asyncio.run(doctor.run_checks()), "detectors.yaml")
    assert (row["ok"], said in row["detail"]) == (ok, True), row


@pytest.mark.parametrize(
    ("bind", "fails"), [("0.0.0.0", True), ("127.0.0.1", False)], ids=["network", "loopback"]
)
def test_doctor_names_an_allowed_host_that_never_matches(templates_dir, monkeypatch, bind, fails):
    """It fails a network bind, where a device using that name is refused.
    A loopback bind never reads the list, so a wildcard 1.4 accepted only
    warns there: `doctor && ...` must not go red on an upgrade."""
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(templates_dir))
    monkeypatch.delenv("KRAFT_HOST", raising=False)
    (templates_dir / "access.yaml").write_text(
        f"bind: {bind}\nallowed_hosts: [kraft.local, '*.ts.net']\n"
    )
    row = _by_name(doctor._config_checks(), "access.yaml")
    assert row["ok"] is not fails
    assert row["warn"] is not fails
    assert "'*.ts.net' is not a host name or IP address" in row["detail"], row


@pytest.mark.parametrize(
    ("access", "warns"),
    [
        ("bind: 0.0.0.0\n", True),
        ("bind: 0.0.0.0\nallowed_hosts: [kraft.local]\n", False),
        ("bind: 127.0.0.1\n", False),
    ],
    ids=["network-bind-no-hosts", "network-bind-with-hosts", "loopback-bind"],
)
def test_doctor_warns_of_a_network_bind_with_no_allowed_hosts(
    templates_dir, monkeypatch, access, warns
):
    """Off loopback with an empty list, every browser on another device gets
    a 403: say so before a 1.4 user, whose plain-http browser was never
    checked, upgrades into it."""
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(templates_dir))
    monkeypatch.delenv("KRAFT_HOST", raising=False)
    (templates_dir / "access.yaml").write_text(access)
    row = _by_name(doctor._config_checks(), "access.yaml")
    assert row["ok"]
    assert row["warn"] is warns
    assert ("no allowed_hosts" in row["detail"]) is warns, row


def test_doctor_flags_a_dead_pidfile(app, tmp_path):
    _prime(tmp_path)
    pid_path = tmp_path / "run" / "kraft.pid"
    pid_path.write_text("999999")
    row = _by_name(asyncio.run(doctor.run_checks()), "pidfile")
    assert not row["ok"]
    assert "not running" in row["detail"]


@pytest.mark.parametrize("file", [auth.MCP_TOKEN_FILE, auth.TRIGGER_TOKEN_FILE])
def test_a_world_readable_token_fails(app, tmp_path, file):
    (_prime(tmp_path).parent / file).chmod(0o644)
    row = _by_name(asyncio.run(doctor.run_checks()), file.replace("-", " "))
    assert not row["ok"] and "0644" in row["detail"], row


def test_doctor_reports_a_missing_bd_without_failing(app, tmp_path, monkeypatch):
    """Kraft-7gy: bd is optional. A deliberately bd-less install is not broken,
    so the line reports and does not set doctor's exit code."""
    _prime(tmp_path)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    rows = asyncio.run(doctor.run_checks())
    row = _by_name(rows, "bd")
    assert row["ok"]
    assert "not installed" in row["detail"]


def test_a_dead_server_is_one_failure_and_the_rest_are_skipped(monkeypatch, tmp_path):
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(tmp_path / "templates"))

    async def dead():
        raise ValueError("no Kraft server at http://127.0.0.1:1 — start one with `kraft serve`")

    monkeypatch.setattr(client, "health", dead)
    rows = asyncio.run(doctor.run_checks())
    failed = {row["name"] for row in rows if not row["ok"]}
    # the local checks still run and still fail on their own merits — but
    # nothing downstream of the server does, or one dead server reads as five
    # problems. (`agent cli` is deliberately not asserted either way: whether
    # `claude` is installed is a fact about the machine, not about doctor.)
    assert "server" in failed
    assert {"templates", "mcp token"} <= failed
    assert all(_by_name(rows, name)["skipped"] for name in ("health", "repos", "worktrees"))
    assert not failed & {"health", "repos", "worktrees"}


def test_an_orphaned_worktree_is_reported(app, tmp_path):
    _prime(tmp_path)
    (tmp_path / "run" / "worktrees" / "wi-ghost").mkdir(parents=True)
    row = _by_name(asyncio.run(doctor.run_checks()), "worktrees")
    assert not row["ok"]
    assert "wi-ghost" in row["detail"]


def test_a_cancelled_items_kept_worktree_is_not_an_orphan(app, tmp_path):
    """Cancel keeps the worktree until the item is archived, and the board's
    list leaves a cancelled item off: the check counts every row, not just
    the board's, or doctor fails for weeks after any cancel."""
    _prime(tmp_path)
    repo = connected_repo(tmp_path)
    wid = asyncio.run(client.create_work_item("t", repo=str(repo)))["id"]
    asyncio.run(client.cancel("not now", wid))
    (tmp_path / "run" / "worktrees" / wid).mkdir(parents=True)

    row = _by_name(asyncio.run(doctor.run_checks()), "worktrees")

    assert row["ok"], row["detail"]


def test_degraded_health_is_spelled_out_one_reason_per_line(app, monkeypatch):
    async def degraded():
        return {
            "status": "degraded",
            "invalid_templates": {"quick-task.yaml": "bad node"},
            "invalid_policy": "budget: not a number",
            "index": {"errors": ["scan failed"], "embeddings": {"available": True}},
            "reattach_summary": {"unknown": []},
        }

    monkeypatch.setattr(client, "health", degraded)
    details = [row["detail"] for row in asyncio.run(doctor.run_checks()) if row["name"] == "health"]
    assert len(details) == 3
    assert any("quick-task.yaml" in d for d in details)


def test_a_missing_vector_extra_is_advice_not_a_failure():
    """Kraft-rj8cn: semantic search is an opt-in extra, so /health stays `ok`
    without it and doctor agrees -- an ok line carrying the advice, like shell
    completion's, not a FAIL that pins the exit code at 1."""
    from kraft.index.embed import _MISSING

    rows = doctor._health_checks(
        {
            "status": "ok",
            "index": {"errors": [], "embeddings": {"available": False, "reason": _MISSING}},
            "reattach_summary": {"unknown": []},
        }
    )

    assert all(row["ok"] for row in rows)
    assert _by_name(rows, "health")["detail"] == "ok"
    assert "kraft-sdlc[vector]" in _by_name(rows, "embeddings")["detail"]


def test_an_installed_but_broken_embedder_fails():
    """Kraft-pm2rj: the extra is there and the operator asked for semantic
    search, but the model will not load: a FAIL, unlike a missing extra."""
    rows = doctor._health_checks(
        {
            "status": "ok",
            "index": {
                "errors": [],
                "embeddings": {"available": True, "model": "m", "reason": "download failed"},
            },
            "reattach_summary": {"unknown": []},
        }
    )

    row = _by_name(rows, "embeddings")
    assert row["ok"] is False
    assert "download failed" in row["detail"]


def test_doctor_exits_1_and_prints_the_failures(app, tmp_path, capsys):
    _prime(tmp_path)
    (tmp_path / "run" / "worktrees" / "wi-ghost").mkdir(parents=True)
    with pytest.raises(SystemExit) as caught:
        cli.main(["admin", "doctor"])
    assert caught.value.code == 1
    out = capsys.readouterr().out
    assert "wi-ghost" in out and "FAIL" in out


def test_doctor_json_is_the_check_list(app, tmp_path, capsys):
    import json

    _prime(tmp_path)
    # Whether a bare-primed instance has any failing check (e.g. "agent cli")
    # depends on what's on the host's PATH, so the exit code itself can't be
    # pinned to a literal -- but `_cmd_doctor`'s contract can: exit 1 iff a
    # row came back not-ok, exit cleanly otherwise. The old version discarded
    # the code and asserted nothing about it (Kraft-nja7).
    try:
        cli.main(["admin", "doctor", "--json"])
        code = 0
    except SystemExit as exc:
        code = exc.code
    rows = json.loads(capsys.readouterr().out)
    assert {"name", "ok", "detail", "skipped"} <= set(rows[0])
    assert code == (1 if any(not row["ok"] for row in rows) else 0)


def test_every_config_check_reports_even_with_no_templates_dir(tmp_path, monkeypatch):
    """CI has no `$KRAFT_HOME/templates`, and `_config_checks` returns early
    there. Every row it can emit must still emit, or a caller reading the run by
    name gets StopIteration instead of an answer — which is how this reached a
    red pipeline while passing on a developer machine that had the directory.
    """
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(tmp_path / "nope"))
    monkeypatch.setattr(doctor, "BUNDLED", tmp_path / "absent")

    rows = asyncio.run(doctor.run_checks())
    assert _by_name(rows, "templates")["ok"] is False
    chains = _by_name(rows, "chain_templates")
    assert chains["ok"] is True and chains["skipped"] is True
    assert _by_name(rows, "chains")["skipped"] is True
    # Not the upgrade guide: a home never seeded has nothing to upgrade.
    assert _by_name(rows, "capabilities")["detail"] == "skipped: no templates dir"


def test_doctor_on_a_never_started_home_says_so(tmp_path, monkeypatch):
    """A newcomer's first `doctor`: a line up front saying no server has run
    here, and the harnesses row pointing at `kraft` like the others, not a raw
    ENOENT."""
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(tmp_path / "templates"))
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setattr(doctor, "BUNDLED", tmp_path / "absent")

    rows = asyncio.run(doctor.run_checks())
    assert rows[0]["name"] == "home" and rows[0]["warn"]
    assert "until `kraft` has started once" in rows[0]["detail"]
    assert "start `kraft` once to seed it" in _by_name(rows, "harnesses.yaml")["detail"]


def _chains(root: Path) -> Path:
    d = root / "chains"
    d.mkdir(parents=True)
    return d


def test_chain_templates_check_names_a_node_missing_from_the_live_copy(tmp_path, monkeypatch):
    bundled = tmp_path / "bundled"
    (_chains(bundled / "templates") / "default.yaml").write_text(
        "id: default\nnodes:\n  - {id: spec, extends: spec}\n  - {id: review, extends: review}\n"
    )
    live = tmp_path / "templates"
    (_chains(live) / "default.yaml").write_text("id: default\nnodes:\n  - {id: spec}\n")
    monkeypatch.setattr(doctor, "BUNDLED", bundled)
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(live))

    check = _by_name(asyncio.run(doctor.run_checks()), "chain_templates")
    assert check["ok"] is True  # an operator's own chain, not a failure
    assert "review" in check["detail"]
    assert "default.yaml" in check["detail"]


def test_chain_templates_check_names_a_template_missing_entirely(tmp_path, monkeypatch):
    bundled = tmp_path / "bundled"
    (_chains(bundled / "templates") / "quick-task.yaml").write_text(
        "id: quick-task\nnodes:\n  - {id: implementation}\n"
    )
    live = tmp_path / "templates"
    _chains(live)  # live has no quick-task.yaml at all
    monkeypatch.setattr(doctor, "BUNDLED", bundled)
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(live))

    check = _by_name(asyncio.run(doctor.run_checks()), "chain_templates")
    assert check["ok"] is True
    assert "quick-task.yaml missing entirely" in check["detail"]


def test_chain_templates_check_reads_only_chains(tmp_path, monkeypatch):
    """A top-level file -- `library.yaml`, whose `nodes:` is a mapping, or a
    legacy chain left beside it -- is not a chain the V1 loader reads."""
    bundled = tmp_path / "bundled"
    (bundled / "templates").mkdir(parents=True)
    (bundled / "templates" / "legacy.yaml").write_text("id: legacy\nnodes:\n  - {id: x}\n")
    live = tmp_path / "templates"
    live.mkdir()
    monkeypatch.setattr(doctor, "BUNDLED", bundled)
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(live))

    check = _by_name(asyncio.run(doctor.run_checks()), "chain_templates")
    assert check["ok"] is True
    assert check["skipped"] is True  # no chains/ in the bundle, nothing to diff


def test_chain_templates_check_ignores_files_with_no_nodes_list(tmp_path, monkeypatch):
    bundled = tmp_path / "bundled"
    (_chains(bundled / "templates") / "notes.yaml").write_text("enabled: false\n")
    live = tmp_path / "templates"
    live.mkdir()
    monkeypatch.setattr(doctor, "BUNDLED", bundled)
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(live))

    check = _by_name(asyncio.run(doctor.run_checks()), "chain_templates")
    assert check["ok"] is True
    assert check["skipped"] is True  # no chain templates found at all, nothing to diff


def test_chain_templates_check_is_skipped_without_a_bundle(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor, "BUNDLED", tmp_path / "absent")
    check = _by_name(asyncio.run(doctor.run_checks()), "chain_templates")
    assert check["skipped"] is True


def test_bundle_check_fails_when_the_spa_is_missing(monkeypatch, tmp_path):
    """A wheel built without `just bundle` serves JSON and no UI. Nothing said so."""
    monkeypatch.setattr(doctor, "BUNDLED", tmp_path / "absent")
    row = doctor._bundle_check()
    assert not row["ok"]
    assert "just bundle" in row["detail"]


def test_bundle_check_passes_when_the_spa_is_there(monkeypatch, tmp_path):
    web = tmp_path / "_bundled" / "web"
    web.mkdir(parents=True)
    (web / "index.html").write_text("<html></html>")
    monkeypatch.setattr(doctor, "BUNDLED", tmp_path / "_bundled")
    assert doctor._bundle_check()["ok"]


def test_version_check_is_never_a_failure(monkeypatch):
    """A release day must not start failing `kraft admin doctor && deploy`."""
    from kraft import update

    monkeypatch.delenv("KRAFT_NO_UPDATE_CHECK", raising=False)
    monkeypatch.setattr(update, "latest", lambda **_: update.Release("v9.9.9", "u"))
    monkeypatch.setattr(update, "installed", lambda: "0.1.0")
    row = doctor._version_check()
    assert row["ok"]
    assert "9.9.9" in row["detail"] and "available" in row["detail"]


def test_version_check_says_so_when_current(monkeypatch):
    from kraft import update

    monkeypatch.delenv("KRAFT_NO_UPDATE_CHECK", raising=False)
    monkeypatch.setattr(update, "latest", lambda **_: update.Release("v0.4.0", "u"))
    monkeypatch.setattr(update, "installed", lambda: "0.4.0")
    assert "newest" in doctor._version_check()["detail"]


def test_version_check_with_no_network_warns_not_skips(monkeypatch):
    """A dead release feed is `ok` (must not fail `doctor && deploy`) but not a
    skip - it ran and could not confirm the answer, which is worth a human
    noticing rather than folding silently into 'all checks passed'."""
    from kraft import update

    monkeypatch.delenv("KRAFT_NO_UPDATE_CHECK", raising=False)
    monkeypatch.setattr(update, "latest", lambda **_: None)
    row = doctor._version_check()
    assert row["ok"] and row["warn"] and not row["skipped"]


def test_version_check_honours_the_no_check_env_var(monkeypatch):
    """One switch silences every version check, not only the one at boot.

    Without this, `run_checks()` reaches the network - which is a test suite that
    depends on gitlab.com being up, and an air-gapped operator paying the timeout
    every time they run doctor.
    """
    from kraft import update

    monkeypatch.setenv("KRAFT_NO_UPDATE_CHECK", "1")
    monkeypatch.setattr(update, "latest", lambda **_: pytest.fail("checked with the env var set"))
    row = doctor._version_check()
    assert row["ok"] and row["skipped"]


def test_completion_check_is_skipped_outside_zsh(monkeypatch):
    monkeypatch.setenv("SHELL", "/bin/bash")
    check = _by_name(asyncio.run(doctor.run_checks()), "shell completion")
    assert check["skipped"] is True


def test_completion_check_names_the_missing_eval_line(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELL", "/bin/zsh")
    monkeypatch.setattr(doctor.Path, "home", lambda: tmp_path)
    (tmp_path / ".zshrc").write_text("# nothing relevant here\n")
    check = _by_name(asyncio.run(doctor.run_checks()), "shell completion")
    assert check["ok"] is True
    assert "register-python-argcomplete kraft" in check["detail"]


def test_completion_check_passes_once_registered(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELL", "/bin/zsh")
    monkeypatch.setattr(doctor.Path, "home", lambda: tmp_path)
    (tmp_path / ".zshrc").write_text('eval "$(register-python-argcomplete kraft)"\n')
    check = _by_name(asyncio.run(doctor.run_checks()), "shell completion")
    assert check["ok"] is True
    assert "registered in" in check["detail"]


def test_mcp_check_passes_on_a_user_scope_registration(app, tmp_path):
    """What `kraft admin init` produces via `claude mcp add --scope user`
    (init.py:122): the throwaway `HOME` conftest's autouse
    `_isolated_kraft_home` already writes."""
    check = _by_name(asyncio.run(doctor.run_checks()), "mcp server")
    assert check["ok"] is True
    assert ".claude.json" in check["detail"]
    # Kraft-9efnk.43: a pass says whose registration it read, and whose not.
    assert "Claude Code's registration only; Codex, Cursor" in check["detail"]


def test_mcp_check_passes_on_a_committed_repo_scope_mcp_json(app, tmp_path):
    """`kraft admin init --repo`'s file, once committed: worktrees check out HEAD."""
    (Path.home() / ".claude.json").unlink()
    repo = make_repo(tmp_path)
    asyncio.run(client.ensure_repo(str(repo)))
    (repo / ".mcp.json").write_text(json.dumps({"mcpServers": {"kraft": {}}}))
    subprocess.run(["git", "add", ".mcp.json"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "mcp"], cwd=repo, check=True)
    assert ".mcp.json" in _by_name(asyncio.run(doctor.run_checks()), "mcp server")["detail"]


@pytest.mark.parametrize("plugins, ok", [({"kraft@kraft": True}, True), ({}, False)])
def test_mcp_check_passes_on_the_plugin_alone_and_fails_on_nothing(app, plugins, ok):
    """The plugin alone is the recommended install, no `kraft admin init`; with
    nothing registered a Claude launch is refused, a real failure."""
    (Path.home() / ".claude.json").unlink()
    (Path.home() / ".claude" / "plugins").mkdir(parents=True)
    (Path.home() / ".claude" / "settings.json").write_text(json.dumps({"enabledPlugins": plugins}))
    installs = {"version": 2, "plugins": {key: [{"scope": "user"}] for key in plugins}}
    (Path.home() / ".claude/plugins/installed_plugins.json").write_text(json.dumps(installs))
    check = _by_name(asyncio.run(doctor.run_checks()), "mcp server")
    assert check["ok"] is ok
    assert ("mcp__plugin_kraft_kraft__" if ok else "kraft admin init") in check["detail"]
    assert "Claude Code's registration only" in check["detail"]


def test_path_check_fails_when_another_kraft_shadows_this_one(monkeypatch):
    """Kraft-xs3ri: every MCP registration runs `kraft` by name, so an older
    install ahead on PATH answers the tools whatever `admin update` installed."""
    from kraft import update

    monkeypatch.setattr(update, "shadowing_kraft", lambda: "/opt/homebrew/bin/kraft")
    row = doctor._path_check()
    assert row["ok"] is False
    assert "/opt/homebrew/bin/kraft" in row["detail"]


def test_path_check_fails_when_no_kraft_is_on_path(monkeypatch, tmp_path):
    """A fresh `uv tool install` run by its full path: the plugin's
    `.mcp.json` and hooks launch `kraft` by name, so they start nothing."""
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    row = doctor._path_check()
    assert row["ok"] is False and "not on PATH" in row["detail"]
    assert "uv tool update-shell" in row["detail"] and "install-service" in row["detail"]


# ── _agent_checks: per selected harness profile, not a hardcoded claude ─────


def _live(tmp_path, monkeypatch, profiles: dict[str, str], selected: list[str]) -> Path:
    """A live templates dir whose one chain runs one agent task per profile in
    `selected`, and whose `harnesses.yaml` declares `profiles` (id -> provider)."""
    live = tmp_path / "templates"
    tasks = {
        f"t{i}": {"kind": "agent", "harness": p, "prompt": "p"} for i, p in enumerate(selected)
    }
    (live / "chains").mkdir(parents=True)
    (live / "library.yaml").write_text(yaml.safe_dump({"tasks": tasks}))
    nodes = [
        {"id": f"n{i}", "kind": "exec", "tasks": [{"id": "t", "extends": t}]}
        for i, t in enumerate(tasks)
    ]
    (live / "chains" / "c.yaml").write_text(yaml.safe_dump({"id": "c", "nodes": nodes}))
    harnesses = {pid: {"provider": provider} for pid, provider in profiles.items()}
    (live / "harnesses.yaml").write_text(yaml.safe_dump({"harnesses": harnesses}))
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(live))
    return live


def test_doctor_checks_every_selected_profile(tmp_path, monkeypatch):
    """doctor.py used to hardcode `claude` and which() it once. A chain with a
    codex task must be told about codex, not reassured about claude."""
    _live(tmp_path, monkeypatch, {"a": "claude", "b": "codex"}, ["a", "b"])
    rows = doctor._agent_checks()
    # b is codex on its default model, which prices.json cannot price.
    assert {r["name"] for r in rows} == {"agent: a", "agent: b", "cost: b"}


def test_doctor_does_not_check_a_profile_nothing_selects(tmp_path, monkeypatch):
    """A declared profile no chain selects is not degraded by its executable
    being absent from PATH."""
    _live(tmp_path, monkeypatch, {"a": "claude", "idle": "gemini"}, ["a"])
    rows = doctor._agent_checks()
    assert not any(r["name"] == "agent: idle" for r in rows)


def test_a_profile_s_own_executable_is_what_is_checked(tmp_path, monkeypatch):
    """A profile that names its `executable:` launches that, not the
    provider's default command, so that is what has to be on PATH."""
    live = _live(tmp_path, monkeypatch, {"a": "claude"}, ["a"])
    (live / "harnesses.yaml").write_text(
        yaml.safe_dump({"harnesses": {"a": {"provider": "claude", "executable": "my-claude"}}})
    )
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    row = _by_name(doctor._agent_checks(), "agent: a")
    assert not row["ok"]
    assert "my-claude" in row["detail"]


@pytest.mark.parametrize(
    ("chains", "says"),
    [
        ({"c": "a", "agy-impl": "gone"}, "chain agy-impl can't run"),
        ({"c": "a", "x": "gone", "y": "gone"}, "chains x, y can't run"),
        ({"x": "gone", "y": "gone"}, "no chain can run"),
        (
            {"c": "a", **{f"x{n}": "gone" for n in range(7)}},
            "chains x0, x1, x2, x3, x4 and 2 more can't run",
        ),
    ],
    ids=["one-of-two", "two-of-three", "all", "many"],
)
def test_a_missing_agent_names_the_chains_it_stops(tmp_path, monkeypatch, chains, says):
    """Only the chains that select a profile need its executable: saying "no
    chain can run" for one custom chain sent an operator after the rest."""
    live = _live(tmp_path, monkeypatch, {"a": "claude", "gone": "claude"}, ["a", "gone"])
    (live / "harnesses.yaml").write_text(
        yaml.safe_dump(
            {
                "harnesses": {
                    "a": {"provider": "claude"},
                    "gone": {"provider": "claude", "executable": "agy"},
                }
            }
        )
    )
    (live / "chains" / "c.yaml").unlink()
    task_of = {"a": "t0", "gone": "t1"}
    for cid, pid in chains.items():
        node = {"id": "n", "kind": "exec", "tasks": [{"id": "t", "extends": task_of[pid]}]}
        (live / "chains" / f"{cid}.yaml").write_text(yaml.safe_dump({"id": cid, "nodes": [node]}))
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "claude").write_text("#!/bin/sh\n")
    (fake / "claude").chmod(0o755)
    monkeypatch.setenv("PATH", str(fake))
    row = _by_name(doctor._agent_checks(), "agent: gone")
    assert row["ok"] is False
    assert row["detail"] == f"`agy` is not on PATH — {says}"


def test_a_selected_profile_harnesses_yaml_lacks_fails(tmp_path, monkeypatch):
    _live(tmp_path, monkeypatch, {}, ["ghost"])
    row = _by_name(doctor._agent_checks(), "agent: ghost")
    assert not row["ok"]
    assert "harnesses.yaml" in row["detail"]


def test_a_quarantined_harness_file_is_reported(tmp_path, monkeypatch):
    _live(tmp_path, monkeypatch, {"a": "claude"}, ["a"])
    hs = harness.HarnessSet(
        valid=harness.load(None).valid,
        invalid={"broken": "broken.yaml: unknown kind 'nope'"},
    )
    monkeypatch.setattr(doctor.harness, "load", lambda _dir: hs)
    row = _by_name(doctor._agent_checks(), "harness: broken")
    assert not row["ok"]
    assert "unknown kind" in row["detail"]


def test_the_dev_fake_still_passes_loudly(monkeypatch, tmp_path):
    """A fixtures symlink reads as OK *and says so*, because a dev instance
    looking like it works is spending no tokens on purpose."""
    _live(tmp_path, monkeypatch, {"a": "claude"}, ["a"])
    fixtures = tmp_path / "fixtures" / "bin"
    fixtures.mkdir(parents=True)
    real = tmp_path / "fixtures" / "fake-claude.sh"
    real.write_text("#!/bin/sh\n")
    real.chmod(0o755)
    (fixtures / "claude").symlink_to(real)
    monkeypatch.setenv("PATH", str(fixtures))
    row = _by_name(doctor._agent_checks(), "agent: a")
    assert row["ok"]
    assert "spends no tokens" in row["detail"]


#: `capabilities.MANIFEST` is empty since V1, so the row is checked against a stand-in.
_MANIFEST = (capabilities.Capability(version="1.0.0", name="thing", what="does it", how="add it"),)


def test_capabilities_row_names_what_the_install_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(capabilities, "MANIFEST", _MANIFEST)
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(tmp_path))
    (tmp_path / ".seeded-version").write_text("0.1.0\n")
    row = doctor._capabilities_check()
    assert row["ok"] is True, "not upgrading is a choice, not a failure"
    assert "0.1.0" in row["detail"]
    assert "thing" in row["detail"] and "add it" in row["detail"]


def test_capabilities_row_is_quiet_when_the_stamp_is_current(tmp_path, monkeypatch):
    monkeypatch.setattr(capabilities, "MANIFEST", _MANIFEST)
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(tmp_path))
    (tmp_path / ".seeded-version").write_text("1.0.0\n")
    row = doctor._capabilities_check()
    assert row["ok"] is True
    assert "up to date" in row["detail"]


def test_capabilities_row_leaves_out_one_the_live_templates_already_hold(tmp_path, monkeypatch):
    held = dataclasses.replace(_MANIFEST[0], present=("harnesses.yaml", "profiles"))
    monkeypatch.setattr(capabilities, "MANIFEST", (held,))
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(tmp_path))
    (tmp_path / "harnesses.yaml").write_text("profiles: {}\n")
    assert "up to date" in doctor._capabilities_check()["detail"]


def test_an_unstamped_home_is_told_everything_rather_than_erroring(tmp_path, monkeypatch):
    monkeypatch.setattr(capabilities, "MANIFEST", _MANIFEST)
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(tmp_path))
    row = doctor._capabilities_check()
    assert row["ok"] is True
    assert "thing" in row["detail"]


def test_a_chain_that_does_not_resolve_fails_doctor_by_name(tmp_path, monkeypatch):
    """Kraft-n1zp9: a chain whose `extends` names nothing parses, so the library
    loads, and `_resolved_chains` used to drop it without a word. The `chains`
    row is the same lint pass `admin templates lint` runs, and fails naming the
    chain and the resolver's message -- with or without a server."""
    live = _live(tmp_path, monkeypatch, {"a": "claude"}, ["a"])
    (live / "chains" / "broken.yaml").write_text(
        yaml.safe_dump({"id": "broken", "nodes": [{"id": "n", "extends": "no_such_node"}]})
    )
    row = _by_name(doctor._config_checks(), "chains")
    assert row["ok"] is False
    assert "broken" in row["detail"] and "no_such_node" in row["detail"]
    # The healthy chain beside it is not blamed.
    assert "c:" not in row["detail"]


def test_the_chains_row_passes_when_every_chain_resolves(tmp_path, monkeypatch):
    _live(tmp_path, monkeypatch, {"a": "claude"}, ["a"])
    row = _by_name(doctor._config_checks(), "chains")
    assert row["ok"] is True and "1 chain" in row["detail"]


@pytest.mark.parametrize(
    "script, ok, detail",
    [
        ('[ "$1" = version ] && echo 29.3.1; exit 0', True, "docker 29.3.1, image img"),
        ('[ "$1" = version ] && echo 29.3.1 && exit 0; exit 1', False, "docker pull img"),
        ("exit 1", False, "daemon is not reachable"),
    ],
    ids=["ready", "image-not-pulled", "no-daemon"],
)
def test_doctor_checks_a_sandboxed_repos_docker_and_image(
    app, tmp_path, monkeypatch, script, ok, detail
):
    """A sandboxed item stops for a human without a daemon, and its first
    launch pulls the image inside its own time cap: both belong in doctor."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "docker").write_text(f"#!/bin/sh\n{script}\n")
    (bin_dir / "docker").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    repo = make_repo(tmp_path)
    asyncio.run(client.ensure_repo(str(repo)))
    repos_yaml = tmp_path / "templates" / "repos.yaml"
    data = yaml.safe_load(repos_yaml.read_text())
    data["repos"][0]["sandbox"] = {"kind": "docker", "image": "img"}
    repos_yaml.write_text(yaml.safe_dump(data))

    rows = [r for r in asyncio.run(doctor.run_checks()) if r["name"].startswith("sandbox ")]

    assert [r["ok"] for r in rows] == [ok]
    assert detail in rows[0]["detail"]


@pytest.mark.parametrize(
    "proxy, warned", [("http://127.0.0.1:3128", True), ("http://proxy.corp:3128", False)]
)
def test_doctor_warns_that_a_loopback_proxy_cannot_reach_a_sandbox(
    app, tmp_path, monkeypatch, proxy, warned
):
    """The container's loopback is its own, so the proxy is not forwarded."""
    for name in ("HTTP_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HTTPS_PROXY", proxy)
    rows = _sandboxed_doctor_rows(app, tmp_path, "proxy ")
    assert [(r["ok"], r["warn"]) for r in rows] == ([(True, True)] if warned else [])
    assert not warned or ("`network:`" in rows[0]["detail"] and proxy in rows[0]["detail"])


@pytest.mark.parametrize("usable", [False, True], ids=["unusable", "usable"])
def test_doctor_warns_that_an_unusable_ssl_cert_file_is_ignored_by_sandboxes(
    app, tmp_path, monkeypatch, usable
):
    """It is skipped, not refused, so only doctor can say it went unused."""
    ca = tmp_path / "ca.pem"
    ca.write_text("-----BEGIN CERTIFICATE-----\nX\n-----END CERTIFICATE-----\n" if usable else "")
    monkeypatch.setenv("SSL_CERT_FILE", str(ca))
    rows = _sandboxed_doctor_rows(app, tmp_path, "ca ")
    assert [(r["ok"], r["warn"]) for r in rows] == ([] if usable else [(True, True)])
    assert usable or ("holds no PEM certificate" in rows[0]["detail"])


@pytest.mark.parametrize("network", [None, {"runtime": {"allow": ["a.io"]}}], ids=["open", "set"])
def test_doctor_warns_about_open_egress_on_a_sandboxed_repo(app, tmp_path, monkeypatch, network):
    """Spec §1: open stays the default, and doctor says so. Under `network:`
    Kraft's own proxy reaches a loopback one, so that warning goes too, and
    the TLS listener is checked: down here, between lifespans."""
    ready = AsyncMock(return_value=(True, "ready"))
    monkeypatch.setattr("kraft.worker.backends.docker.DockerBackend.health", ready)
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:3128")
    rows = _sandboxed_doctor_rows(app, tmp_path, ("egress ", "proxy "), network=network)
    warned = [(r["name"].split()[0], r["ok"], r["warn"]) for r in rows]
    listener, opened = [("egress", False, False)], [("egress", True, True), ("proxy", True, True)]
    assert warned == (listener if network else opened)
    assert ("did not answer" if network else "open egress") in rows[0]["detail"]


def _sandboxed_doctor_rows(app, tmp_path, prefix, **sandbox) -> list[dict]:
    """Doctor's rows starting with `prefix`, for one repository sandboxed
    in `img` (plus `sandbox`'s fields)."""
    repo = make_repo(tmp_path)
    asyncio.run(client.ensure_repo(str(repo)))
    repos_yaml = tmp_path / "templates" / "repos.yaml"
    data = yaml.safe_load(repos_yaml.read_text())
    data["repos"][0]["sandbox"] = {"kind": "docker", "image": "img", **sandbox}
    repos_yaml.write_text(yaml.safe_dump(data))  # `network: None` is no network

    return [r for r in asyncio.run(doctor.run_checks()) if r["name"].startswith(prefix)]


def test_doctor_asks_the_runtime_once_however_many_repos_are_sandboxed(app, tmp_path, monkeypatch):
    """A runtime that does not answer costs doctor its wait once, not once
    per sandboxed repository."""
    ready = AsyncMock(return_value=(True, "ready"))
    monkeypatch.setattr("kraft.worker.backends.docker.DockerBackend.health", ready)
    for name in ("one", "two"):
        asyncio.run(client.ensure_repo(str(make_repo(tmp_path, name))))
    repos_yaml = tmp_path / "templates" / "repos.yaml"
    data = yaml.safe_load(repos_yaml.read_text())
    for entry in data["repos"]:
        entry["sandbox"] = {"kind": "docker", "image": "img"}
    repos_yaml.write_text(yaml.safe_dump(data))

    asyncio.run(doctor.run_checks())
    assert [c.kwargs["refresh"] for c in ready.await_args_list] == [True, False]
