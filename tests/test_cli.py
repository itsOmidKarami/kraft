"""Home resolution and first-run seeding — the two things `kraft` does before
it is just the server the rest of the suite already covers."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import uvicorn
import yaml
from support.pidfile import hold_pidfile
from support.server import child_env

from kraft import cli, paths


def test_kraft_home_follows_env(monkeypatch, tmp_path):
    monkeypatch.setenv("KRAFT_HOME", str(tmp_path / "elsewhere"))
    assert paths.kraft_home() == tmp_path / "elsewhere"
    assert paths.default_run_dir() == tmp_path / "elsewhere" / "run"
    assert paths.default_config_dir() == tmp_path / "elsewhere" / "config"


def test_kraft_home_defaults_under_the_user(monkeypatch):
    monkeypatch.delenv("KRAFT_HOME", raising=False)
    assert paths.kraft_home() == Path.home() / ".kraft"


def _bundle(monkeypatch, tmp_path) -> Path:
    bundled = tmp_path / "_bundled" / "config"
    bundled.mkdir(parents=True)
    (bundled / "policy.yaml").write_text("loops: {}\n")
    (bundled / "access.yaml").write_text("bind: 0.0.0.0\n")
    # A local checkout should never have a live notify.yaml here, but nothing
    # stops `just install`'s `cp -R templates ...` from copying one if one
    # exists (e.g. KRAFT_CONFIG_DIR pointed at a checkout mid-dev). Put one
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


def test_a_seeded_home_is_the_operators_alone(monkeypatch, tmp_path):
    """As `access.yaml` and `repos.yaml` are, and as a template is once
    Settings saves it: 0644 copies of the package's files flipped to 0600 one
    save at a time."""
    bundled = _bundle(monkeypatch, tmp_path)
    for path in [bundled, *bundled.rglob("*")]:
        path.chmod(0o755 if path.is_dir() else 0o644)
    home = tmp_path / "home" / "templates"

    assert cli.seed_home(home) is True
    modes = {p.relative_to(home).as_posix(): p.stat().st_mode & 0o777 for p in home.rglob("*")}
    assert modes.pop("chains") == 0o700
    assert set(modes.values()) == {0o600}, modes
    assert home.stat().st_mode & 0o777 == 0o700


def test_seed_home_never_overwrites_an_edited_config(monkeypatch, tmp_path):
    _bundle(monkeypatch, tmp_path)
    home = tmp_path / "home" / "templates"
    home.mkdir(parents=True)
    (home / "library.yaml").write_text("tasks: {mine: {}}\n")
    (home / "policy.yaml").write_text("loops: {mine: 1}\n")

    assert cli.seed_home(home) is False
    assert (home / "policy.yaml").read_text() == "loops: {mine: 1}\n"
    assert not (home / "chains").exists()  # a seeded home's deleted file stays deleted


def test_a_templates_dir_made_before_the_first_start_gets_the_rest_seeded(monkeypatch, tmp_path):
    """The sandbox and detectors pages say to write a file into templates/. Made
    before the first start, that directory kept the whole bundle out, and the
    server came up degraded, refusing work. Each file it lacks is seeded; none
    it has is touched."""
    _bundle(monkeypatch, tmp_path)
    home = tmp_path / "home" / "templates"
    (home / "chains").mkdir(parents=True)
    home.chmod(0o755)
    (home / "sandbox.yaml").write_text("cli: podman\n")
    (home / "sandbox.yaml").chmod(0o644)
    (home / "policy.yaml").write_text("loops: {mine: 1}\n")
    (home / "chains" / "mine.yaml").write_text("id: mine\n")

    assert cli.seed_home(home) is True
    assert (home / "sandbox.yaml").read_text() == "cli: podman\n"
    assert (home / "policy.yaml").read_text() == "loops: {mine: 1}\n"
    assert (home / "chains" / "mine.yaml").read_text() == "id: mine\n"
    assert (home / "library.yaml").read_text() == "tasks: {}\n"
    assert (home / "chains" / "default.yaml").read_text() == "id: default\n"
    assert not (home / "notify.yaml").exists()
    assert not home.with_name("templates.seeding").exists()
    modes = {p.relative_to(home).as_posix(): p.stat().st_mode & 0o777 for p in home.rglob("*")}
    assert (home.stat().st_mode & 0o777, modes.pop("chains")) == (0o700, 0o700)
    assert set(modes.values()) == {0o600}, modes
    assert cli.seed_home(home) is False


def test_a_directory_holding_no_kraft_config_is_not_seeded(monkeypatch, tmp_path):
    """A mistyped KRAFT_CONFIG_DIR naming somebody's project: left alone."""
    _bundle(monkeypatch, tmp_path)
    elsewhere = tmp_path / "project"
    elsewhere.mkdir()
    (elsewhere / "README.md").write_text("mine\n")

    assert cli.seed_home(elsewhere) is False
    assert [p.name for p in elsewhere.iterdir()] == ["README.md"]


def test_a_pre_v1_home_is_left_for_the_major_update(monkeypatch, tmp_path):
    _bundle(monkeypatch, tmp_path)
    home = tmp_path / "home" / "templates"
    home.mkdir(parents=True)
    (home / "registry.yaml").write_text("hooks: {}\n")

    assert cli.seed_home(home) is False
    assert [p.name for p in home.iterdir()] == ["registry.yaml"]


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
    (home / "library.yaml").write_text("tasks: {}\n")

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
    """A templates dir already seeded, so _serve() skips seeding."""
    home = tmp_path / "templates"
    home.mkdir()
    (home / "library.yaml").write_text("tasks: {}\n")
    (home / "access.yaml").write_text(access_yaml)
    monkeypatch.setenv("KRAFT_CONFIG_DIR", str(home))
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


def test_serve_flags_override_access_yaml(monkeypatch, tmp_path, capsys):
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
    # --port is not persisted, so every other verb would still dial 8765: say so.
    assert (
        "still dial port 8765; reach this instance with KRAFT_PORT=9001" in capsys.readouterr().out
    )


def test_serve_port_flag_matching_what_clients_dial_prints_no_hint(monkeypatch, tmp_path, capsys):
    _servable_home(monkeypatch, tmp_path, "bind: 127.0.0.1\nport: 9001\n")
    monkeypatch.setattr(uvicorn, "Config", lambda app, **kw: None)
    monkeypatch.setattr(cli.admin._SignalLoggingServer, "run", lambda self, *a, **k: None)
    cli.main(["admin", "start", "--port", "9001"])
    assert "KRAFT_PORT" not in capsys.readouterr().out


@pytest.mark.parametrize(
    ("host", "hint"), [("192.0.2.10", True), ("0.0.0.0", False)], ids=["other-host", "wildcard"]
)
def test_serve_host_flag_says_how_other_commands_reach_it(
    monkeypatch, tmp_path, capsys, host, hint
):
    """--host is not persisted either; a wildcard bind is still reached on the
    loopback address every other verb dials, so it needs no hint."""
    _servable_home(monkeypatch, tmp_path, "bind: 127.0.0.1\nport: 8765\npassword_hash: x\n")
    monkeypatch.setattr(uvicorn, "Config", lambda app, **kw: None)
    monkeypatch.setattr(cli.admin._SignalLoggingServer, "run", lambda self, *a, **k: None)
    cli.main(["admin", "start", "--host", host])
    out = capsys.readouterr().out
    assert ("still dial host 127.0.0.1; reach this instance with KRAFT_HOST=" in out) is hint


def test_serve_on_ipv6_loopback_with_a_port_flag_starts_and_says_where(
    monkeypatch, tmp_path, capsys
):
    """`--port` asks the client which port it dials, after `--host` has set
    KRAFT_HOST. With `::1` that URL was `http://::1:8765`, where the port
    cannot be told from the address, and httpx refused it: start crashed
    before it bound anything."""
    _servable_home(monkeypatch, tmp_path, "bind: 127.0.0.1\nport: 8765\n")
    seen = {}

    class FakeConfig:
        def __init__(self, app, **kw):
            seen.update(kw)

    monkeypatch.setattr(uvicorn, "Config", FakeConfig)
    monkeypatch.setattr(cli.admin._SignalLoggingServer, "run", lambda self, *a, **k: None)
    cli.main(["admin", "start", "--host", "::1", "--port", "9004"])
    assert (seen["host"], seen["port"]) == ("::1", 9004)
    out = capsys.readouterr().out
    assert "still dial port 8765; reach this instance with KRAFT_PORT=9004" in out
    assert "kraft: http://[::1]:9004\n" in out


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
    # The seam `_serve` binds through: a regressed check fails here at once
    # rather than starting a real server on 0.0.0.0.
    monkeypatch.setattr(
        cli.admin._SignalLoggingServer, "run", lambda self, *a, **k: pytest.fail("must not bind")
    )
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


def test_detach_returns_once_the_child_is_up(monkeypatch, tmp_path, capsys):
    _servable_home(monkeypatch, tmp_path, "bind: 127.0.0.1\nport: 8765\n")
    run_dir = tmp_path / "run"
    monkeypatch.setenv("KRAFT_RUN_DIR", str(run_dir))
    # `_read_pid` clears a pidfile no live server holds, so the "child" has to
    # hold one, as a real server does (support.pidfile).
    fake_child_pid = str(os.getpid())
    held = []

    def fake_popen(*args, **kwargs):
        return _FakePopen(
            on_start=lambda: held.append(hold_pidfile(paths.RunDirs(run_dir).ensure().pid))
        )

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    async def healthy():
        return {"status": "ok"}

    # Up means answering /api/health too, not only the pidfile
    # (tests/cli/test_admin.py pins that wait).
    monkeypatch.setattr(cli.admin.client, "health", healthy)
    cli.main(["admin", "start", "--detach"])
    out = capsys.readouterr().out
    assert "127.0.0.1:8765" in out
    assert fake_child_pid in out
    assert "detached" in out


def test_the_detached_child_imports_kraft_not_the_shell_cwd(monkeypatch, tmp_path, capsys):
    """Plain `python -m` puts the cwd first on `sys.path`: started from a repo
    that ships an `mcp.py` (or anything Kraft imports), the child ran that file
    and `start --detach` and `restart` failed."""
    _servable_home(monkeypatch, tmp_path, "bind: 127.0.0.1\nport: 8765\n")
    run_dir = tmp_path / "run"
    monkeypatch.setenv("KRAFT_RUN_DIR", str(run_dir))
    argvs, held = [], []

    def fake_popen(argv, **kwargs):
        argvs.append(argv)
        return _FakePopen(
            on_start=lambda: held.append(hold_pidfile(paths.RunDirs(run_dir).ensure().pid))
        )

    async def healthy():
        return {"status": "ok"}

    monkeypatch.setattr(cli.admin.client, "health", healthy)
    with monkeypatch.context() as patched:  # the real Popen back for the run below
        patched.setattr(subprocess, "Popen", fake_popen)
        cli.main(["admin", "start", "--detach"])
    shadow = tmp_path / "shadow"
    shadow.mkdir()
    for module in ("mcp.py", "argcomplete.py"):
        (shadow / module).write_text('raise SystemExit("shadowed: the repo\'s own module")\n')
    # The child's own command, asked for its help instead of serving.
    done = subprocess.run(
        [*argvs[0], "--help"],
        cwd=shadow,
        capture_output=True,
        text=True,
        env=child_env({"KRAFT_HOME": str(tmp_path / "k")}),
        timeout=30,
    )
    assert "shadowed" not in done.stderr
    assert (done.returncode, "usage: kraft admin start" in done.stdout) == (0, True), done.stderr


@pytest.mark.parametrize("home", ["pointed-by-hand", "1.x-at-the-default"])
def test_detach_refuses_while_one_is_already_running(monkeypatch, tmp_path, capsys, home):
    """And leaves a 1.x home under its old name: the 2.0 rename comes after
    the check, never under a live 1.4 server (`prepare_home`)."""
    old = _servable_home(monkeypatch, tmp_path, "bind: 127.0.0.1\nport: 8765\n")
    if home == "1.x-at-the-default":
        monkeypatch.setenv("KRAFT_HOME", str(tmp_path))
        monkeypatch.setenv("KRAFT_CONFIG_DIR", "")
        monkeypatch.setenv("KRAFT_TEMPLATES_DIR", "")
    run_dir = tmp_path / "run"
    monkeypatch.setenv("KRAFT_RUN_DIR", str(run_dir))
    held = hold_pidfile(paths.RunDirs(run_dir).pid)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("spawned a second server"))
    with pytest.raises(SystemExit):
        cli.main(["admin", "start", "--detach"])
    assert "already running" in capsys.readouterr().err
    assert (old / "library.yaml").is_file() and not (tmp_path / "config").exists()
    held.release()


def test_detach_reports_a_child_that_exits_before_binding(monkeypatch, tmp_path, capsys):
    _servable_home(monkeypatch, tmp_path, "bind: 127.0.0.1\nport: 8765\n")
    run_dir = tmp_path / "run"
    monkeypatch.setenv("KRAFT_RUN_DIR", str(run_dir))
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: _FakePopen(exit_code=1))
    with pytest.raises(SystemExit):
        cli.main(["admin", "start", "--detach"])
    assert "detached start failed" in capsys.readouterr().err


# A failed lifespan's traceback, then the one uvicorn's own exit adds: on
# Python 3.14 the second, with its `~~~^^^` marker lines, outgrows the tail.
_REFUSED_START = (
    "ERROR:    Traceback (most recent call last):\n"
    '  File "/venv/lib/python3.14/site-packages/kraft/db.py", line 1173, in migrate\n'
    "RuntimeError: database schema v50 is newer than code v44\n\n"
    "ERROR:    Application startup failed. Exiting.\n"
    "kraft: shutdown - unhandled exception:\n"
    "Traceback (most recent call last):\n"
    + '  File "/usr/lib/python3.14/asyncio/base_events.py", line 683, in run_forever\n'
    "    self._run_once()\n"
    "    ~~~~~~~~~~~~~~^^\n" * 30 + "SystemExit: 3\n"
)


@pytest.mark.parametrize(
    ("output", "first_line", "tail"),
    [
        (
            _REFUSED_START,
            "kraft: detached start failed: "
            "RuntimeError: database schema v50 is newer than code v44",
            "SystemExit: 3",
        ),
        (
            "kraft: refusing to bind 0.0.0.0 without a password\n",
            "kraft: detached start failed:",
            "refusing to bind",
        ),
    ],
    ids=["traceback-past-the-tail", "no-traceback"],
)
def test_detach_failure_names_this_starts_error(
    monkeypatch, tmp_path, capsys, output, first_line, tail
):
    """The error line first, then the tail: on Python 3.14 the tail alone
    held only uvicorn's exit. Only this start's output counts: an earlier
    run's error in the same log is not why this one failed."""
    _servable_home(monkeypatch, tmp_path, "bind: 127.0.0.1\nport: 8765\n")
    run_dir = tmp_path / "run"
    monkeypatch.setenv("KRAFT_RUN_DIR", str(run_dir))
    log_path = paths.RunDirs(run_dir).ensure().logs / "server.log"
    log_path.write_text("ValueError: an earlier run's own failure\n")

    def fake_popen(*args, stdout, **kwargs):
        stdout.write(output.encode())
        stdout.flush()
        return _FakePopen(exit_code=1)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    with pytest.raises(SystemExit):
        cli.main(["admin", "start", "--detach"])
    err = capsys.readouterr().err
    assert len(_REFUSED_START) > 2000
    assert err.splitlines()[0] == first_line
    assert tail in err
    assert "earlier run" not in err
    assert err.splitlines()[-1] == f"kraft: the whole log is {log_path}"


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
    """The worktree and branch go with the item, so the destructive verb asks
    first, and says unpushed commits go too: archive keeps those, abandon
    does not."""
    ns = cli.build_parser().parse_args(["item", "abandon", "w1"])
    assert ns.yes is False
    with pytest.raises(ValueError, match="--yes") as refused:
        ns.func(ns)
    assert "commits you never pushed are lost" in str(refused.value)


def _run_flooding(tmp_path, body: str) -> subprocess.Popen:
    """`kraft view events` in a child whose verb is `body`, stdout on a pipe.
    Block-buffered, as a pipe normally is: under `PYTHONUNBUFFERED` (which
    the suite may set) nothing is left for the exit's own flush to trip on."""
    script = f"""
import subprocess, sys
from kraft import cli
from kraft.cli import view


def flood(ns):
{textwrap.indent(textwrap.dedent(body), "    ")}


view._cmd_events = flood
cli.main(["view", "events", "w1", "--json"])
"""
    env = child_env({"KRAFT_HOME": str(tmp_path / "home")})
    env.pop("PYTHONUNBUFFERED", None)
    return subprocess.Popen(
        [sys.executable, "-c", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env
    )


def test_a_reader_that_closes_early_ends_the_command_quietly(tmp_path):
    """`kraft view events ID --json | head` once ended in a BrokenPipeError
    traceback. A real pipe, since only a closed reader raises it."""
    # The `finally` is what /dev/null is for: CPython drops the bytes whose
    # write failed, so the exit's flush fails only on output written after the
    # failure, such as a closing line printed on the way out.
    proc = _run_flooding(
        tmp_path,
        """
        try:
            for _ in range(100_000):
                print("x" * 80)
        finally:
            print("done")
        """,
    )
    assert proc.stdout.readline() == b"x" * 80 + b"\n"
    proc.stdout.close()  # what `head` does once it has its lines
    stderr = proc.stderr.read().decode()
    assert proc.wait(timeout=60) == 141, stderr
    assert stderr == ""


def test_a_broken_pipe_that_is_not_stdout_still_fails_loudly(tmp_path):
    """Only stdout's reader leaving is the reader being done. A child process
    that died under us is a failure, and its traceback must show."""
    proc = _run_flooding(
        tmp_path,
        """
        print("started", flush=True)
        child = subprocess.Popen([sys.executable, "-c", "pass"], stdin=subprocess.PIPE)
        child.wait()
        child.stdin.write(b"x" * 1_000_000)
        child.stdin.flush()
        """,
    )
    out, err = proc.communicate(timeout=60)
    assert out == b"started\n"
    assert proc.returncode == 1
    assert b"BrokenPipeError" in err


def test_config_dir_reads_the_new_variable_then_the_1x_one(monkeypatch, tmp_path):
    """`KRAFT_CONFIG_DIR` names the directory; the 1.x `KRAFT_TEMPLATES_DIR`
    still does when the new one is unset, so a shell or a unit written for
    1.x keeps pointing at the same files."""
    monkeypatch.setenv("KRAFT_HOME", str(tmp_path))
    monkeypatch.delenv("KRAFT_CONFIG_DIR", raising=False)
    monkeypatch.delenv("KRAFT_TEMPLATES_DIR", raising=False)
    assert paths.config_dir() == tmp_path / "config"
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(tmp_path / "old"))
    assert paths.config_dir() == tmp_path / "old"
    monkeypatch.setenv("KRAFT_CONFIG_DIR", str(tmp_path / "new"))
    assert paths.config_dir() == tmp_path / "new"
    assert paths.config_dir({"KRAFT_TEMPLATES_DIR": "/elsewhere"}) == Path("/elsewhere")


@pytest.mark.parametrize("marker", ["library.yaml", "registry.yaml"], ids=["1.x", "0.x"])
def test_config_dir_reads_a_home_not_yet_renamed_under_its_old_name(monkeypatch, tmp_path, marker):
    """Between the package upgrade and the first 2.0 start, `kraft admin
    doctor` or `kraft view list` must find the 1.x home where it still is,
    not report an empty `config/` beside it."""
    monkeypatch.setenv("KRAFT_HOME", str(tmp_path))
    monkeypatch.delenv("KRAFT_CONFIG_DIR", raising=False)
    monkeypatch.delenv("KRAFT_TEMPLATES_DIR", raising=False)
    (tmp_path / "templates").mkdir()
    (tmp_path / "templates" / marker).write_text("")
    assert paths.config_dir() == tmp_path / "templates"
    (tmp_path / "config").mkdir()
    assert paths.config_dir() == tmp_path / "config"


@pytest.mark.parametrize("marker", ["library.yaml", "registry.yaml"], ids=["1.x", "0.x"])
def test_seed_home_adopts_a_pre_2_templates_directory_instead_of_seeding(
    monkeypatch, tmp_path, marker
):
    """The 2.0 rename: a home whose config still sits in `templates/` is
    renamed to `config/` once, so an upgrade keeps every edited file and never
    seeds a fresh copy beside the operator's. A 0.x home (a registry, no
    library) is adopted too, so `kraft admin update` finds it where every
    reader now looks and offers the replacement."""
    _bundle(monkeypatch, tmp_path)
    monkeypatch.setenv("KRAFT_HOME", str(tmp_path / "home"))
    old = tmp_path / "home" / "templates"
    old.mkdir(parents=True)
    (old / marker).write_text("tasks: {}\n")
    (old / "policy.yaml").write_text("default: {attempts: 1, wall_clock_s: 1}\n")
    home = paths.default_config_dir()

    assert cli.seed_home(home) is False

    assert not old.exists()
    assert (home / marker).is_file()
    assert (home / "policy.yaml").read_text() == "default: {attempts: 1, wall_clock_s: 1}\n"
    assert not (home / "access.yaml").exists()  # adopted, not seeded over
    # Once: the next start finds `config/` and leaves it alone.
    assert cli.admin.adopt_pre_2_home(home) is False


@pytest.mark.parametrize("where", ["renamed", "pointed-by-hand"])
def test_the_start_carries_the_keys_2_0_moved_between_files(monkeypatch, tmp_path, where):
    """A 1.x `intake.yaml` `max_concurrent` and `policy.yaml` `triggers:` move
    to the file 2.0 reads them from, once, at the first start, each file
    keeping its comments: in the home the start renames, and just the same in
    one an operator points `KRAFT_CONFIG_DIR` at, which is never renamed."""
    monkeypatch.setenv("KRAFT_HOME", str(tmp_path / "home"))
    old = tmp_path / ("elsewhere" if where == "pointed-by-hand" else "home/templates")
    if where == "pointed-by-hand":
        monkeypatch.setenv("KRAFT_CONFIG_DIR", str(old))
    old.mkdir(parents=True)
    (old / "library.yaml").write_text("tasks: {}\n")
    (old / "intake.yaml").write_text("# pickup\nenabled: false\nmax_concurrent: 1  # one\n")
    (old / "policy.yaml").write_text(
        "# caps\ndefault: {attempts: 1, wall_clock_s: 1}\n"
        "triggers:\n  - {cron: '0 9 * * 1', repo: /r, chain: default, title: t}\n"
    )

    home = cli.admin.prepare_home()

    assert home == (old if where == "pointed-by-hand" else paths.default_config_dir())
    assert old.exists() == (where == "pointed-by-hand")
    intake = yaml.safe_load((home / "intake.yaml").read_text())
    policy = yaml.safe_load((home / "policy.yaml").read_text())
    assert "max_concurrent" not in intake and policy["max_concurrent"] == 1
    assert "triggers" not in policy
    assert intake["schedules"] == [
        {"cron": "0 9 * * 1", "repo": "/r", "chain": "default", "title": "t"}
    ]
    assert (home / "intake.yaml").read_text().startswith("# pickup\n")
    assert (home / "policy.yaml").read_text().startswith("# caps\n")


@pytest.mark.parametrize(
    "case",
    ["pointed-by-hand", "no-library", "config-exists"],
)
def test_a_templates_directory_is_adopted_only_at_the_default_location(monkeypatch, tmp_path, case):
    """A directory an operator pointed `KRAFT_CONFIG_DIR` at is theirs to
    name; a `templates/` with no library was never a seeded home; and a home
    already on `config/` keeps whatever sits beside it."""
    monkeypatch.setenv("KRAFT_HOME", str(tmp_path / "home"))
    old = tmp_path / "home" / "templates"
    old.mkdir(parents=True)
    if case != "no-library":
        (old / "library.yaml").write_text("tasks: {}\n")
    target = tmp_path / "elsewhere" if case == "pointed-by-hand" else paths.default_config_dir()
    if case == "config-exists":
        target.mkdir()

    assert cli.admin.adopt_pre_2_home(target) is False
    assert old.exists()
