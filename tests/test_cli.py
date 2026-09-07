"""Home resolution and first-run seeding — the two things `kraft` does before
it is just the server the rest of the suite already covers."""

from __future__ import annotations

from pathlib import Path

import pytest

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
    (bundled / "registry.yaml").write_text("hooks: {}\n")
    (bundled / "access.yaml").write_text("bind: 0.0.0.0\n")
    # A local checkout should never have a live notify.yaml here, but nothing
    # stops `just install`'s `cp -R templates ...` from copying one if one
    # exists (e.g. KRAFT_TEMPLATES_DIR pointed at a checkout mid-dev). Put one
    # in the bundle so seed_home is proven to strip it, not just to never have
    # been given one.
    (bundled / "notify.yaml").write_text("url: https://hook.invalid/t0ken\n")
    monkeypatch.setattr(cli, "BUNDLED", tmp_path / "_bundled")
    return bundled


def test_seed_home_copies_the_bundle_once(monkeypatch, tmp_path):
    _bundle(monkeypatch, tmp_path)
    home = tmp_path / "home" / "templates"

    assert cli.seed_home(home) is True
    assert (home / "registry.yaml").read_text() == "hooks: {}\n"
    # per-machine, holds a password hash: never shipped in the bundle
    assert not (home / "access.yaml").exists()
    # per-machine, usually holds a bearer token in the URL: never shipped either
    assert not (home / "notify.yaml").exists()


def test_seed_home_never_overwrites_an_edited_config(monkeypatch, tmp_path):
    _bundle(monkeypatch, tmp_path)
    home = tmp_path / "home" / "templates"
    home.mkdir(parents=True)
    (home / "registry.yaml").write_text("hooks: {mine: 1}\n")

    assert cli.seed_home(home) is False
    assert (home / "registry.yaml").read_text() == "hooks: {mine: 1}\n"


def test_seed_home_says_so_when_there_is_nothing_to_seed_with(monkeypatch, tmp_path):
    """A build that skipped `just install` ships no _bundled/. The server would
    die on a bare FileNotFoundError for registry.yaml; say what is wrong instead."""
    import pytest

    monkeypatch.setattr(cli, "BUNDLED", tmp_path / "missing")
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
    assert (home / "registry.yaml").exists()


def test_bare_kraft_still_serves(monkeypatch):
    served = []
    monkeypatch.setattr(cli, "_serve", lambda: served.append(True))
    cli.main([])
    assert served == [True]


def test_unknown_subcommand_exits_with_a_usable_message(monkeypatch, capsys):
    """argparse owns usage errors now: exit 2, message on stderr, naming the verb."""
    monkeypatch.setattr(cli, "_serve", lambda: pytest.fail("must not serve"))
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
    return home


def test_serve_verb_reaches_uvicorn_with_the_configured_bind(monkeypatch, tmp_path):
    _servable_home(monkeypatch, tmp_path, "bind: 127.0.0.1\nport: 8765\n")
    seen = {}
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, **kw: seen.update(kw))
    cli.main(["serve"])
    assert seen["host"] == "127.0.0.1"
    assert seen["port"] == 8765


def test_serve_flags_override_access_yaml(monkeypatch, tmp_path):
    _servable_home(monkeypatch, tmp_path, "bind: 127.0.0.1\nport: 8765\n")
    seen = {}
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, **kw: seen.update(kw))
    cli.main(["serve", "--port", "9001"])
    assert seen["port"] == 9001
    assert seen["host"] == "127.0.0.1"  # untouched: only the flag given changes


def test_serve_flag_beats_env(monkeypatch, tmp_path):
    _servable_home(monkeypatch, tmp_path, "bind: 127.0.0.1\nport: 8765\n")
    monkeypatch.setenv("KRAFT_PORT", "9002")
    seen = {}
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, **kw: seen.update(kw))
    cli.main(["serve", "--port", "9003"])
    assert seen["port"] == 9003


def test_serve_host_flag_cannot_bypass_the_password_check(monkeypatch, tmp_path):
    """The security regression test for this sub-project. A flag must not be a
    way around a check an env var respects."""
    _servable_home(monkeypatch, tmp_path, "bind: 127.0.0.1\nport: 8765\n")
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, **kw: pytest.fail("must not bind"))
    with pytest.raises(SystemExit, match="refusing to bind 0.0.0.0"):
        cli.main(["serve", "--host", "0.0.0.0"])


def test_bare_kraft_and_kraft_serve_are_the_same_path(monkeypatch, tmp_path):
    _servable_home(monkeypatch, tmp_path, "bind: 127.0.0.1\nport: 8765\n")
    calls = []
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, **kw: calls.append(kw))
    cli.main([])
    cli.main(["serve"])
    assert calls[0] == calls[1]


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
    ns = cli.build_parser().parse_args(["abandon", "w1"])
    assert ns.yes is False
    with pytest.raises(ValueError, match="--yes"):
        ns.func(ns)
