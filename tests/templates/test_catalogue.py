"""`catalogue.choices`: the closed sets the template editors offer, which must
be exactly the values the schema accepts, each with a line saying what it is."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from kraft.policy import TaskPolicyOverride
from kraft.templates import catalogue
from kraft.templates import models as tm


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
