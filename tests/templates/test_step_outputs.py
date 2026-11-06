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
        {"files": ["."]},
        {"files": ["a//b"]},
        {"files": ["a\\b"]},
        {"files": ["a\x01b"]},
        {"files": ["Variables.env"]},
        {"files": ["a", "a/b"]},
        {"files": ["A/b", "a"]},
        {"variables": ["ID\n"]},
        {"variables": ["ID", "ID"]},
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
        "dot",
        "empty-segment",
        "backslash",
        "control-character",
        "variables-file-by-another-case",
        "file-and-its-own-folder",
        "folder-and-a-file-by-another-case",
        "variable-with-trailing-newline",
        "repeated-variable",
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
        (["m.s.t.u"], ValueError),
    ],
    ids=["own-node", "other-node", "both", "bare-id", "empty-segment", "empty", "four-segments"],
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


@pytest.mark.parametrize(
    ("make", "model"),
    [(sub, tm.SubprocessTask), (agent, tm.AgentTask)],
    ids=["subprocess", "agent"],
)
def test_reads_must_be_qualified_on_the_kinds_that_take_it(make, model):
    assert model.model_validate(make(reads=["s.t"])).reads == ["s.t"]
    with pytest.raises(ValidationError, match="never a bare task id"):
        model.model_validate(make(reads=["t"]))


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


W = sub("w", outputs=OUT)


def _writer_node(id: str = "build", **kw) -> dict:
    """`build`: step `a` writes (`a.w`), step `b` is where a reader may sit."""
    return node(
        id,
        steps=[{"id": "a", "tasks": [W]}, {"id": "b", "tasks": [sub("r", reads=["a.w"])]}],
        **kw,
    )


def _two_steps(reader_step: dict, writer_at: int, **kw) -> dict:
    """A node whose step 1 and step 2 each hold one task; the writer is in `writer_at`."""
    steps = [
        {"id": "s1", "tasks": [sub("t1", **({"outputs": OUT} if writer_at == 1 else {}))]},
        {"id": "s2", "tasks": [sub("t2", **({"outputs": OUT} if writer_at == 2 else {}))]},
    ]
    steps[reader_step["at"] - 1].update({k: v for k, v in reader_step.items() if k != "at"})
    return node("n", steps=steps, **kw)


def _reading(task_id: str, *entries: str) -> dict:
    return plan(sub(task_id, reads=list(entries)))


@pytest.mark.parametrize(
    ("chain", "refused"),
    [
        (
            _chain(node("n", steps=[{"id": "s", "tasks": [sub("r", reads=["a.w"])]}])),
            "n.s.r: reads 'a.w', which names no task",
        ),
        (
            _chain(
                node(
                    "n",
                    steps=[
                        {"id": "a", "tasks": [sub("w")]},
                        {"id": "b", "tasks": [sub("r", reads=["a.w"])]},
                    ],
                )
            ),
            "n.b.r: reads 'a.w', which declares no outputs",
        ),
        (
            _chain(
                node("early", tasks=[sub("r", reads=["late.main.w"])]),
                node("late", tasks=[W]),
            ),
            "early.main.r: reads 'late.main.w', which does not run before it",
        ),
        (
            _chain(
                node(
                    "n",
                    steps=[
                        {"id": "a", "tasks": [sub("r", reads=["b.w"])]},
                        {"id": "b", "tasks": [W]},
                    ],
                )
            ),
            "n.a.r: reads 'b.w', which does not run before it",
        ),
        (
            _chain(node("n", steps=[{"id": "a", "tasks": [W, sub("r", reads=["a.w"])]}])),
            "n.a.r: reads 'a.w', which does not run before it",
        ),
        (
            _chain(
                node("w", tasks=[sub("w", scope="each_repository")]),
                node("n", tasks=[sub("r", reads=["w.main.w"])]),
            ),
            "n.main.r: reads 'w.main.w', which declares no outputs",
        ),
        (
            _chain(
                node("w", tasks=[W], on_failure=plan(sub("h", outputs=None))),
                node("n", tasks=[sub("r", reads=["w.on_failure.main.h"])]),
            ),
            "more than three segments",
        ),
        (
            _chain(_two_steps({"at": 1, "on_failure": _reading("h", "s2.t2")}, writer_at=2)),
            "n.s1.on_failure.main.h: reads 's2.t2', which does not run before it",
        ),
        (
            _chain(
                node("w", tasks=[W]),
                node(
                    "n",
                    tasks=[sub()],
                    on_base_changed={
                        "restart_from": "n",
                        "on_conflict": _reading("h", "w.main.w"),
                    },
                ),
            ),
            "n.on_base_changed.on_conflict.main.h: reads 'w.main.w', "
            "but a task here cannot read step outputs",
        ),
        (
            _chain(
                node("w", tasks=[W]),
                node(
                    "m",
                    tasks=[{"id": "g", "kind": "forge", "target": "mr.merge"}],
                    on_merge_rejected={"restart_from": "w", "repair": _reading("h", "w.main.w")},
                ),
            ),
            "m.on_merge_rejected.repair.main.h: reads 'w.main.w', "
            "but a task here cannot read step outputs",
        ),
        (
            _chain(
                node("w", tasks=[W]),
                {"id": "g", "kind": "gate", "auto_review": agent("rev", reads=["w.main.w"])},
            ),
            "g.auto_review: reads 'w.main.w', but a task here cannot read step outputs",
        ),
    ],
    ids=[
        "missing-writer",
        "writer-declares-nothing",
        "later-node",
        "later-step",
        "same-step-sibling",
        "fan-out-task",
        "handler-slot-task",
        "step-on-failure-of-earlier-step",
        "on-conflict",
        "merge-rejected-repair",
        "auto-review",
    ],
)
def test_reads_ordering_refuses(chain, refused):
    with pytest.raises(ValidationError) as refusal:
        tm.Chain.model_validate(chain)
    assert refused in str(refusal.value)


@pytest.mark.parametrize(
    "chain",
    [
        _chain(node("w", tasks=[W]), node("n", tasks=[sub("r", reads=["w.main.w"])])),
        _chain(_writer_node()),
        _chain(_writer_node(on_failure=_reading("h", "a.w"))),
        _chain(_writer_node(fix_loop=_reading("h", "a.w"))),
        _chain(_writer_node(fix_loop={**plan(sub("f")), "judge": sub("j", reads=["a.w"])})),
        _chain(_writer_node(escalation=sub("esc", reads=["a.w"]))),
        _chain(_two_steps({"at": 2, "on_failure": _reading("h", "s2.t2")}, writer_at=2)),
        _chain(
            _two_steps({"at": 2, "on_failure": _reading("h", "s1.t1")}, writer_at=1),
        ),
        _chain(
            node(
                "n",
                steps=[
                    {"id": "a", "tasks": [W]},
                    {"id": "b", "tasks": [sub("r", on_failure=_reading("h", "a.w"))]},
                ],
            )
        ),
    ],
    ids=[
        "earlier-node",
        "earlier-step",
        "node-on-failure",
        "fix-loop-repair",
        "judge",
        "escalation",
        "step-on-failure-of-its-own-step",
        "step-on-failure-of-an-earlier-writer",
        "task-on-failure",
    ],
)
def test_reads_ordering_accepts(chain):
    assert tm.Chain.model_validate(chain)


def test_a_library_node_used_under_two_ids_reads_by_step_and_task():
    twin = {"steps": [{"id": "a", "tasks": [W]}, {"id": "b", "tasks": [sub("r", reads=["a.w"])]}]}
    chain = tm.Chain.model_validate(_chain(node("one", **twin), node("two", **twin)))
    paths = {
        t.path: t.task.reads for n in tm.ResolvedChain.from_chain(chain).nodes for t in n.tasks()
    }
    assert paths["one.b.r"] == paths["two.b.r"] == ["a.w"]
