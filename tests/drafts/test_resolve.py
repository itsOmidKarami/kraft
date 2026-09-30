"""The resolve result: every schema error with its path and line, the last
valid model under a YAML error, the change list, impact, and the comment
warning."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import yaml

from kraft.drafts import resolve
from kraft.templates.library import TemplateLibrary

CHAIN = "chains/scratch.yaml"
SCRATCH = """id: scratch
nodes:
  - id: run
    kind: exec
    tasks:
      - {id: t, kind: subprocess, command: "true"}
"""


@pytest.fixture
def st(templates_dir, database):
    return SimpleNamespace(
        templates_dir=templates_dir,
        library=TemplateLibrary.from_yaml_dir(templates_dir),
        instance_policy=None,
        skills_dir=None,
        db=database,
    )


def scratch(st, text, *, published=None, **kw):
    return resolve.resolve(st, "chains", "scratch", {CHAIN: text}, {CHAIN: published}, **kw)


async def test_every_schema_error_is_a_problem_with_its_path_and_line(st):
    text = SCRATCH.replace('command: "true"}', 'command: "true", bogus: 1}') + (
        "  - {id: gate, kind: gate, message: 3}\n"
    )
    problems = scratch(st, text)["problems"]
    assert [(p["path"], p["field"], p["file"], p["line"]) for p in problems] == [
        ("run.main.t", "bogus", CHAIN, 6),
        ("gate", "message", CHAIN, 7),
    ]


async def test_a_yaml_error_keeps_the_newest_model_that_parses(st):
    good = SCRATCH.replace("id: run", "id: kept")
    history = [{"files": {CHAIN: good}}, {"files": {CHAIN: "nodes: [\n"}}]
    result = scratch(st, "id: scratch\nnodes:\n  - {id: [\n", published=SCRATCH, history=history)
    assert result["model"][CHAIN] == yaml.safe_load(good)
    assert result["yaml_error"]["file"] == CHAIN
    assert (result["yaml_error"]["line"], result["yaml_error"]["col"]) == (4, 1)


def chain(*nodes):
    return {"nodes": list(nodes)}


def node(id, *tasks, **fields):
    return {"id": id, "kind": "exec", "tasks": list(tasks), **fields}


def test_changes_are_adds_field_changes_removes_and_moves():
    t = {"id": "t", "kind": "agent"}
    before = chain(node("a", t), node("b"), node("c", t), node("gone", t, {"id": "u"}))
    after = chain(
        node("c", {**t, "model": "opus"}, retry="x"),
        node("a", t, {"id": "new"}),
        node("b"),
    )
    got = resolve.changes(resolve._flat(before), resolve._flat(after))
    assert got == [
        {"path": "c", "kind": "change", "summary": "retry, moved", "fields": ["retry"]},
        {"path": "c.main.t", "kind": "change", "summary": "model", "fields": ["model"]},
        {"path": "a.main.new", "kind": "add", "summary": "added"},
        # its tasks fold into the removed node
        {"path": "gone", "kind": "remove", "summary": "removed"},
    ]


async def test_impact_counts_running_items_and_the_repos_that_default_to_the_chain(st):
    def insert(c):
        for wid, template, status in [
            ("w1", "quick-task", "active"),
            ("w2", "quick-task", "completed"),
            ("w3", None, "paused"),  # no chain named runs `default`
        ]:
            c.execute(
                "INSERT INTO work_items (id, title, repo, chain_template, chain_definition, "
                "status, created_at, updated_at) VALUES (?, 't', '/r', ?, '{}', ?, 'now', 'now')",
                (wid, template, status),
            )

    await st.db.write(insert)
    (st.templates_dir / "repos.yaml").write_text(
        "repos:\n  - {path: /a, default_chain_template: quick-task}\n  - {path: /b}\n"
    )
    assert resolve._impact(st, "quick-task") == {"running": 1, "repos": ["/a"]}
    assert resolve._impact(st, "default") == {"running": 1, "repos": ["/b"]}


@pytest.mark.parametrize(
    ("published", "serialized", "warned"),
    [
        ("# mine\n" + SCRATCH, [CHAIN], True),
        (SCRATCH.replace("id: run", "id: run  # mine"), [CHAIN], True),
        ("# mine\n" + SCRATCH, [], False),
        (SCRATCH, [CHAIN], False),
    ],
    ids=["serialized-comment", "trailing-comment", "typed", "no-comment"],
)
async def test_a_serialized_file_warns_when_its_published_text_has_a_comment(
    st, published, serialized, warned
):
    result = scratch(st, SCRATCH, published=published, serialized=serialized)
    assert bool(result["warnings"]) is warned
