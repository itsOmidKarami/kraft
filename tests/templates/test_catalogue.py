"""`catalogue.choices`: the closed sets the template editors offer, which must
be exactly the values the schema accepts, each with a line saying what it is.
And `catalogue.node_inheritance`: the nodes of a child add-on's chain it marks."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from support.addons import TEAM, chain, example_tree, home

from kraft.policy import TaskPolicyOverride
from kraft.templates import catalogue
from kraft.templates import models as tm
from kraft.templates.library import TemplateLibrary


def values(field: str) -> list[str]:
    return [c["value"] for c in catalogue.choices()[field]]


def builtin(ref: str) -> tm.BuiltinTask:
    return tm.BuiltinTask.model_validate({"id": "t", "kind": "builtin", "ref": ref})


def forge(target: str) -> tm.ForgeTask:
    return tm.ForgeTask.model_validate({"id": "t", "kind": "forge", "target": target})


def agent(inputs: list[str]) -> tm.AgentTask:
    return tm.AgentTask.model_validate(
        {"id": "t", "kind": "agent", "harness": "claude", "prompt": "p", "inputs": inputs}
    )


def grants(names: list[str]) -> TaskPolicyOverride:
    return TaskPolicyOverride.model_validate({"grants": names})


@pytest.mark.parametrize(
    ("field", "accepts"),
    [
        ("ref", builtin),
        ("target", forge),
        ("inputs", lambda v: agent([v])),
        ("grants", lambda v: grants([v])),
    ],
)
def test_every_choice_is_a_value_the_schema_accepts_and_nothing_else_is(field, accepts):
    offered = values(field)
    assert offered and len(offered) == len(set(offered))
    for value in offered:
        accepts(value)
    with pytest.raises(ValidationError):
        accepts("sds")


def test_the_choices_are_the_whole_vocabulary():
    assert values("ref") == [a.value for a in tm.BuiltinAction]
    assert values("target") == [t.value for t in tm.ForgeAction]
    assert values("inputs") == [i.value for i in tm.AgentInput]
    assert values("grants") == ["git-commit", "git-rebase", "git-push"]


def test_each_choice_says_what_it_does_and_a_forge_target_whether_it_waits():
    listed = catalogue.choices()
    assert all(c["summary"].strip() for cs in listed.values() for c in cs)
    assert {c["value"] for c in listed["target"] if c["waits"]} == {
        t.value for t in tm.ForgeAction if t.waits
    }


#: A team whose added `pci_review` task builds on its overridden `security_scan`.
PCI_ON_SCAN = {
    **TEAM,
    "library": {
        **TEAM["library"],
        "tasks": {**TEAM["library"]["tasks"], "pci_review": {"extends": "security_scan"}},
    },
}
SCAN = {"origin": "overridden", "entry": "tasks.team-payments:security_scan"}


@pytest.mark.parametrize(
    ("team", "chain_id", "nodes"),
    [
        pytest.param(
            None,
            "platform-dept:default",
            {"scan": {"origin": "overridden", "entry": "tasks.platform-dept:security_scan"}},
            id="inherited-chain-overridden-task",
        ),
        pytest.param(
            None,
            "team-payments:default",
            {
                "pci_review": {"origin": "added", "entry": "tasks.team-payments:pci_review"},
                "scan": SCAN,
            },
            id="extending-chain",
        ),
        pytest.param(
            PCI_ON_SCAN,
            "team-payments:default",
            {"pci_review": SCAN, "scan": SCAN},
            id="overridden-beats-added",
        ),
        pytest.param(None, "company:default", {}, id="root-chain"),
        pytest.param(None, "local", {}, id="local-chain"),
    ],
)
def test_node_inheritance(tmp_path, team, chain_id, nodes):
    """Each node of a child add-on's chain built from a component the child
    overrides or adds is marked with it; a node that only inherits is not."""
    local = home(tmp_path, chains={"local": chain("base")})
    library = TemplateLibrary.from_yaml_dir(local, addons=list(example_tree(tmp_path, team=team)))
    assert catalogue.node_inheritance(library, chain_id) == nodes
