"""The collection this repository publishes (`.kraft/collection.json`): the
shipped `config/` as the add-on `kraft-default`, and the add-ons that build on
it. A change to the shipped library that breaks one of them fails here."""

import shutil
from pathlib import Path

import pytest

from kraft import cli
from kraft.addons import load
from kraft.cli import addon as addon_cli
from kraft.cli.addon import validate
from kraft.progress import implementing_nodes
from kraft.templates.library import TemplateLibrary

REPO = Path(__file__).resolve().parents[2]
CHILDREN = ("kraft-superpowers",)


def _shipped_config(tmp_path: Path) -> Path:
    config = tmp_path / "config"
    shutil.copytree(REPO / "config", config, ignore=shutil.ignore_patterns(".kraft"))
    return config


def test_the_published_collection_validates_against_the_shipped_config(tmp_path):
    report = validate(REPO, (_shipped_config(tmp_path), tmp_path / "addons"))

    assert report["problems"] == []
    assert {a["name"] for a in report["addons"]} == {"kraft-default", *CHILDREN}


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    """An instance seeded from the shipped config with both children installed
    from this working tree, once for the module: an install takes seconds."""
    tmp_path = tmp_path_factory.mktemp("kraft-sdlc")
    config, run_dir = _shipped_config(tmp_path), tmp_path / "run"

    async def no_server():
        return {}

    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("KRAFT_CONFIG_DIR", str(config))
        mp.setenv("KRAFT_RUN_DIR", str(run_dir))
        mp.delenv("KRAFT_WORK_ITEM_ID", raising=False)
        mp.setattr(addon_cli.client, "reload_addons", no_server)
        cli.main(["admin", "addon", "collection", "add", str(REPO)])
        for child in CHILDREN:
            cli.main(["admin", "addon", "install", f"{child}@kraft-sdlc", "-y"])
    return TemplateLibrary.from_yaml_dir(config, addons=load.installed(config, run_dir / "addons"))


@pytest.mark.parametrize("chain", ["default", "quick-task"])
@pytest.mark.parametrize("child", CHILDREN)
def test_a_child_chain_keeps_its_implementing_node(library, child, chain):
    """A method set as `skill:` would make the implementer a judging task, and
    Kraft would tell it implementing is another node's job."""
    assert implementing_nodes(library.resolve_chain(f"{child}:{chain}")) == ["implementation"]


def test_a_child_spec_keeps_the_shipped_steering(library):
    """A child's `steering:` replaces its parent's list, so the spec task's own
    standards must be named again beside the method."""
    [spec] = [n for n in library.resolve_chain("kraft-superpowers:default").nodes if n.id == "spec"]
    [author] = [t.task for step in spec.steps for t in step.tasks]
    assert "kraft-superpowers:project-standards" in author.steering
