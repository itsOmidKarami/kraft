"""`kraft doctor` — one line per check, exit 1 on any failure."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml
from support.harness import make_repo

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
        "pidfile",
        "mcp token",
        "agent: claude",
        "mcp server",
        "shell completion",
        "bd",
        "worktrees",
    ):
        assert name in _names(rows)


def test_doctor_flags_a_dead_pidfile(app, tmp_path):
    _prime(tmp_path)
    pid_path = tmp_path / "run" / "kraft.pid"
    pid_path.write_text("999999")
    row = _by_name(asyncio.run(doctor.run_checks()), "pidfile")
    assert not row["ok"]
    assert "not running" in row["detail"]


def test_a_world_readable_mcp_token_fails(app, tmp_path):
    _prime(tmp_path).chmod(0o644)
    row = _by_name(asyncio.run(doctor.run_checks()), "mcp token")
    assert not row["ok"]
    assert "0644" in row["detail"]


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


def test_a_disconnected_repo_path_fails(app, tmp_path, monkeypatch):
    repo = make_repo(tmp_path, name="gone")
    asyncio.run(client.ensure_repo(str(repo)))
    (repo / ".git").rename(repo / "not-git")
    row = next(r for r in asyncio.run(doctor.run_checks()) if r["name"].startswith("repo "))
    assert not row["ok"]
    assert "no longer a git repo" in row["detail"]


def test_doctor_reports_a_connected_repo_with_no_setup_command(app, tmp_path):
    """Section 1 removes the Python default deliberately, so every connected
    repo needs a declaration. Finding that out from doctor beats finding it
    out from a parked work item."""
    repo = make_repo(tmp_path, name="undeclared")
    asyncio.run(client.ensure_repo(str(repo)))
    row = next(r for r in asyncio.run(doctor.run_checks()) if r["name"].startswith("setup "))
    assert not row["ok"]
    assert "setup_command" in row["detail"]


def test_doctor_fails_a_repo_entry_carrying_an_unrecognised_key(app, tmp_path):
    """Kraft-4hn34: a key that binds nothing is a failing check, naming it."""
    repo = make_repo(tmp_path, name="stale")
    asyncio.run(client.ensure_repo(str(repo)))
    path = tmp_path / "templates" / "repos.yaml"
    data = yaml.safe_load(path.read_text())
    data["repos"][0]["legacy_widget"] = 1
    path.write_text(yaml.safe_dump(data))

    row = next(r for r in asyncio.run(doctor.run_checks()) if r["name"].startswith("keys "))

    assert not row["ok"]
    assert "legacy_widget" in row["detail"]


@pytest.mark.parametrize("retired", ["default_model", "default_root_merge_policy"])
def test_doctor_passes_a_repo_entry_carrying_a_retired_key(app, tmp_path, retired):
    """Ruling 165: an older install's retired keys are dropped on read with a
    warning of their own, so doctor's unrecognised-key check never sees them
    and must not fail on them."""
    repo = make_repo(tmp_path, name="older")
    asyncio.run(client.ensure_repo(str(repo)))
    path = tmp_path / "templates" / "repos.yaml"
    data = yaml.safe_load(path.read_text())
    data["repos"][0][retired] = "bump" if retired.endswith("policy") else "sonnet"
    path.write_text(yaml.safe_dump(data))

    checks = asyncio.run(doctor.run_checks())

    assert not [r for r in checks if r["name"].startswith("keys ")]
    assert any(r["name"].startswith("repo ") and r["ok"] for r in checks)


def test_doctor_fails_when_a_forge_task_has_no_forge_recorded(app, tmp_path):
    """make_repo never adds an origin, so `probe_repo` records forge: None —
    the state every pre-forge repos.yaml entry is already in."""
    repo = make_repo(tmp_path, name="noforge")
    asyncio.run(client.ensure_repo(str(repo)))

    row = _by_name(asyncio.run(doctor.run_checks()), "forge noforge")

    assert not row["ok"]
    assert "repos.yaml" in row["detail"]


def test_doctor_reports_the_resolved_forge_cli(app, tmp_path, monkeypatch):
    repo = make_repo(tmp_path, name="onforge")
    subprocess.run(
        ["git", "remote", "add", "origin", "git@gitlab.com:group/repo.git"],
        cwd=repo,
        check=True,
    )
    asyncio.run(client.ensure_repo(str(repo)))
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    glab = stub_dir / "glab"
    glab.write_text("#!/bin/sh\nexit 0\n")
    glab.chmod(0o755)
    monkeypatch.setenv("PATH", f"{stub_dir}:{os.environ['PATH']}")

    row = _by_name(asyncio.run(doctor.run_checks()), "forge onforge")

    assert row["ok"]
    assert row["detail"] == "gitlab · glab"


def test_doctor_warns_on_a_repo_on_the_dev_only_fake_forge(app, tmp_path):
    """Ruling 147: `forge: fake` resolves to the in-process `FakeForge`, so
    there is no `fake` binary to look for on PATH -- failing the check would
    tell a `just dev` user their forge is broken when it is not. But green
    would hide a real repo left on a forge that opens nothing and merges
    nothing (review finding 5), so it is a visible warning."""
    repo = make_repo(tmp_path, name="devforge")
    asyncio.run(client.ensure_repo(str(repo)))
    path = tmp_path / "templates" / "repos.yaml"
    data = yaml.safe_load(path.read_text())
    next(r for r in data["repos"] if r["name"] == "devforge")["forge"] = "fake"
    path.write_text(yaml.safe_dump(data))

    row = _by_name(asyncio.run(doctor.run_checks()), "forge devforge")

    assert row["warn"], row
    assert "fake" in row["detail"] and "dev only" in row["detail"]
    assert "nothing" in row["detail"]


def test_no_forge_check_when_no_chain_runs_a_forge_task(app, tmp_path):
    """The check is about forge work the operator's chains actually do: a
    library whose chains run no forge task must not grow a row per repo telling
    them to fix something they are not using. `quick-task` runs none."""
    repo = make_repo(tmp_path, name="quiet")
    asyncio.run(client.ensure_repo(str(repo)))
    assert "forge quiet" in _names(asyncio.run(doctor.run_checks()))
    (tmp_path / "templates" / "chains" / "default.yaml").unlink()

    names = _names(asyncio.run(doctor.run_checks()))

    assert "repo quiet" in names
    assert "forge quiet" not in names


def test_an_orphaned_worktree_is_reported(app, tmp_path):
    _prime(tmp_path)
    (tmp_path / "run" / "worktrees" / "wi-ghost").mkdir(parents=True)
    row = _by_name(asyncio.run(doctor.run_checks()), "worktrees")
    assert not row["ok"]
    assert "wi-ghost" in row["detail"]


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
    assert "uv sync --extra vector" in _by_name(rows, "embeddings")["detail"]


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
    (init.py:122). `HOME` is already a throwaway directory (conftest's autouse
    `_isolated_kraft_home`), so this writes the real file the check reads."""
    home = Path.home()
    home.mkdir(parents=True, exist_ok=True)
    (home / ".claude.json").write_text(
        json.dumps({"mcpServers": {"kraft": {"command": "kraft", "args": ["admin", "mcp"]}}})
    )
    check = _by_name(asyncio.run(doctor.run_checks()), "mcp server")
    assert check["ok"] is True
    assert ".claude.json" in check["detail"]


def test_mcp_check_passes_on_a_repo_scope_mcp_json(app, tmp_path):
    """What `kraft admin init --repo` writes (init._write_repo_mcp_json)."""
    repo = make_repo(tmp_path)
    asyncio.run(client.ensure_repo(str(repo)))
    (repo / ".mcp.json").write_text(json.dumps({"mcpServers": {"kraft": {"command": "kraft"}}}))
    check = _by_name(asyncio.run(doctor.run_checks()), "mcp server")
    assert check["ok"] is True
    assert ".mcp.json" in check["detail"]


def test_mcp_check_fails_when_nothing_registers_kraft(app, tmp_path):
    """A real failure, not an advisory like `bd` or shell completion: every
    worker launch passes `--permission-prompt-tool mcp__kraft__permission_request`,
    and with no registration the agent CLI exits 0 ignoring the flag while a
    running chain's permission asks go unanswered."""
    check = _by_name(asyncio.run(doctor.run_checks()), "mcp server")
    assert check["ok"] is False
    assert "kraft admin init" in check["detail"]


def test_path_check_fails_when_another_kraft_shadows_this_one(monkeypatch):
    """Kraft-xs3ri: every MCP registration runs `kraft` by name, so an older
    install ahead on PATH answers the tools whatever `admin update` installed."""
    from kraft import update

    monkeypatch.setattr(update, "shadowing_kraft", lambda: "/opt/homebrew/bin/kraft")
    row = doctor._path_check()
    assert row["ok"] is False
    assert "/opt/homebrew/bin/kraft" in row["detail"]


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
    assert {r["name"] for r in rows} == {"agent: a", "agent: b"}


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
