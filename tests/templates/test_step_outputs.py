"""Step outputs in the chain schema: what a task declares it leaves behind
(`outputs`), what a later task names (`reads`), and where each is allowed."""

import pytest
from pydantic import ValidationError
from support.harness import v1_chain

from kraft.templates import models as tm


def sub(id: str = "t", **kw) -> dict:
    return {"id": id, "kind": "subprocess", "command": "make report", **kw}


def agent(id: str = "t", **kw) -> dict:
    return {"id": id, "kind": "agent", "harness": "codex", "prompt": "write the files", **kw}


def node(id: str = "n", **kw) -> dict:
    return {"kind": "exec", "id": id, **kw}


def plan(task: dict) -> dict:
    return {"tasks": [task]}


OUT = {"files": ["junit.xml"]}


@pytest.mark.parametrize(
    "declared",
    [
        {},
        {"files": [], "variables": []},
        {"files": ["../x"]},
        {"files": ["/abs"]},
        {"files": [""]},
        {"files": ["a/*.xml"]},
        {"files": ["variables.env"]},
        {"files": ["A.xml", "a.xml"]},
        {"variables": ["lower"]},
        {"variables": ["1X"]},
    ],
    ids=[
        "empty",
        "both-lists-empty",
        "parent",
        "absolute",
        "empty-name",
        "glob",
        "variables-file",
        "case-twins",
        "lowercase-variable",
        "digit-first-variable",
    ],
)
def test_outputs_decl_refuses(declared):
    with pytest.raises(ValidationError):
        tm.OutputsDecl.model_validate(declared)


@pytest.mark.parametrize(
    "declared",
    [
        {"files": ["junit.xml"]},
        {"files": ["report final.xml"]},
        {"variables": ["PIPELINE_ID"]},
    ],
    ids=["plain", "space", "variable"],
)
def test_outputs_decl_accepts(declared):
    assert tm.OutputsDecl.model_validate(declared)


@pytest.mark.parametrize(
    ("reads", "want"),
    [
        (["s.t"], ("n.s.t",)),
        (["m.s.t"], ("m.s.t",)),
        (["s.t", "m.s.t"], ("n.s.t", "m.s.t")),
        (["t"], ValueError),
        (["s..t"], ValueError),
        ([""], ValueError),
    ],
    ids=["own-node", "other-node", "both", "bare-id", "empty-segment", "empty"],
)
def test_resolve_reads(reads, want):
    if want is ValueError:
        with pytest.raises(ValueError):
            tm.resolve_reads("n", reads)
    else:
        assert tm.resolve_reads("n", reads) == want


def _chain(*nodes: dict) -> dict:
    return {"id": "c", "nodes": list(nodes)}


@pytest.mark.parametrize(
    ("chain", "refused"),
    [
        (
            _chain(node(tasks=[sub(outputs=OUT, scope="each_repository")])),
            "fan-out task cannot declare outputs",
        ),
        (
            _chain(node(tasks=[sub(on_failure=plan(sub("fix", outputs=OUT)))])),
            "cannot declare outputs",
        ),
        (
            _chain(
                node(steps=[{"id": "s", "tasks": [sub()], "on_failure": plan(sub(outputs=OUT))}])
            ),
            "cannot declare outputs",
        ),
        (
            _chain(node(tasks=[sub()], on_failure=plan(sub("fix", outputs=OUT)))),
            "cannot declare outputs",
        ),
        (
            _chain(node(tasks=[sub()], fix_loop=plan(sub("fix", outputs=OUT)))),
            "a fix loop: task 'fix' cannot declare outputs",
        ),
        (
            _chain(
                node(tasks=[sub()], fix_loop={**plan(sub("fix")), "judge": sub("j", outputs=OUT)})
            ),
            "a fix loop's judge: task 'j' cannot declare outputs",
        ),
        (
            _chain(node(tasks=[sub()], escalation=sub("esc", outputs=OUT))),
            "the escalation task: task 'esc' cannot declare outputs",
        ),
        (
            _chain(
                node(
                    tasks=[sub()],
                    on_base_changed={
                        "restart_from": "n",
                        "on_conflict": plan(sub("r", outputs=OUT)),
                    },
                )
            ),
            "cannot declare outputs",
        ),
        (
            _chain(
                node("early", tasks=[sub()]),
                node(
                    "m",
                    tasks=[{"id": "merge", "kind": "forge", "target": "mr.merge"}],
                    on_merge_rejected={
                        "restart_from": "early",
                        "repair": plan(sub("r", outputs=OUT)),
                    },
                ),
            ),
            "cannot declare outputs",
        ),
        (
            _chain({"id": "g", "kind": "gate", "auto_review": agent("rev", outputs=OUT)}),
            "a gate's auto_review task: task 'rev' cannot declare outputs",
        ),
        (
            _chain(node(tasks=[{"id": "f", "kind": "forge", "target": "mr.sync", "outputs": OUT}])),
            "outputs",
        ),
    ],
    ids=[
        "fan-out",
        "task-on-failure",
        "step-on-failure",
        "node-on-failure",
        "fix-loop-repair",
        "judge",
        "escalation",
        "on-conflict",
        "merge-rejected-repair",
        "auto-review",
        "forge-task",
    ],
)
def test_outputs_refused_where_not_allowed(chain, refused):
    with pytest.raises(ValidationError, match=refused):
        tm.Chain.model_validate(chain)


def test_outputs_accepted_on_a_node_task_of_both_kinds():
    chain = tm.Chain.model_validate(
        _chain(node(tasks=[sub("a", outputs=OUT), agent("b", outputs={"variables": ["K"]})]))
    )
    assert [t.outputs.files or t.outputs.variables for t in chain.nodes[0].tasks] == [
        ["junit.xml"],
        ["K"],
    ]


@pytest.mark.parametrize(
    "task",
    [
        {"id": "b", "kind": "builtin", "ref": "kraft.mr_rebase"},
        *({"id": "f", "kind": "forge", "target": a.value} for a in tm.ForgeAction),
    ],
    ids=["builtin", *(a.value for a in tm.ForgeAction)],
)
def test_reads_refused_on(task):
    with pytest.raises(ValidationError, match="only an agent or subprocess task reads outputs"):
        tm.Chain.model_validate(_chain(node(tasks=[{**task, "reads": ["s.t"]}])))
    assert tm.Chain.model_validate(_chain(node(tasks=[{**task, "reads": []}])))


@pytest.mark.parametrize("make", [sub, agent], ids=["subprocess", "agent"])
def test_reads_must_be_qualified_on_the_kinds_that_take_it(make):
    assert tm.Chain.model_validate(_chain(node(tasks=[make(reads=["s.t"])])))
    with pytest.raises(ValidationError, match="never a bare task id"):
        tm.Chain.model_validate(_chain(node(tasks=[make(reads=["t"])])))


def test_dumped_defaults_round_trip(tmp_path):
    chain = v1_chain(
        [
            node(
                "n",
                tasks=[
                    sub("a"),
                    agent("b"),
                    {"id": "c", "kind": "builtin", "ref": "kraft.mr_rebase"},
                    {"id": "d", "kind": "forge", "target": "mr.sync"},
                ],
            )
        ],
        repo=tmp_path,
    )
    raw = chain.to_json()
    assert '"reads":[]' in raw and '"outputs":null' in raw
    assert tm.MaterializedChain.from_json(raw).to_json() == raw
