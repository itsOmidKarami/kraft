"""A new home is seeded with thin chains over the built-in kraft-default, and an
existing home is never touched."""

from __future__ import annotations

import shutil
from pathlib import Path

from kraft.addons import builtin, load
from kraft.api.config_check import lint_report
from kraft.cli import admin
from kraft.templates.library import TemplateLibrary

REPO_CONFIG = Path(__file__).resolve().parents[2] / "config"


def _bytes(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def _node_ids(library: TemplateLibrary, chain: str) -> list[str]:
    return [n.id for n in library.resolve_chain(chain).nodes]


def test_a_seeded_home_runs_the_builtin_chain(tmp_path, monkeypatch):
    bundle = tmp_path / "_bundled"
    shutil.copytree(
        REPO_CONFIG,
        bundle / "config",
        ignore=shutil.ignore_patterns("chains", "library.yaml", ".kraft"),
    )
    monkeypatch.setattr(admin, "BUNDLED", bundle)
    monkeypatch.setattr(builtin, "source_dir", lambda: REPO_CONFIG)
    home = tmp_path / "home" / "config"
    assert admin.seed_home(home)

    assert not (home / ".kraft").exists()
    assert (
        (home / "chains" / "default.yaml")
        .read_text()
        .endswith("id: default\nextends: kraft-default:default\n")
    )
    quick = (home / "chains" / "quick-task.yaml").read_text()
    assert quick.endswith("id: quick-task\nextends: kraft-default:quick-task\n")
    assert (home / "library.yaml").read_text().endswith("\ntasks: {}\n")
    addons = load.installed(home, tmp_path / "run" / "addons")
    library = TemplateLibrary.from_yaml_dir(home, skills_dir=None, addons=addons)
    builtin_library = TemplateLibrary.from_yaml_dir(REPO_CONFIG, skills_dir=None)
    for chain in ("default", "quick-task"):
        assert _node_ids(library, chain) == _node_ids(builtin_library, chain)
    report = lint_report(home, addons=addons)
    assert report["valid"], report["issues"]


def test_an_existing_home_is_not_touched(tmp_path, monkeypatch):
    home = tmp_path / "home" / "config"
    shutil.copytree(REPO_CONFIG, home)
    monkeypatch.setenv("KRAFT_CONFIG_DIR", str(home))
    monkeypatch.setenv("KRAFT_HOME", str(tmp_path / "home"))
    before = _bytes(home)

    admin.prepare_home()

    assert _bytes(home) == before
    addons = load.installed(home, tmp_path / "run" / "addons")
    library = TemplateLibrary.from_yaml_dir(home, skills_dir=None, addons=addons)
    # still its own copy: it hides kraft-default:default, and says nothing of it
    assert "kraft-default:" not in (home / "chains" / "default.yaml").read_text()
    assert _node_ids(library, "default") == _node_ids(
        TemplateLibrary.from_yaml_dir(home, skills_dir=None), "default"
    )
