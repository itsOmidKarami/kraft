"""Home resolution and first-run seeding — the two things `kraft` does before
it is just the server the rest of the suite already covers."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import uvicorn

from kraft import cli, paths


def test_kraft_home_follows_env(monkeypatch, tmp_path):
    monkeypatch.setenv("KRAFT_HOME", str(tmp_path / "elsewhere"))
    assert paths.kraft_home() == tmp_path / "elsewhere"
    assert paths.default_run_dir() == tmp_path / "elsewhere" / "run"
    assert paths.default_templates_dir() == tmp_path / "elsewhere" / "templates"


def test_kraft_home_defaults_under_the_user(monkeypatch):
    monkeypatch.delenv("KRAFT_HOME", raising=False)
    assert paths.kraft_home() == Path.home() / ".kraft"


def _bundle(monkeypatch, tmp_path) -> Path:
    bundled = tmp_path / "_bundled" / "templates"
    bundled.mkdir(parents=True)
    (bundled / "policy.yaml").write_text("loops: {}\n")
    (bundled / "access.yaml").write_text("bind: 0.0.0.0\n")
    # A local checkout should never have a live notify.yaml here, but nothing
    # stops `just install`'s `cp -R templates ...` from copying one if one
    # exists (e.g. KRAFT_TEMPLATES_DIR pointed at a checkout mid-dev). Put one
    # in the bundle so seed_home is proven to strip it, not just to never have
    # been given one.
    (bundled / "notify.yaml").write_text("url: https://hook.invalid/t0ken\n")
    # The V1 library and its chains/ subdirectory. Here because the installed
    # layout is V1 and `seed_home` is the only thing that puts it in a home --
    # a bundle with no subdirectory could not catch a seed that stopped
    # recursing.
    (bundled / "library.yaml").write_text("tasks: {}\n")
    (bundled / "chains").mkdir()
    (bundled / "chains" / "default.yaml").write_text("id: default\n")
    monkeypatch.setattr(cli.admin, "BUNDLED", tmp_path / "_bundled")
    return bundled


def test_seed_home_copies_the_bundle_once(monkeypatch, tmp_path):
    _bundle(monkeypatch, tmp_path)
    home = tmp_path / "home" / "templates"

    assert cli.seed_home(home) is True
    assert (home / "policy.yaml").read_text() == "loops: {}\n"
    # per-machine, holds a password hash: never shipped in the bundle
    assert not (home / "access.yaml").exists()
    # per-machine, usually holds a bearer token in the URL: never shipped either
    assert not (home / "notify.yaml").exists()


def test_seed_home_installs_the_v1_library_and_its_chains(monkeypatch, tmp_path):
    """Phase 2's exit criterion is that the *installed* layout is V1, and this is
    the only seam where that happens: `seed_home` is what puts a home's templates
    there. `tests/templates/test_materialization.py` asserts the packaged
    `templates/` tree is V1, which cannot fail if seeding itself breaks -- in
    particular if it stopped recursing into `chains/`."""
    _bundle(monkeypatch, tmp_path)
    home = tmp_path / "home" / "templates"

    assert cli.seed_home(home) is True
    assert (home / "library.yaml").read_text() == "tasks: {}\n"
    assert (home / "chains" / "default.yaml").read_text() == "id: default\n"


def test_seed_home_never_overwrites_an_edited_config(monkeypatch, tmp_path):
    _bundle(monkeypatch, tmp_path)
    home = tmp_path / "home" / "templates"
    home.mkdir(parents=True)
    (home / "policy.yaml").write_text("loops: {mine: 1}\n")

    assert cli.seed_home(home) is False
    assert (home / "policy.yaml").read_text() == "loops: {mine: 1}\n"


def test_seed_home_says_so_when_there_is_nothing_to_seed_with(monkeypatch, tmp_path):
    """A build that skipped `just install` ships no _bundled/. The server would
    die on a bare FileNotFoundError for library.yaml; say what is wrong instead."""
    import pytest

    monkeypatch.setattr(cli.admin, "BUNDLED", tmp_path / "missing")
    home = tmp_path / "home" / "templates"

    with pytest.raises(SystemExit, match="no bundled defaults"):
        cli.seed_home(home)


def test_seed_home_leaves_no_half_seeded_home_behind(monkeypatch, tmp_path):
    """The copy lands under a staging name, so an interrupted seed cannot leave a
    partial config that the next start mistakes for a complete one."""
    import pytest

    _bundle(monkeypatch, tmp_path)
    home = tmp_path / "home" / "templates"
    home.parent.mkdir(parents=True)
    real_rename = Path.rename
    monkeypatch.setattr(Path, "rename", lambda self, target: (_ for _ in ()).throw(OSError("boom")))

    with pytest.raises(OSError):
        cli.seed_home(home)
    assert not home.exists()

    monkeypatch.setattr(Path, "rename", real_rename)
    assert cli.seed_home(home) is True
    assert (home / "policy.yaml").exists()


def test_seeding_records_the_version_it_seeded_from(monkeypatch, tmp_path):
    _bundle(monkeypatch, tmp_path)
    monkeypatch.setattr(cli.admin, "_version", lambda: "9.9.9")
    home = tmp_path / "home" / "templates"

    assert cli.seed_home(home) is True
    assert (home / ".seeded-version").read_text().strip() == "9.9.9"


def test_the_stamp_is_not_yaml_so_load_templates_never_sees_it(monkeypatch, tmp_path):
    """load_templates globs '*.yaml'. A YAML stamp would be read as a malformed
    template and show up as degraded health -- the exact hazard CONFIG_FILES
    exists for. Not being YAML is the fix."""
    _bundle(monkeypatch, tmp_path)
    monkeypatch.setattr(cli.admin, "_version", lambda: "9.9.9")
    home = tmp_path / "home" / "templates"

    cli.seed_home(home)
    stamp = home / ".seeded-version"
    assert stamp.exists()
    assert stamp.suffix != ".yaml"
    assert stamp not in set(home.glob("*.yaml"))


def test_seeding_an_existing_home_still_does_nothing(tmp_path):
    home = tmp_path / "home" / "templates"
    home.mkdir(parents=True)

    assert cli.seed_home(home) is False
    assert not (home / ".seeded-version").exists()


def test_unknown_subcommand_exits_with_a_usable_message(monkeypatch, capsys):
    """argparse owns usage errors now: exit 2, message on stderr, naming the verb."""
    monkeypatch.setattr(cli.admin, "_serve", lambda: pytest.fail("must not serve"))
    with pytest.raises(SystemExit) as exc:
        cli.main(["wat"])
    assert exc.value.code == 2
    assert "wat" in capsys.readouterr().err


def _servable_home(monkeypatch, tmp_path, access_yaml: str) -> Path:
    """A templates dir that already exists, so _serve() skips seeding."""
    home = tmp_path / "templates"
    home.mkdir()
    (home / "access.yaml").write_text(access_yaml)
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(home))
    # setenv, not delenv: `kraft serve --host` writes KRAFT_HOST into os.environ,
    # and monkeypatch records no undo for a delenv of a variable that was absent —
    # so a delenv here would let that write leak into every later test.
    monkeypatch.setenv("KRAFT_HOST", "")
    monkeypatch.setenv("KRAFT_PORT", "")
    # These tests use the literal port from access.yaml, which may be a real
    # port on the machine running the suite (this is Kraft-kquf's own bug, on
    # a dev box that already runs a real kraft daemon on 8765) — not what any
    # of them are testing.
    monkeypatch.setattr(cli.admin, "_refuse_if_addr_taken", lambda *a, **k: None)
    return home


def test_serve_verb_reaches_uvicorn_with_the_configured_bind(monkeypatch, tmp_path):
    _servable_home(monkeypatch, tmp_path, "bind: 127.0.0.1\nport: 8765\n")
    seen = {}

    class FakeConfig:
        def __init__(self, app, **kw):
            seen.update(kw)

    monkeypatch.setattr(uvicorn, "Config", FakeConfig)
    monkeypatch.setattr(cli.admin._SignalLoggingServer, "run", lambda self, *a, **k: None)
    cli.main(["admin", "start"])
    assert seen["host"] == "127.0.0.1"
    assert seen["port"] == 8765


def test_serve_flags_override_access_yaml(monkeypatch, tmp_path):
    _servable_home(monkeypatch, tmp_path, "bind: 127.0.0.1\nport: 8765\n")
    seen = {}

    class FakeConfig:
        def __init__(self, app, **kw):
            seen.update(kw)

    monkeypatch.setattr(uvicorn, "Config", FakeConfig)
    monkeypatch.setattr(cli.admin._SignalLoggingServer, "run", lambda self, *a, **k: None)
    cli.main(["admin", "start", "--port", "9001"])
    assert seen["port"] == 9001
    assert seen["host"] == "127.0.0.1"  # untouched: only the flag given changes


def test_serve_flag_beats_env(monkeypatch, tmp_path):
    _servable_home(monkeypatch, tmp_path, "bind: 127.0.0.1\nport: 8765\n")
    monkeypatch.setenv("KRAFT_PORT", "9002")
    seen = {}

    class FakeConfig:
        def __init__(self, app, **kw):
            seen.update(kw)

    monkeypatch.setattr(uvicorn, "Config", FakeConfig)
    monkeypatch.setattr(cli.admin._SignalLoggingServer, "run", lambda self, *a, **k: None)
    cli.main(["admin", "start", "--port", "9003"])
    assert seen["port"] == 9003


def test_serve_host_flag_cannot_bypass_the_password_check(monkeypatch, tmp_path):
    """The security regression test for this sub-project. A flag must not be a
    way around a check an env var respects."""
    _servable_home(monkeypatch, tmp_path, "bind: 127.0.0.1\nport: 8765\n")
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: pytest.fail("must not bind"))
    with pytest.raises(SystemExit, match="refusing to bind 0.0.0.0"):
        cli.main(["admin", "start", "--host", "0.0.0.0"])


def test_bare_kraft_and_kraft_serve_are_the_same_path(monkeypatch, tmp_path):
    _servable_home(monkeypatch, tmp_path, "bind: 127.0.0.1\nport: 8765\n")
    calls = []

    class FakeConfig:
        def __init__(self, app, **kw):
            calls.append(kw)

    monkeypatch.setattr(uvicorn, "Config", FakeConfig)
    monkeypatch.setattr(cli.admin._SignalLoggingServer, "run", lambda self, *a, **k: None)
    cli.main([])
    cli.main(["admin", "start"])
    assert calls[0] == calls[1]


class _FakePopen:
    """Stands in for `subprocess.Popen`: `on_start` runs synchronously where
    the real child would eventually bind and write the pidfile on its own."""

    def __init__(self, *args, on_start=None, exit_code=None, **kwargs):
        self._exit_code = exit_code
        if on_start is not None:
            on_start()

    def poll(self):
        return self._exit_code


def test_detach_returns_once_the_child_writes_the_pidfile(monkeypatch, tmp_path, capsys):
    _servable_home(monkeypatch, tmp_path, "bind: 127.0.0.1\nport: 8765\n")
    run_dir = tmp_path / "run"
    monkeypatch.setenv("KRAFT_RUN_DIR", str(run_dir))
    # `_read_pid` clears a pidfile naming a dead pid, so the "child" has to
    # write one that is actually alive — this test process's own.
    fake_child_pid = str(os.getpid())

    def fake_popen(*args, **kwargs):
        return _FakePopen(
            on_start=lambda: paths.RunDirs(run_dir).ensure().pid.write_text(fake_child_pid)
        )

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    cli.main(["admin", "start", "--detach"])
    out = capsys.readouterr().out
    assert "127.0.0.1:8765" in out
    assert fake_child_pid in out
    assert "detached" in out


def test_detach_refuses_while_one_is_already_running(monkeypatch, tmp_path, capsys):
    _servable_home(monkeypatch, tmp_path, "bind: 127.0.0.1\nport: 8765\n")
    run_dir = tmp_path / "run"
    monkeypatch.setenv("KRAFT_RUN_DIR", str(run_dir))
    pid_path = paths.RunDirs(run_dir).pid
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text(str(os.getpid()))
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("spawned a second server"))
    with pytest.raises(SystemExit):
        cli.main(["admin", "start", "--detach"])
    assert "already running" in capsys.readouterr().err


def test_detach_reports_a_child_that_exits_before_binding(monkeypatch, tmp_path, capsys):
    _servable_home(monkeypatch, tmp_path, "bind: 127.0.0.1\nport: 8765\n")
    run_dir = tmp_path / "run"
    monkeypatch.setenv("KRAFT_RUN_DIR", str(run_dir))
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: _FakePopen(exit_code=1))
    with pytest.raises(SystemExit):
        cli.main(["admin", "start", "--detach"])
    assert "detached start failed" in capsys.readouterr().err


def test_detach_host_flag_cannot_bypass_the_password_check(monkeypatch, tmp_path, capsys):
    """Same regression as the foreground path: a flag must not be a way around
    a check an env var respects, detached or not."""
    _servable_home(monkeypatch, tmp_path, "bind: 127.0.0.1\nport: 8765\n")
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("must not spawn a child"))
    with pytest.raises(SystemExit, match="refusing to bind 0.0.0.0"):
        cli.main(["admin", "start", "--detach", "--host", "0.0.0.0"])


def test_version_flag_prints_a_version(capsys):
    """A stale install is invisible without this: the build that predated
    argparse fell through to `serve` on every subcommand (Kraft-krd)."""
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert out.startswith("kraft ")
    assert out.strip() != "kraft"


def test_abandon_refuses_without_yes(monkeypatch):
    """The worktree goes with the item, so the destructive verb asks first."""
    ns = cli.build_parser().parse_args(["item", "abandon", "w1"])
    assert ns.yes is False
    with pytest.raises(ValueError, match="--yes"):
        ns.func(ns)
