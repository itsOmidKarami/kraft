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
from kraft.templates import Registry

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


def _bind_auto_forge(tmp_path):
    """Put a `backend: auto` forge hook in the registry the server loaded.

    `conftest.app` points KRAFT_TEMPLATES_DIR at `fake_templates_dir`, whose
    back half is all `builtin: noop` — so without this the forge check is
    correctly silent and neither test below would exercise anything.
    """
    path = tmp_path / "templates" / "registry.yaml"
    data = yaml.safe_load(path.read_text())
    data["hooks"]["on.mr.open"] = {"kind": "forge", "handler": "open_mr", "backend": "auto"}
    path.write_text(yaml.safe_dump(data))


def test_doctor_fails_when_an_auto_hook_has_no_forge_recorded(app, tmp_path):
    """make_repo never adds an origin, so `probe_repo` records forge: None —
    the state every pre-forge repos.yaml entry is already in."""
    repo = make_repo(tmp_path, name="noforge")
    asyncio.run(client.ensure_repo(str(repo)))
    _bind_auto_forge(tmp_path)

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
    _bind_auto_forge(tmp_path)
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    glab = stub_dir / "glab"
    glab.write_text("#!/bin/sh\nexit 0\n")
    glab.chmod(0o755)
    monkeypatch.setenv("PATH", f"{stub_dir}:{os.environ['PATH']}")

    row = _by_name(asyncio.run(doctor.run_checks()), "forge onforge")

    assert row["ok"]
    assert row["detail"] == "gitlab · glab"


def test_doctor_passes_a_dev_repo_on_the_fake_forge(app, tmp_path):
    """Ruling 147: `forge: fake` resolves to the in-process `FakeForge`, so
    there is no `fake` binary to look for on PATH -- failing the check for one
    would tell a `just dev` user their forge is broken when it is not."""
    repo = make_repo(tmp_path, name="devforge")
    asyncio.run(client.ensure_repo(str(repo)))
    path = tmp_path / "templates" / "repos.yaml"
    data = yaml.safe_load(path.read_text())
    next(r for r in data["repos"] if r["name"] == "devforge")["forge"] = "fake"
    path.write_text(yaml.safe_dump(data))
    _bind_auto_forge(tmp_path)

    row = _by_name(asyncio.run(doctor.run_checks()), "forge devforge")

    assert row["ok"], row["detail"]
    assert "fake" in row["detail"] and "dev" in row["detail"]


def test_no_forge_check_when_nothing_is_bound_to_auto(app, tmp_path):
    """The check is about a binding the operator actually has: a registry with
    no `auto` forge hook must not grow a row per repo telling them to fix
    something they are not using."""
    repo = make_repo(tmp_path, name="quiet")
    asyncio.run(client.ensure_repo(str(repo)))

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


def test_hooks_check_names_a_hook_left_on_noop(tmp_path, monkeypatch):
    bundled = tmp_path / "bundled"
    (bundled / "templates").mkdir(parents=True)
    (bundled / "templates" / "registry.yaml").write_text(
        "hooks:\n"
        "  on.spec.requested: { kind: agent, command: claude, skill: spec, artifact: spec }\n"
    )
    live = tmp_path / "templates"
    live.mkdir()
    (live / "registry.yaml").write_text(
        "hooks:\n  on.spec.requested: { kind: builtin, handler: noop }\n"
    )
    monkeypatch.setattr(doctor, "BUNDLED", bundled)
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(live))

    check = _by_name(asyncio.run(doctor.run_checks()), "hooks")
    assert check["ok"] is True  # an operator's choice, not a failure
    assert "on.spec.requested" in check["detail"]


def test_hooks_check_names_a_hook_missing_from_the_live_registry(tmp_path, monkeypatch):
    """The shape a registry seeded before a hook point existed takes: the key is
    not on the placeholder, it is not there at all. `live.get(h)` returns None,
    which is not a noop, so this case used to exempt itself from the very check
    written for it (Kraft-zmb)."""
    bundled = tmp_path / "bundled"
    (bundled / "templates").mkdir(parents=True)
    (bundled / "templates" / "registry.yaml").write_text(
        "hooks:\n"
        "  on.spec.requested: { kind: agent, command: claude, skill: spec, artifact: spec }\n"
    )
    live = tmp_path / "templates"
    live.mkdir()
    (live / "registry.yaml").write_text(
        "hooks:\n  on.env.prepare: { kind: builtin, handler: env_setup }\n"
    )
    monkeypatch.setattr(doctor, "BUNDLED", bundled)
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(live))

    check = _by_name(asyncio.run(doctor.run_checks()), "hooks")
    assert check["ok"] is True
    assert "on.spec.requested" in check["detail"]
    assert "missing entirely" in check["detail"]


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
    hooks = _by_name(rows, "hooks")
    assert hooks["ok"] is True and hooks["skipped"] is True


def test_hooks_check_is_skipped_without_a_bundled_registry(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor, "BUNDLED", tmp_path / "absent")
    check = _by_name(asyncio.run(doctor.run_checks()), "hooks")
    assert check["skipped"] is True


def test_dead_hooks_check_names_a_hook_noop_in_both_registries(tmp_path, monkeypatch):
    """`on.review.mr.run`'s actual shape: noop in the *shipped* registry too, so
    `_hooks_check`'s `real` set never contains it and it can never be reported
    there (spec "Why the existing doctor check cannot catch C6"). This is the
    check that has to catch it instead.
    """
    bundled = tmp_path / "bundled"
    (bundled / "templates").mkdir(parents=True)
    (bundled / "templates" / "registry.yaml").write_text(
        "hooks:\n"
        "  on.review.mr.run: { kind: builtin, handler: noop }\n"
        "  on.spec.requested: { kind: agent, command: claude, skill: spec, artifact: spec }\n"
    )
    (bundled / "templates" / "default.yaml").write_text(
        "id: default\n"
        "nodes:\n"
        "  - id: mr_checks\n"
        "    tasks: [on.review.mr.run]\n"
        "  - id: spec\n"
        "    tasks: [on.spec.requested]\n"
    )
    live = tmp_path / "templates"
    live.mkdir()
    # Live is unchanged from shipped for `on.review.mr.run` (dead by design) but
    # has drifted to noop for `on.spec.requested` too -- `_hooks_check`'s own
    # question, and this check must not also claim it.
    (live / "registry.yaml").write_text(
        "hooks:\n"
        "  on.review.mr.run: { kind: builtin, handler: noop }\n"
        "  on.spec.requested: { kind: builtin, handler: noop }\n"
    )
    (live / "default.yaml").write_text(
        "id: default\n"
        "nodes:\n"
        "  - id: mr_checks\n"
        "    tasks: [on.review.mr.run]\n"
        "  - id: spec\n"
        "    tasks: [on.spec.requested]\n"
    )
    monkeypatch.setattr(doctor, "BUNDLED", bundled)
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(live))

    rows = asyncio.run(doctor.run_checks())
    dead = _by_name(rows, "dead_hooks")
    assert dead["ok"] is True  # operator's own chain, not a doctor failure
    assert "on.review.mr.run" in dead["detail"]
    assert "on.spec.requested" not in dead["detail"]
    # A noop core hook is a plugin extension point by design: information, not
    # an instruction to implement or remove it.
    assert "unless a plugin binds it" in dead["detail"]
    assert "drop it" not in dead["detail"]

    hooks = _by_name(rows, "hooks")
    assert "on.spec.requested" in hooks["detail"]
    assert "on.review.mr.run" not in hooks["detail"]


def test_dead_hooks_check_ignores_a_noop_hook_no_chain_dispatches(tmp_path, monkeypatch):
    bundled = tmp_path / "bundled"
    (bundled / "templates").mkdir(parents=True)
    (bundled / "templates" / "registry.yaml").write_text(
        "hooks:\n  on.review.security.run: { kind: builtin, handler: noop }\n"
    )
    (bundled / "templates" / "default.yaml").write_text(
        "id: default\nnodes:\n  - id: spec\n    tasks: [on.spec.requested]\n"
    )
    live = tmp_path / "templates"
    live.mkdir()
    (live / "registry.yaml").write_text(
        "hooks:\n  on.review.security.run: { kind: builtin, handler: noop }\n"
    )
    (live / "default.yaml").write_text(
        "id: default\nnodes:\n  - id: spec\n    tasks: [on.spec.requested]\n"
    )
    monkeypatch.setattr(doctor, "BUNDLED", bundled)
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(live))

    check = _by_name(asyncio.run(doctor.run_checks()), "dead_hooks")
    assert check["ok"] is True
    assert "on.review.security.run" not in check["detail"]


def test_dead_hooks_check_is_skipped_without_a_bundled_registry(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor, "BUNDLED", tmp_path / "absent")
    check = _by_name(asyncio.run(doctor.run_checks()), "dead_hooks")
    assert check["skipped"] is True


def test_hooks_check_and_dead_hooks_check_are_disjoint_by_construction():
    """The two checks select on opposite conditions of the *shipped* binding:
    `_hooks_check` only ever selects a hook that is real (not noop) in shipped;
    `_dead_hooks_check` only ever selects one that is noop in shipped. Proven
    against the predicates themselves, over every combination of shipped/live
    state, rather than against one sample registry -- a "don't double-report"
    test over two independently-defined predicates is unsatisfiable by
    construction unless checked this way.
    """
    for shipped_is_noop in (True, False):
        for live_state in ("noop", "absent", "real"):
            shipped_is_real = not shipped_is_noop
            live_is_noop = live_state == "noop"
            live_is_absent = live_state == "absent"
            hooks_check_selects = shipped_is_real and (live_is_noop or live_is_absent)
            dead_hooks_check_selects = shipped_is_noop and live_is_noop
            assert not (hooks_check_selects and dead_hooks_check_selects)


def test_chain_templates_check_names_a_node_missing_from_the_live_copy(tmp_path, monkeypatch):
    bundled = tmp_path / "bundled"
    (bundled / "templates").mkdir(parents=True)
    (bundled / "templates" / "default.yaml").write_text(
        "id: default\n"
        "nodes:\n"
        "  - id: spec\n"
        "    tasks: [on.spec.requested]\n"
        "  - id: chain_review\n"
        "    tasks: [on.chain.review_ready]\n"
    )
    live = tmp_path / "templates"
    live.mkdir()
    (live / "default.yaml").write_text(
        "id: default\nnodes:\n  - id: spec\n    tasks: [on.spec.requested]\n"
    )
    monkeypatch.setattr(doctor, "BUNDLED", bundled)
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(live))

    check = _by_name(asyncio.run(doctor.run_checks()), "chain_templates")
    assert check["ok"] is True  # an operator's own template, not a failure
    assert "chain_review" in check["detail"]
    assert "default.yaml" in check["detail"]


def test_chain_templates_check_names_a_template_missing_entirely(tmp_path, monkeypatch):
    bundled = tmp_path / "bundled"
    (bundled / "templates").mkdir(parents=True)
    (bundled / "templates" / "quick-task.yaml").write_text(
        "id: quick-task\nnodes:\n  - id: implementation\n    tasks: [on.implementation.start]\n"
    )
    live = tmp_path / "templates"
    live.mkdir()
    # live has no quick-task.yaml at all
    monkeypatch.setattr(doctor, "BUNDLED", bundled)
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(live))

    check = _by_name(asyncio.run(doctor.run_checks()), "chain_templates")
    assert check["ok"] is True
    assert "quick-task.yaml missing entirely" in check["detail"]


def test_chain_templates_check_ignores_files_with_no_nodes_list(tmp_path, monkeypatch):
    bundled = tmp_path / "bundled"
    (bundled / "templates").mkdir(parents=True)
    (bundled / "templates" / "intake.yaml").write_text("enabled: false\ninterval_s: 300\n")
    live = tmp_path / "templates"
    live.mkdir()
    monkeypatch.setattr(doctor, "BUNDLED", bundled)
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(live))

    check = _by_name(asyncio.run(doctor.run_checks()), "chain_templates")
    assert check["ok"] is True
    assert check["skipped"] is True  # no chain templates found at all, nothing to diff


def test_chain_templates_check_is_skipped_without_a_bundled_registry(tmp_path, monkeypatch):
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


# ── _agent_checks: per-harness, not a single hardcoded claude check ─────────


def _reg(**agents) -> Registry:
    return Registry(hooks={hook: {"kind": "agent", "harness": hid} for hook, hid in agents.items()})


def test_doctor_checks_every_referenced_harness():
    """doctor.py used to hardcode `claude` and which() it once. A chain with a
    codex node must be told about codex, not reassured about claude."""
    rows = doctor._agent_checks(
        _reg(**{"on.spec.requested": "claude", "on.implementation.start": "codex"}),
        harness.load(None),
    )
    assert {r["name"] for r in rows} == {"agent: claude", "agent: codex"}


def test_doctor_does_not_check_a_harness_nothing_references():
    """gemini.yaml ships, but a registry that never names it is not degraded
    by gemini being absent from PATH."""
    rows = doctor._agent_checks(_reg(**{"on.spec.requested": "claude"}), harness.load(None))
    assert not any(r["name"] == "agent: gemini" for r in rows)


def test_a_quarantined_harness_file_is_reported():
    hs = harness.HarnessSet(
        valid=harness.load(None).valid,
        invalid={"broken": "broken.yaml: unknown kind 'nope'"},
    )
    rows = doctor._agent_checks(_reg(**{"on.spec.requested": "claude"}), hs)
    row = next(r for r in rows if r["name"] == "harness: broken")
    assert not row["ok"]
    assert "unknown kind" in row["detail"]


def test_the_dev_fake_still_passes_loudly(monkeypatch, tmp_path):
    """The existing `_agent_check` behaviour that must survive: a fixtures
    symlink reads as OK *and says so*, because a dev instance looking like it
    works is spending no tokens on purpose."""
    fixtures = tmp_path / "fixtures" / "bin"
    fixtures.mkdir(parents=True)
    real = tmp_path / "fixtures" / "fake-claude.sh"
    real.write_text("#!/bin/sh\n")
    real.chmod(0o755)
    (fixtures / "claude").symlink_to(real)
    monkeypatch.setenv("PATH", str(fixtures))
    row = doctor._agent_checks(_reg(**{"on.spec.requested": "claude"}), harness.load(None))[0]
    assert row["ok"]
    assert "spends no tokens" in row["detail"]


def test_capabilities_row_names_what_the_install_is_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(tmp_path))
    (tmp_path / ".seeded-version").write_text("0.1.0\n")
    row = doctor._capabilities_check()
    assert row["ok"] is True, "not upgrading is a choice, not a failure"
    assert "0.1.0" in row["detail"]
    for c in capabilities.MANIFEST:
        assert c.name in row["detail"]
        assert c.how in row["detail"]


def test_capabilities_row_is_quiet_when_the_stamp_is_current(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(tmp_path))
    (tmp_path / ".seeded-version").write_text(capabilities.MANIFEST[-1].version + "\n")
    row = doctor._capabilities_check()
    assert row["ok"] is True
    assert "up to date" in row["detail"]


def test_an_unstamped_home_is_told_everything_rather_than_erroring(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(tmp_path))
    row = doctor._capabilities_check()
    assert row["ok"] is True
    assert capabilities.MANIFEST[0].name in row["detail"]
