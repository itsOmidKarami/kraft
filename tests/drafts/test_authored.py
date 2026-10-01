"""The authored serializer: the round trip over every shipped template file,
the `tasks:` shorthand, key order, block scalars, and canonical-path
addressing with the copy of an inherited container."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from kraft.drafts import authored, ops
from kraft.templates.library import LIBRARY_FILE, TemplateLibrary
from kraft.templates.models import (
    AgentTask,
    BaseChangePolicy,
    BuiltinTask,
    Chain,
    ExecNode,
    FixLoop,
    ForgeTask,
    GateNode,
    RecoveryPlan,
    Step,
    SubprocessTask,
)

TEMPLATES = Path(__file__).resolve().parents[2] / "templates"
SHIPPED = [*sorted((TEMPLATES / "chains").glob("*.yaml")), TEMPLATES / LIBRARY_FILE]
CHAINS = SHIPPED[:-1]


@pytest.fixture(scope="module")
def library():
    return TemplateLibrary.from_yaml_dir(TEMPLATES)


def resolved(library: TemplateLibrary, id: str):
    chain = library.resolve_chain(id)
    return chain.chain.model_dump(), chain.steering


def round_trip(text: str) -> str:
    return authored.dump(authored.load(text))


@pytest.mark.parametrize("file", SHIPPED, ids=lambda p: p.name)
def test_a_shipped_file_dumps_to_the_same_resolved_chains(library, file):
    dumped = yaml.safe_load(round_trip(file.read_text()))
    if file.name == LIBRARY_FILE:
        candidate = library.with_library(dumped, file)
        assert candidate.steering == library.steering
        for id in library.chain_ids:
            assert resolved(candidate, id) == resolved(library, id)
    else:
        candidate, id = library.with_chain(file, dumped)
        assert resolved(candidate, id) == resolved(library, id)


@pytest.mark.parametrize("file", SHIPPED, ids=lambda p: p.name)
def test_dump_is_idempotent(file):
    once = round_trip(file.read_text())
    assert round_trip(once) == once


def test_the_shipped_default_chain_dumps_as_written_less_its_comments():
    text = (TEMPLATES / "chains" / "default.yaml").read_text()
    lines = text.splitlines(keepends=True)
    assert round_trip(text) == "".join(x for x in lines if not x.lstrip().startswith("#"))


SHORTHAND = """\
id: c
nodes:
  - id: a
    kind: exec
    tasks:
      - id: t
        kind: subprocess
        command: 'true'
"""


def test_the_shorthand_loads_as_a_main_step_and_is_written_back():
    model = authored.load(SHORTHAND)
    assert model["nodes"][0]["steps"] == [
        {"id": "main", "tasks": [{"id": "t", "kind": "subprocess", "command": "true"}]}
    ]
    assert authored.dump(model) == SHORTHAND.replace("id: c\n", "id: c\n\n")


def test_a_main_step_with_a_policy_stays_steps():
    model = authored.load(SHORTHAND)
    authored.put(model, "a.main", "policy", {"time_cap_minutes": 5})
    assert "    steps:\n      - id: main\n        tasks:\n" in authored.dump(model)


def test_an_added_key_follows_its_canonical_predecessor_and_authored_keys_keep_their_order():
    authored_order = "    kind: exec\n    skippable: false\n    read_only: true\n"
    model = authored.load(SHORTHAND.replace("    kind: exec\n", authored_order))
    authored.put(model, "a", "icon", "box")
    authored.put(model, "a", "policy.time_cap_minutes", 5)
    authored.put(model, "a", "policy.budget_usd", 1)
    # `skippable` and `read_only` stay before the tasks, where the author put them.
    assert list(model["nodes"][0]) == [
        "id", "kind", "icon", "skippable", "read_only", "steps", "policy"
    ]  # fmt: skip
    assert "    kind: exec\n    icon: box\n    skippable: false\n" in authored.dump(model)
    assert list(model["nodes"][0]["policy"]) == ["budget_usd", "time_cap_minutes"]


def test_a_prompt_keeps_its_line_breaks_as_a_block():
    model = authored.load(SHORTHAND)
    authored.put(model, "a.main.t", "command", "make\nmake test\n")
    assert "        command: |\n          make\n          make test\n" in authored.dump(model)


#: Every component model, so a field added to one is given a place in the
#: canonical order rather than landing last.
CANONICAL = [
    (Chain, authored.CHAIN_KEYS),
    (ExecNode, authored.NODE_KEYS),
    (GateNode, authored.NODE_KEYS),
    (Step, authored.STEP_KEYS),
    (RecoveryPlan, authored.SHAPE_KEYS),
    (FixLoop, authored.SHAPE_KEYS),
    (BaseChangePolicy, authored.NESTED_KEYS["on_base_changed"]),
    *((task, authored.TASK_KEYS) for task in (AgentTask, BuiltinTask, SubprocessTask, ForgeTask)),
]


@pytest.mark.parametrize(("model", "keys"), CANONICAL, ids=[m.__name__ for m, _ in CANONICAL])
def test_every_model_field_has_a_canonical_place(model, keys):
    assert set(model.model_fields) <= set(keys)


SLOTS = """\
id: slots
nodes:
  - id: a
    kind: exec
    tasks:
      - {id: t, kind: subprocess, command: x, on_failure: {tasks: [{id: r, kind: subprocess, command: x}]}}
    on_failure: {tasks: [{id: x, kind: subprocess, command: x}]}
    fix_loop:
      tasks: [{id: f, kind: subprocess, command: x}]
      judge: {id: j, kind: subprocess, command: x}
    escalation: {id: e, kind: subprocess, command: x}
    on_base_changed: {restart_from: a, on_conflict: {tasks: [{id: c, kind: subprocess, command: x}]}}
  - id: b
    kind: exec
    steps:
      - {id: s, tasks: [{id: u, kind: subprocess, command: x}], on_failure: {tasks: [{id: v, kind: subprocess, command: x}]}}
  - id: g
    kind: gate
    auto_review: {id: rv, kind: agent, harness: claude, prompt: p}
"""  # noqa: E501


def test_every_canonical_task_path_addresses_its_authored_task(library):
    candidate, id = library.with_chain(TEMPLATES / "chains" / "slots.yaml", yaml.safe_load(SLOTS))
    model = authored.load(SLOTS)
    found = {p: authored.at(model, p)["id"] for p in candidate.resolve_chain(id).task_paths}
    assert found == {
        "a.main.t": "t",
        "a.main.t.on_failure.main.r": "r",
        "a.on_failure.main.x": "x",
        "a.fix_loop.main.f": "f",
        "a.fix_loop.judge": "j",
        "a.escalation.e": "e",
        "a.on_base_changed.on_conflict.main.c": "c",
        "b.s.u": "u",
        "b.s.on_failure.main.v": "v",
        "g.auto_review": "rv",
    }
    assert authored.at(model, "a.escalation")["id"] == "e"
    assert authored.at(model, "a.escalation.other") is None


@pytest.mark.parametrize("file", CHAINS, ids=lambda p: p.name)
def test_writing_at_every_task_path_owns_what_it_inherits_and_resolves_the_same(library, file):
    lib = authored.load((TEMPLATES / LIBRARY_FILE).read_text())
    model = authored.load(file.read_text())
    for path in library.resolve_chain(file.stem).task_paths:
        task = authored.at(model, path, library=lib, write=True)
        assert task["id"] == path.rpartition(".")[2]
    candidate, id = library.with_chain(file, yaml.safe_load(authored.dump(model)))
    assert resolved(candidate, id) == resolved(library, id)


def test_an_inherited_container_is_copied_in_after_extends():
    lib = authored.load((TEMPLATES / LIBRARY_FILE).read_text())
    model = authored.load((TEMPLATES / "chains" / "default.yaml").read_text())
    path = "merge_request_feedback.ci.await_ci"
    assert authored.at(model, path) is None
    assert authored.at(model, path, library=lib, write=True)["extends"] == "await_mr_ci"
    node = next(n for n in model["nodes"] if n["id"] == "merge_request_feedback")
    assert list(node) == ["id", "extends", "steps", "on_base_changed"]
    assert node["steps"] == lib["nodes"]["post_draft_feedback"]["steps"]


def test_library_paths_are_prefixed_by_their_section():
    lib = authored.load((TEMPLATES / LIBRARY_FILE).read_text())
    at = {
        p: authored.at(lib, p, file=authored.LIBRARY)
        for p in (
            "nodes.verification.review.code_review",
            "nodes.verification.fix_loop.judge",
            "nodes.implementation.main.implement",
            "tasks.implementer",
            "steering.project-standards",
        )
    }
    assert at["nodes.verification.review.code_review"]["extends"] == "code_review"
    assert at["nodes.verification.fix_loop.judge"]["extends"] == "strict_judge"
    assert at["nodes.implementation.main.implement"]["extends"] == "implementer"
    assert at["tasks.implementer"]["profile"] == "strong"
    assert "instructions" in at["steering.project-standards"]


@pytest.mark.parametrize("file", CHAINS, ids=lambda p: p.name)
def test_set_then_reset_of_a_field_on_every_task_resolves_the_same(library, file):
    st = SimpleNamespace(templates_dir=TEMPLATES, library=library)
    draft = ops.Draft(st, file.stem, {f"chains/{file.name}": file.read_text()}, exists=True)
    for path in library.resolve_chain(file.stem).task_paths:
        field = {"path": path, "field": "policy.budget_usd"}
        ops.apply(draft, [{"op": "set_field", **field, "value": 7}, {"op": "reset_field", **field}])
    candidate, id = library.with_chain(file, yaml.safe_load(draft.finish()[f"chains/{file.name}"]))
    assert resolved(candidate, id) == resolved(library, id)
