"""A repo library's chain that extends an add-on's chain: what it may name,
how it resolves, and what an update of that add-on does to it."""

import pytest
import yaml
from support.addons import (
    AGENT,
    addon_json,
    chain,
    edit_addons_yaml,
    install,
    installed,
    instance,
    make_collection,
    publish,
    repo_library_files,
    repo_with_library,
)

from kraft.addons import load, manifest, repo_library, update
from kraft.config import read_yaml, write_yaml
from kraft.templates.library import TemplateLibrary, TemplateLibraryError

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
            LISTS,
            {"ship": {"extends": "app:default"}, "default": chain("base")},
            "a bare name in a repo library is its own, so write 'default'",
            id="its-own-id",
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


@pytest.mark.parametrize("alias", [None, "o"], ids=["name", "alias"])
def test_a_repo_library_chain_extends_an_addon_chain_it_lists(tmp_path, org, app, alias):
    home = _home(tmp_path, org, app, alias=alias)
    _accepted(app, home)

    resolved = _library(home).resolve_chain("app:ship")

    assert [node.id for node in resolved.nodes] == ["build"]
    assert set(resolved.addons) == {"app", alias or "base"}


@pytest.mark.parametrize(
    "state, why",
    [("not-installed", "not installed"), ("disabled", "is disabled")],
    ids=["not-installed", "disabled"],
)
def test_a_repo_library_lists_only_addons_the_instance_loads(tmp_path, org, app, state, why):
    home = _home(tmp_path, org, app, installed=state != "not-installed")
    if state == "disabled":
        edit_addons_yaml(home[0], lambda c: c["addons"].update({ID: False}))

    problems = _refused(app, home, LISTS, {"ship": SHIP})

    said = f"library.yaml: addons: lists {ID}, which this instance does not load ({why})"
    assert said in problems, problems


@pytest.mark.parametrize(
    "chains",
    [{"ship": {"extends": "default"}}, {"default": {"extends": "default"}}],
    ids=["the-library-lacks-it", "the-chain-extends-itself"],
)
def test_a_bare_parent_is_the_librarys_own(app, home, chains):
    write_yaml(home[0] / "chains" / "default.yaml", {"nodes": [{"id": "build", **STEP}]})

    problems = _refused(app, home, LISTS, chains)

    hint = "a bare name in a repo library is its own chain, and an instance chain cannot be"
    assert any(hint in problem for problem in problems), problems


def test_an_addons_bare_parent_gets_no_repo_library_hint(tmp_path):
    """The hint is a repo library's: an add-on's bare parent is its own too,
    and its author never wrote a `.kraft/`."""
    ship = {"ship": {"extends": "default"}}
    addon = installed(tmp_path, "base", library={"tasks": {"base": AGENT}}, chains=ship)
    config_dir, _ = instance(tmp_path, chains={"default": {"nodes": [{"id": "build", **STEP}]}})

    with pytest.raises(TemplateLibraryError, match="no chain named 'base:default'") as raised:
        TemplateLibrary.from_yaml_dir(config_dir, addons=[addon]).resolve_chain("base:ship")

    assert "repo library" not in str(raised.value)


def _disable(config_dir):
    edit_addons_yaml(config_dir, lambda c: c["addons"].update({ID: {"as": "o", "enabled": False}}))


def _forget(config_dir):
    """`base@org` dropped from `addons.yaml` and its lock by hand: unknown here."""
    edit_addons_yaml(config_dir, lambda c: c["addons"].pop(ID))
    lock = read_yaml(config_dir / "addons.lock")
    lock["addons"].pop(ID)
    write_yaml(config_dir / "addons.lock", lock)


@pytest.mark.parametrize(
    "drop, why",
    [
        (_disable, "is disabled"),
        # Unknown: its id names no namespace.
        (_forget, "no chain named 'base@org:default'"),
    ],
    ids=["disabled", "unknown"],
)
def test_a_listed_addon_that_stops_loading_is_never_another(tmp_path, app, drop, why):
    both = make_collection(tmp_path, {"base": BASE, "other": BASE}, name="org")
    home = _home(tmp_path, both, app, alias="o")
    _accepted(app, home)
    # `other@org` now holds the namespace `base`, and `base@org` stops loading.
    install(*home, both, "other", alias="base")
    drop(home[0])

    with pytest.raises(TemplateLibraryError, match=why):
        _library(home).resolve_chain("app:ship")


def _update(home):
    (result,) = update.update(
        [ID], accept=lambda review: True, config_dir=home[0], addons_root=home[1]
    )
    return result


ONE_NODE = {"default": {"nodes": [{"id": "build", **STEP}]}}


@pytest.mark.parametrize(
    "auto, named, then",
    [(False, False, False), (True, False, False), (False, True, False), (False, False, True)],
    ids=["by-hand-applies", "auto-update-holds", "named-by-repos-yaml", "then-the-library-changes"],
)
def test_an_update_that_breaks_a_repo_library_chain_holds_and_never_refuses(
    app, home, org, auto, named, then
):
    _accepted(app, home)
    if named:  # the instance's own file names the chain: still no veto
        entry = {"path": str(app), "id": "app", "default_chain": "app:ship"}
        write_yaml(home[0] / "repos.yaml", {"repos": [entry]})
    publish(org, "base", version="1.1.0", chains=ONE_NODE)  # `check`, which ship removes, is gone
    said = "chain app:ship would stop resolving"

    if auto:
        edit_addons_yaml(home[0], lambda c: c["addons"].update({ID: {"auto_update": True}}))
        outcome = update.auto_update(*home)[ID]
        assert outcome["outcome"] == "held" and said in outcome["message"], outcome
        return
    result = _update(home)

    assert result.outcome == "applied", result.problems
    assert result.review.reach[0].startswith(said), result.review.reach
    with pytest.raises(TemplateLibraryError, match="check"):
        _library(home).resolve_chain("app:ship")
    if then:  # a change of the library that leaves `ship` alone is not refused for it
        text = {**LISTS, "tasks": {"base": {**AGENT, "prompt": "new words"}}}
        repo_library_files(app, library=text, chains={"ship": SHIP})
        found = repo_library.check(app, "app", *home)
        assert found.outcome == "waiting", found.problems
        still = "its chain app:ship still does not resolve (since the add-on update)"
        assert still in found.review.content, found.review


@pytest.mark.parametrize(
    "task, section, line",
    [
        pytest.param(
            {**TASK, "policy": {"budget_usd": 9}},
            "reach",
            "app:ship.nodes[build].tasks[t].policy: limit budget_usd raised, 5 -> 9",
            id="limit-raised-is-reach",
        ),
        pytest.param(
            {**TASK, "prompt": "new words"},
            "content",
            "app:ship.nodes[build].tasks[t]: prompt changed",
            id="text-is-content",
        ),
    ],
)
def test_the_review_shows_what_a_repo_library_chain_will_run(app, home, org, task, section, line):
    _accepted(app, home)
    publish(org, "base", version="1.1.0", library={"tasks": {"base": task}})

    result = _update(home)

    assert result.outcome == "applied", result.problems
    assert any(entry.startswith(line) for entry in getattr(result.review, section)), result.review


HOUSE = {"steering": {"house": {"instructions": "be kind"}}}


@pytest.mark.parametrize(
    "library, runs",
    [
        pytest.param(
            {"tasks": {"base": {**TASK, "prompt": "new words"}}}, ("new words", []), id="text"
        ),
        # A change of shape, which `_holds` would hold; content by the add-on's rules.
        pytest.param(
            {**HOUSE, "tasks": {"base": {**TASK, "steering": ["house"]}}},
            (AGENT["prompt"], ["base:house"]),
            id="a-change-of-shape",
        ),
    ],
)
def test_an_addon_change_reaches_a_repo_library_chain_by_the_addons_rules(
    app, home, org, library, runs
):
    _accepted(app, home)
    edit_addons_yaml(home[0], lambda c: c["addons"].update({ID: {"auto_update": True}}))
    publish(org, "base", version="1.1.0", library=library)

    assert update.auto_update(*home)[ID] == {"outcome": "applied"}
    (task,) = _library(home).resolve_chain("app:ship").nodes[0].tasks()
    assert (task.task.prompt, list(task.task.steering)) == runs


def test_a_change_of_the_librarys_extending_chain_holds(app, home):
    _accepted(app, home)
    # Keeps `check`: a change of shape.
    repo_library_files(app, library=LISTS, chains={"ship": {"extends": "base:default"}})

    found = repo_library.check(app, "app", *home)

    assert "chain app:ship changes more than text" in found.holds, found


LOWERED = {"policy": {"budget_usd": 3}}
#: An accepted library that builds on nothing: one local chain.
ALONE = {"library": {"tasks": {"base": AGENT}}, "chains": {"ship": chain("base")}}


@pytest.mark.parametrize(
    "was, now, section, line",
    [
        pytest.param(
            {},
            # An override may only tighten a parent's limit (`extension.tighten`).
            {"chains": {"ship": {**SHIP, "overrides": {"build.main.t": LOWERED}}}},
            "content",
            "app:ship.nodes[build].tasks[t].policy: budget_usd lowered, 5 -> 3",
            id="its-extending-chain",
        ),
        pytest.param(
            ALONE,
            {},
            "reach",
            "now builds on base@org: uninstalling or disabling it will be refused while this "
            "library lists it",
            id="an-addon-listed",
        ),
        pytest.param({}, ALONE, "content", "no longer builds on base@org", id="an-addon-dropped"),
    ],
)
def test_a_change_of_the_library_shows_what_it_builds_on(app, home, was, now, section, line):
    _accepted(app, home, **was)
    repo_library_files(app, **{"library": LISTS, "chains": {"ship": SHIP}, **now})

    found = repo_library.check(app, "app", *home)

    assert found.outcome == "waiting", found.problems
    assert line in getattr(found.review, section), found.review
