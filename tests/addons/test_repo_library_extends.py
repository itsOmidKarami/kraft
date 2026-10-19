"""A repo library's chain that extends an add-on's chain: what it may name,
how it resolves, and what an update of that add-on does to it."""

import pytest
import yaml
from support.addons import (
    AGENT,
    addon_json,
    install,
    instance,
    make_collection,
    repo_library_files,
    repo_with_library,
)

from kraft.addons import load, manifest, repo_library, update
from kraft.config import write_yaml
from kraft.templates.library import TemplateLibrary

ID = "base@org"
TASK = {**AGENT, "policy": {"budget_usd": 5}}
STEP = {"kind": "exec", "tasks": [{"id": "t", "extends": "base"}]}
BASE = {
    "library": {"tasks": {"base": TASK}},
    "chains": {"default": {"nodes": [{"id": "build", **STEP}, {"id": "check", **STEP}]}},
}
#: The repo library's chain: the add-on's `default` without `check`.
SHIP = {"extends": "base:default", "remove": ["check"]}
LISTS = {"addons": [ID], "tasks": {"base": AGENT}}
#: SHIP, adding a node whose task names the add-on: not a top-level `extends`.
NESTED = {
    **SHIP,
    "add": [
        {
            "after": "build",
            "node": {"id": "x", "kind": "exec", "tasks": [{"id": "t", "extends": "base:base"}]},
        }
    ],
}


@pytest.fixture
def org(tmp_path):
    """The collection `org`, publishing `base` 1.0.0."""
    return make_collection(tmp_path, {"base": BASE}, name="org")


@pytest.fixture
def app(tmp_path):
    """`app`, with an `origin` holding the library most tests accept. Not
    `test_repo_library.py`'s `make_repo` + `with_origin` body: `just
    check-tests` refuses a copied helper body, fixtures included."""
    return repo_with_library(tmp_path, library=LISTS, chains={"ship": SHIP})


def _home(tmp_path, org, app, *, alias=None, installed=True):
    """An instance that knows `org`, has `base@org` installed (under `alias`)
    unless `installed` is false, and connects `app` under the id `app`."""
    config_dir, addons_dir = instance(tmp_path, {"org": {"git": org.as_uri()}})
    if installed:
        install(config_dir, addons_dir, org, "base", alias=alias)
    write_yaml(config_dir / "repos.yaml", {"repos": [{"path": str(app), "id": "app"}]})
    return config_dir, addons_dir


@pytest.fixture
def home(tmp_path, org, app):
    return _home(tmp_path, org, app)


def _accepted(app, home, *, library=LISTS, chains=None):
    """Push a repo library to `app`'s origin and accept it as a person would."""
    repo_library_files(app, library=library, chains=chains or {"ship": SHIP})
    found = repo_library.check(app, "app", *home)
    assert found.outcome == "waiting", found.problems
    repo_library.accept(found, *home)
    return found


def _library(home):
    return TemplateLibrary.from_yaml_dir(home[0], addons=load.installed(*home))


def _refused(app, home, library, chains):
    repo_library_files(app, library=library, chains=chains)
    found = repo_library.check(app, "app", *home)
    assert found.outcome == "refused", found
    return found.problems


@pytest.mark.parametrize(
    "library, chains, why",
    [
        pytest.param(
            {"tasks": {"base": AGENT}},
            None,
            "under addons: in .kraft/library.yaml",
            id="undeclared",
        ),
        pytest.param(
            {**LISTS, "tasks": {"base": {**AGENT, "extends": "base:base"}}},
            None,
            "library.yaml: tasks.base: extends 'base:base' reaches outside the add-on",
            id="a-task-extends-it",
        ),
        pytest.param(
            LISTS,
            {"ship": NESTED},
            "chains/ship.yaml: add[0].node.tasks[t]: extends 'base:base' reaches outside",
            id="a-nested-node-extends-it",
        ),
        pytest.param(
            {**LISTS, "addons": ID},
            None,
            "library.yaml: addons: must be a list of add-on ids",
            id="not-a-list",
        ),
        pytest.param(
            {**LISTS, "addons": ["base"]},
            None,
            "library.yaml: addons[0]: 'base' is not an add-on id",
            id="not-an-id",
        ),
        pytest.param(
            {**LISTS, "addons": ["library@org"]},
            None,
            "library.yaml: addons[0]: 'library' is this library's own qualifier",
            id="named-library",
        ),
        pytest.param(
            {**LISTS, "addons": [ID, "base@other"]},
            None,
            "library.yaml: addons: lists two add-ons named base: base@org and base@other",
            id="two-of-one-name",
        ),
    ],
)
def test_a_repo_library_names_only_the_addons_it_lists(app, home, library, chains, why):
    problems = _refused(app, home, library, chains or {"ship": SHIP})

    assert any(why in problem for problem in problems), problems


def test_an_addon_lists_no_addons():
    body = yaml.safe_dump({"addons": [ID], "tasks": {"base": AGENT}}).encode()
    found = manifest.addon(addon_json("release"), "addon.json")

    assert update.problems(found, {"library.yaml": ("100644", body)}) == [
        "library.yaml: addons: an add-on may not list add-ons; there are no add-on dependencies"
    ]


@pytest.mark.parametrize("alias", [None], ids=["name"])
def test_a_repo_library_chain_extends_an_addon_chain_it_lists(tmp_path, org, app, alias):
    home = _home(tmp_path, org, app, alias=alias)
    _accepted(app, home)

    resolved = _library(home).resolve_chain("app:ship")

    assert [node.id for node in resolved.nodes] == ["build"]
    assert set(resolved.addons) == {"app", alias or "base"}
