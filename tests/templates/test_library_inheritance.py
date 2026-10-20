"""A child add-on's library: its parent's merged library, as the parent's
authors wrote it, with the child's own entries laid over it and every
reference read in the child's namespace (design "What a child's library is").

The tree is the design's example: `team-payments` builds on `platform-dept`,
which builds on `company`."""

import json
import re
from dataclasses import replace

import pytest
from support.addons import AGENT, chain, edit_store, home, installed

from kraft.api.routes.settings import _chain_summary
from kraft.templates import catalogue
from kraft.templates.library import Namespace, TemplateLibrary

COMPANY = {
    "library": {
        "steering": {"house": {"instructions": "company rules"}},
        "tasks": {
            "verify": {
                **AGENT,
                "prompt": "company verify",
                "skill": "deploy-review",
                "steering": ["house"],
            },
            "security_scan": {**AGENT, "prompt": "company scan"},
            "hotfix": {**AGENT, "prompt": "company hotfix"},
        },
    },
    "chains": {
        "default": {
            "nodes": [
                {"id": "verification", "kind": "exec", "tasks": [{"id": "v", "extends": "verify"}]},
                {"id": "scan", "kind": "exec", "tasks": [{"id": "s", "extends": "security_scan"}]},
            ]
        },
        "hotfix": chain("hotfix"),
    },
    "skills": {"deploy-review": "company method"},
    "profiles": {"slow": {"model": {"codex": "slow-model"}}},
}
DEPT = {"library": {"tasks": {"security_scan": {**AGENT, "prompt": "dept scan"}}}}
TEAM = {
    "library": {
        "tasks": {
            "security_scan": {"extends": "platform-dept:security_scan", "model": "pci-model"},
            "pci_review": {**AGENT, "prompt": "team pci"},
            # Each file reference by the parent's name: the ancestor that ships it.
            "supered": {
                **AGENT,
                "skill": "platform-dept:deploy-review",
                "steering": ["platform-dept:house"],
                "profile": "platform-dept:slow",
            },
        }
    },
    "chains": {
        "supers": chain("supered"),
        "default": {
            "extends": "platform-dept:default",
            "add": [
                {
                    "after": "verification",
                    "node": {
                        "id": "pci_review",
                        "kind": "exec",
                        "tasks": [{"id": "p", "extends": "pci_review"}],
                    },
                }
            ],
        },
    },
}


def _tree(tmp_path, *, dept_alias=None, team=None):
    """The three add-ons, each as installed, children naming their parents."""
    root = installed(tmp_path, "company", **COMPANY)
    dept = replace(installed(tmp_path, "platform-dept", alias=dept_alias, **DEPT), parent=root.id)
    child = replace(installed(tmp_path, "team-payments", **(team or TEAM)), parent=dept.id)
    return root, dept, child


def _library(tmp_path, **tree):
    local = home(tmp_path, chains={"local": chain("base")})
    return TemplateLibrary.from_yaml_dir(local, addons=list(_tree(tmp_path, **tree)))


def _task(library, chain_id, node):
    resolved = library.resolve_chain(chain_id)
    found = next(n for n in resolved.nodes if n.id == node)
    return next(iter(found.tasks())).task


def _inherited_as_is(library, dept):
    assert _task(library, f"{dept}:hotfix", "n").prompt == "company hotfix"


def _override_replaces(library, dept):
    # A team that declares no `security_scan` inherits the dept's override of
    # company's, and company's `default` runs it.
    inherited = library.component(Namespace.TASKS, "team-payments:security_scan")
    assert inherited.data["prompt"] == "dept scan"
    assert _task(library, "team-payments:default", "scan").prompt == "dept scan"


def _extend_inherited_component(library, dept):
    task = _task(library, "team-payments:default", "scan")
    assert (task.prompt, task.model) == ("dept scan", "pci-model")


def _extend_inherited_chain(library, dept):
    ids = [n.id for n in library.resolve_chain("team-payments:default").nodes]
    assert ids == ["verification", "pci_review", "scan"]


def _late_binding_through_extends(library, dept):
    # The inherited `scan` node, written by company, runs the team's override.
    assert _task(library, "team-payments:default", "scan").model == "pci-model"


def _late_binding_inherited_chain(library, dept):
    task = _task(library, f"{dept}:default", "scan")
    assert (task.prompt, task.model) == ("dept scan", None)


def _skill_inherited(library, dept):
    assert _task(library, "team-payments:default", "verification").skill == "company:deploy-review"


def _skill_overridden(library, dept):
    assert (
        _task(library, "team-payments:default", "verification").skill
        == "team-payments:deploy-review"
    )


def _steering_inherited(library, dept):
    task = _task(library, "team-payments:default", "verification")
    assert task.steering == ["team-payments:house"]
    assert "team-payments:house" in library.steering
    resolved = library.resolve_chain("team-payments:default")
    assert resolved.steering == {"team-payments:house": "company rules"}


def _skill_by_parent_name(library, dept):
    # The team ships its own `deploy-review`; the parent's name reads company's.
    assert _task(library, "team-payments:supers", "n").skill == "company:deploy-review"


def _steering_by_parent_name(library, dept):
    task = _task(library, "team-payments:supers", "n")
    assert task.steering == ["company:house"]
    resolved = library.resolve_chain("team-payments:supers")
    assert resolved.steering == {"company:house": "company rules"}


def _profile_by_parent_name(library, dept):
    assert _task(library, "team-payments:supers", "n").profile == "company:slow"


#: Every check over `TEAM`, which overrides `security_scan` itself.
_ALL = [
    _inherited_as_is,
    _extend_inherited_component,
    _extend_inherited_chain,
    _late_binding_through_extends,
    _late_binding_inherited_chain,
    _skill_inherited,
    _steering_inherited,
    _steering_by_parent_name,
    _profile_by_parent_name,
]

#: A team that inherits `security_scan` as the dept overrode it.
TEAM_INHERITS_SCAN = {
    **TEAM,
    "library": {
        "tasks": {k: v for k, v in TEAM["library"]["tasks"].items() if k != "security_scan"}
    },
}
WITH_SKILL = {**TEAM, "skills": {"deploy-review": "team method"}}


@pytest.mark.parametrize(
    ("tree", "checks"),
    [
        pytest.param({}, [_inherited_as_is], id="inherited-as-is"),
        pytest.param({"team": TEAM_INHERITS_SCAN}, [_override_replaces], id="override-replaces"),
        pytest.param({}, [_extend_inherited_component], id="extend-inherited-component"),
        pytest.param({}, [_extend_inherited_chain], id="extend-inherited-chain"),
        pytest.param({}, [_late_binding_through_extends], id="late-binding-through-extends"),
        pytest.param({}, [_late_binding_inherited_chain], id="late-binding-inherited-chain"),
        pytest.param({"dept_alias": "plat"}, _ALL, id="alias-parent"),
        pytest.param({}, [_skill_inherited], id="skill-inherited"),
        pytest.param({"team": WITH_SKILL}, [_skill_overridden], id="skill-overridden"),
        pytest.param({}, [_steering_inherited], id="steering-inherited"),
        pytest.param({"team": WITH_SKILL}, [_skill_by_parent_name], id="skill-by-parent-name"),
        pytest.param({}, [_steering_by_parent_name], id="steering-by-parent-name"),
        pytest.param({}, [_profile_by_parent_name], id="profile-by-parent-name"),
    ],
)
def test_inherited_library(tmp_path, tree, checks):
    library = _library(tmp_path, **tree)
    for check in checks:
        check(library, tree.get("dept_alias") or "platform-dept")
    assert not library.lint()


#: The team's override of `security_scan` changes its inherited kind: the
#: team's `default` then fails, through the dept's `default` it extends.
BROKEN = {
    **TEAM,
    "library": {
        "tasks": {
            **TEAM["library"]["tasks"],
            "security_scan": {"extends": "platform-dept:security_scan", "kind": "exec"},
        }
    },
}


def _surfaces(library):
    return {
        "chain_ids": library.chain_ids,
        "component_names": [library.component_names(ns) for ns in ("tasks", "steps", "nodes")],
        "steering": list(library.steering),
        "catalogue": catalogue.components(library, []),
        "lint": [issue.message for issue in library.lint()],
    }


@pytest.mark.parametrize(
    ("team", "surface", "shows"),
    [
        pytest.param(TEAM, "chain_ids", "team-payments:hotfix", id="chain-ids"),
        pytest.param(TEAM, "component_names", "team-payments:verify", id="component-names"),
        pytest.param(TEAM, "steering", "team-payments:house", id="steering"),
        pytest.param(TEAM, "catalogue", "platform-dept:security_scan", id="catalogue"),
        # The hidden level's own failure is said, not only that it fails.
        pytest.param(BROKEN, "lint", "cannot change kind", id="lint"),
        pytest.param(TEAM, "provenance", "platform-dept:default", id="provenance-parent"),
        pytest.param(TEAM, "chains-extends", "platform-dept:default", id="chains-extends"),
    ],
)
def test_layers_never_leak(tmp_path, team, surface, shows):
    """The hidden ancestor levels (`<top>^<k>`) are nowhere a person reads:
    a reference into one reads as the ancestor's own name."""
    library = _library(tmp_path, team=team)
    if surface == "provenance":
        shown = library.provenance("team-payments:default")["parent"]
        assert shown == shows
    elif surface == "chains-extends":
        summary = _chain_summary(library, "team-payments:default")
        assert summary["extends"] == shows
        shown = summary
    else:
        shown = _surfaces(library)[surface]
    text = json.dumps(shown, default=str)
    assert shows in text
    assert not re.search(r"[\w-]\^\d", text)


def test_with_addons_drops_descendants(tmp_path):
    """A rebuild whose root ancestor no longer reads leaves it and every
    add-on below it out, and the rest of the library still builds."""
    library = _library(tmp_path)
    root, dept, team = library.addons
    edit_store(root.root, "library.yaml", "tasks: {}\n")
    rebuilt = library.with_addons(library.addons)
    why = {a.id: a.left_out for a in rebuilt.addons}
    assert "does not match" in why["company@acme"]
    assert why["platform-dept@acme"].startswith("its parent company@acme is not loaded")
    assert why["team-payments@acme"].startswith("its parent platform-dept@acme is not loaded")
    assert rebuilt.chain_ids == ("local",)
    assert rebuilt.resolve_chain("local").nodes


def test_read_only_names_the_author(tmp_path):
    """An inherited entry is served under the child and written by an
    ancestor: the 409 names both. A chain and a task of one name are told
    apart by their section."""
    team = {**TEAM, "library": {"tasks": {**TEAM["library"]["tasks"], "hotfix": AGENT}}}
    library = _library(tmp_path, team=team)
    assert catalogue.read_only_message(library, "platform-dept:hotfix") == (
        "platform-dept:hotfix comes from add-on company@acme through platform-dept@acme; "
        "extend it or copy it to your library"
    )
    assert library.authored_by("tasks.team-payments:hotfix").id == "team-payments@acme"
    assert library.authored_by("team-payments:hotfix", "chains").id == "company@acme"
    assert library.authored_by("team-payments:verify", "tasks").id == "company@acme"
    assert library.authored_by("company:hotfix").id == "company@acme"


#: A reference to the team's hidden level, from wherever it is written.
_HIDDEN = {"tasks": {"base": {"extends": "team-payments^1:security_scan"}}}


@pytest.mark.parametrize(
    ("local", "rogue"),
    [
        ({"chains": {"local": chain("team-payments^1:security_scan")}}, None),
        ({"library": _HIDDEN}, None),
        ({}, _HIDDEN),
    ],
    ids=["local-chain", "local-library", "addon-library"],
)
def test_a_hidden_level_is_not_reachable_by_name(tmp_path, local, rogue):
    """Only a child reads its parent's entries, and only by the parent's name:
    a file naming a hidden level is refused, not resolved, whether the
    instance's own or another add-on's."""
    root = home(tmp_path, **local)
    addons = list(_tree(tmp_path))
    if rogue is not None:
        addons.append(installed(tmp_path, "rogue", library=rogue))
    report = TemplateLibrary.lint_dir(root, addons=addons)
    assert [issue.message.split(": ", 1)[1] for issue in report.issues] == [
        "'team-payments^1:security_scan' is no name a library declares; an add-on reads "
        "its parent's entries by the parent's name"
    ]
