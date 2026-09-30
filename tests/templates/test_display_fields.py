"""The authoring fields the chain editor shows and sets: `icon` on an exec
node, a step and a task, a gate's `artifact_required`, and a chain's
`description` (`kraft.templates.models`; split from test_models.py, which is at
its line budget)."""

import pytest
from pydantic import ValidationError

from kraft.templates import models as tm


def agent(id: str = "author", **kw) -> dict:
    return {"id": id, "kind": "agent", "harness": "codex", "prompt": "do it", **kw}


def test_icon_artifact_required_and_description_load_where_they_belong():
    """A well-formed name the UI cannot draw still loads (R32): whether it is
    a Lucide icon is `config_check`'s lint, never the model's."""
    chain = tm.Chain.model_validate(
        {
            "description": "Spec, then build.",
            "nodes": [
                {
                    "id": "spec",
                    "kind": "exec",
                    "icon": "file-text",
                    "steps": [{"id": "s", "icon": "list", "tasks": [agent(icon="no-such-icon")]}],
                },
                {"id": "g", "kind": "gate", "artifact": "spec", "artifact_required": True},
            ],
        }
    )
    node, gate = chain.nodes
    assert (node.icon, node.steps[0].icon, node.steps[0].tasks[0].icon) == (
        "file-text",
        "list",
        "no-such-icon",
    )
    assert gate.artifact_required is True
    assert chain.description == "Spec, then build."


@pytest.mark.parametrize(
    ("node", "match"),
    [
        ({"id": "g", "kind": "gate", "icon": "flag"}, "icon"),
        ({"id": "g", "kind": "gate", "artifact_required": True}, "needs an artifact"),
        (
            {
                "id": "n",
                "kind": "exec",
                "tasks": [agent()],
                "fix_loop": {"tasks": [agent("fix")], "judge": agent("judge", icon="scale")},
            },
            "judge has a fixed icon",
        ),
        ({"id": "n", "kind": "exec", "icon": "File_Text", "tasks": [agent()]}, "pattern"),
    ],
    ids=["icon-on-gate", "required-without-artifact", "icon-on-judge", "not-kebab-case"],
)
def test_a_field_where_it_does_not_belong_is_a_schema_error(node, match):
    with pytest.raises(ValidationError, match=match):
        tm.Chain.model_validate({"nodes": [node]})
