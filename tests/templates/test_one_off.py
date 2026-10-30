from pathlib import Path

import pytest

from kraft.templates import one_off
from kraft.templates.library import TemplateLibrary

LIBRARY = TemplateLibrary.from_yaml_dir(Path(__file__).parents[2] / "config")
IMPLEMENT = "implementation.main.implement"
AGENT = {"id": "t", "kind": "agent", "harness": "claude", "prompt": "run k6"}


def _add(node: dict, after: str = "verification") -> dict:
    return {"extends": "default", "add": [{"after": after, "node": node}]}


def _parts(authored: dict):
    extends = authored.get("extends")
    parent = LIBRARY.resolve_chain(extends) if extends else None
    library, id = LIBRARY.with_chain(Path("<unsaved>/chains/x.yaml"), {"id": "x", **authored})
    return one_off.custom_parts(authored, parent, library.resolve_chain(id))


@pytest.mark.parametrize(
    ("authored", "expected"),
    [
        pytest.param({"extends": "default"}, [], id="extends-only"),
        pytest.param({"extends": "default", "remove": ["work_brief"]}, [], id="remove-skippable"),
        pytest.param(_add({"id": "v2", "extends": "verification"}), [], id="add-library-node"),
        pytest.param(
            _add({"id": "c", "kind": "exec", "tasks": [{"id": "c", "extends": "code_review"}]}),
            [],
            id="add-library-tasks",
        ),
        pytest.param(
            {"extends": "default", "overrides": {IMPLEMENT: {"model": "x"}}},
            [],
            id="override-model",
        ),
        pytest.param(
            {"extends": "default", "overrides": {IMPLEMENT: {"policy": {"time_cap_minutes": 30}}}},
            [],
            id="override-cap",
        ),
        pytest.param(
            {"id": "x", "nodes": [{"id": "a", "kind": "exec", "tasks": [{"id": "t", **AGENT}]}]},
            [""],
            id="no-extends",
        ),
        pytest.param(
            {"extends": "default", "policy": {"allowed_tools": ["Bash"]}},
            ["policy"],
            id="chain-policy",
        ),
        pytest.param(
            {"extends": "default", "remove": ["spec_approval"]}, ["remove[0]"], id="remove-gate"
        ),
        pytest.param(
            {"extends": "default", "remove": ["draft_merge_request"]},
            ["remove[0]"],
            id="remove-landing",
        ),
        pytest.param(
            _add({"id": "k6", "kind": "exec", "tasks": [AGENT]}),
            ["add[0].node"],
            id="add-inline-prompt",
        ),
        pytest.param(_add({"id": "g", "kind": "gate"}), ["add[0].node"], id="add-gate"),
        pytest.param(
            _add({"id": "v2", "extends": "verification"}, after="final_review"),
            ["add[0].node"],
            id="add-after-final-gate",
        ),
        pytest.param(
            {"extends": "default", "overrides": {IMPLEMENT: {"prompt": "x"}}},
            [f"overrides.{IMPLEMENT}"],
            id="override-prompt",
        ),
        pytest.param(
            {
                "extends": "default",
                "overrides": {IMPLEMENT: {"policy": {"allowed_tools": ["Bash"]}}},
            },
            [f"overrides.{IMPLEMENT}"],
            id="override-tools",
        ),
        pytest.param(
            {"extends": "default", "overrides": {"spec_approval": {"message": "x"}}},
            ["overrides.spec_approval"],
            id="override-gate",
        ),
    ],
)
def test_custom_parts(authored, expected):
    assert [p.path for p in _parts(authored)] == expected


def test_inline_prompt_part_holds_the_full_value():
    (part,) = _parts(_add({"id": "k6", "kind": "exec", "tasks": [AGENT]}))
    assert "run k6" in str(part.value)
    assert part.view() == {"path": part.path, "what": part.what, "value": part.value}


def test_digest_ignores_key_order():
    assert one_off.digest({"a": 1, "b": 2}) == one_off.digest({"b": 2, "a": 1})


def test_prepare_fills_the_id():
    plain, digest = one_off.prepare({"extends": "default"})
    named, named_digest = one_off.prepare({"id": "one-off", "extends": "default"})
    assert plain == named and digest == named_digest and len(digest) == 64
