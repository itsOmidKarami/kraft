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
    monkeypatch.setattr(cli, "BUNDLED", tmp_path / "_bundled")
    return bundled


def test_seed_home_copies_the_bundle_once(monkeypatch, tmp_path):
    _bundle(monkeypatch, tmp_path)
    home = tmp_path / "home" / "templates"

    assert cli.seed_home(home) is True
    assert (home / "registry.yaml").read_text() == "hooks: {}\n"
    # per-machine, holds a password hash: never shipped in the bundle
    assert not (home / "access.yaml").exists()


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


def test_unknown_subcommand_exits_with_a_usable_message(monkeypatch):
    monkeypatch.setattr(cli, "_serve", lambda: pytest.fail("must not serve"))
    with pytest.raises(SystemExit) as exc:
        cli.main(["wat"])
    assert "wat" in str(exc.value)
