"""A forge task's `config`, typed by its target, and the flat form it replaced
(`forge-task-config-is-per-target`, `flat-forge-keys-read-as-config`)."""

from __future__ import annotations

import json
import logging

import pytest
import yaml
from drafts.test_authored import SLOTS, TEMPLATES
from pydantic import ValidationError

from kraft.drafts import authored
from kraft.templates import models as tm
from kraft.templates.library import TemplateLibrary
from kraft.templates.models import MaterializedChain
from templates.test_time_caps import _materialize


def _task(target: str, **kw) -> dict:
    return {"id": "t", "kind": "forge", "target": target, **kw}


@pytest.mark.parametrize(
    ("task", "loc"),
    [
        (
            _task(
                "mr.automated_review",
                config={"reviewers": [{"bot": "a"}], "grace_period": "30s"},
            ),
            None,
        ),
        (_task("ci.pipeline", config={"pipeline": "1", "watch_ci": True}), ("config", "watch_ci")),
        (
            _task("ci.pipeline", config={"pipeline": "1", "watch_ci": False, "reviewers": None}),
            None,
        ),
        (_task("mr.automated_review", config={"watch_cl": False}), ("config", "watch_cl")),
        (_task("ci.pipeline", config={"pipeline": "1", "reviwers": None}), ("config", "reviwers")),
        (_task("ci.pipeline", config={}), ("config", "pipeline")),
        (_task("mr.ci", config={"watch_ci": True}), ("config", "watch_ci")),
        (_task("mr.sync", config={"pipeline": None}), None),
        (_task("mr.ci", config={"watch_ci": 0}), ("config", "watch_ci")),
        (_task("mr.ci", config={"x": 1}), ("config", "x")),
    ],
    ids=[
        "the-review-waits-own",
        "another-targets-key",
        "another-targets-clear",
        "a-typo-even-unset",
        "a-typo-even-null",
        "empty-on-a-pipeline-wait",
        "a-target-with-none",
        "a-target-with-none-cleared",
        "zero-is-not-false",
        "an-unknown-key-on-a-target-with-none",
    ],
)
def test_a_forge_tasks_config_takes_its_targets_keys(request, task, loc):
    """`forge-task-config-is-per-target`: another target's key is refused with
    a real value and dropped when null or false; any other key is refused."""
    if loc is None:
        tm.ForgeTask.model_validate(task)
        return
    with pytest.raises(ValidationError) as refused:
        tm.ForgeTask.model_validate(task)
    error = refused.value.errors()[0]
    assert error["loc"] == loc
    if request.node.callspec.id == "another-targets-key":
        assert error["msg"] == "not an option of ci.pipeline, whose config takes pipeline"


POLL = {"polling": {"initial_interval": "30s"}}


@pytest.mark.parametrize(
    ("task", "stored", "want"),
    [
        ({"reviewers": None}, False, {"config": {"reviewers": None}}),
        ({"reviewers": None}, True, {}),
        ({"reviewers": []}, True, {"config": {"reviewers": []}}),
        ({"watch_ci": False}, False, {"config": {"watch_ci": False}}),
        ({"watch_ci": False, "pipeline": None}, True, {}),
        (
            {"wait": {"grace_period": "30s", **POLL}},
            False,
            {"wait": POLL, "config": {"grace_period": "30s"}},
        ),
        ({"wait": {"grace_period": "30s"}}, False, {"config": {"grace_period": "30s"}}),
        ({"kind": "agent", "reviewers": []}, False, {"kind": "agent", "reviewers": []}),
    ],
    ids=[
        "reviewers-null-as-written",
        "reviewers-null-stored",
        "reviewers-empty-stored",
        "watch-ci-false-as-written",
        "defaults-stored",
        "grace-period",
        "grace-period-alone",
        "an-agent-task-untouched",
    ],
)
def test_lift_config_moves_the_flat_keys(task, stored, want):
    assert tm.lift_config(task, stored=stored)[0] == want


def test_lift_config_puts_config_where_the_first_moved_key_stood():
    task = {
        "id": "t",
        "kind": "forge",
        "target": "mr.automated_review",
        "reviewers": [],
        "policy": {},
        "watch_ci": True,
    }
    lifted, moved = tm.lift_config(task, stored=False)
    assert list(lifted) == ["id", "kind", "target", "config", "policy"]
    assert lifted["config"] == {"reviewers": [], "watch_ci": True}
    assert moved == ("reviewers", "watch_ci")


@pytest.mark.parametrize(
    ("task", "says"),
    [
        ({"reviewers": [], "config": {"reviewers": []}}, "reviewers: set it once"),
        (
            {"wait": {"grace_period": "30s"}, "config": {"grace_period": "60s"}},
            "wait.grace_period: set it once",
        ),
    ],
    ids=["reviewers", "grace-period"],
)
def test_a_flat_key_beside_the_same_key_under_config_is_refused(task, says):
    with pytest.raises(ValueError, match=says):
        tm.lift_config(task, stored=False)


_REVIEW = {"id": "review", "kind": "forge", "target": "mr.automated_review"}
_CI = {"id": "ci", "kind": "forge", "target": "mr.ci"}
_V26 = {"reviewers": None, "pipeline": None, "wait": {"grace_period": None, "polling": {}}}
_MAIN = {**_V26, "watch_ci": False}


@pytest.mark.parametrize(
    ("review", "ci", "want"),
    [
        (_V26, _V26, (None, False, 60, True)),
        (
            {
                **_MAIN,
                "reviewers": [{"bot": "a", "check": None}],
                "watch_ci": True,
                "wait": {"grace_period": "30s", "polling": {}},
            },
            _MAIN,
            (["a"], True, 30, False),
        ),
        ({}, {}, (None, False, 60, True)),
    ],
    ids=["v2.6", "main", "before-2.6"],
)
def test_a_stored_snapshot_reads_the_flat_form_silently(review, ci, want, caplog, warnings_afresh):
    """Every snapshot dumped the flat defaults on every forge task: they go
    quietly, set values are lifted, and nothing warns about Kraft's own record."""
    chain = _materialize(
        {
            "id": "c",
            "nodes": [
                {
                    "id": "n",
                    "kind": "exec",
                    "steps": [{"id": "s0", "tasks": [_REVIEW]}, {"id": "s1", "tasks": [_CI]}],
                }
            ],
        }
    )
    stored = json.loads(chain.to_json())
    for step in stored["chain"]["nodes"][0]["steps"]:
        (task,) = step["tasks"]
        task.pop("config")
        task.update(review if task["id"] == "review" else ci)
    with caplog.at_level(logging.WARNING):
        read = MaterializedChain.from_json(json.dumps(stored))
    task = read.chain.nodes[0].steps[0].tasks[0].task
    names = None if task.reviewers is None else [r.bot for r in task.reviewers]
    assert (names, task.watch_ci, task.grace.total_seconds(), task.config is None) == want
    assert "deprecated" not in caplog.text


def test_a_flat_grace_period_reads_beside_a_retired_wait_timeout(warnings_afresh):
    task = tm.ForgeTask.model_validate(
        _task("mr.automated_review", wait={"timeout": "90s", "grace_period": "30s"})
    )
    assert (task.grace.total_seconds(), task.policy.total_time_cap_minutes) == (30, 2)


def test_the_lift_reaches_every_task_position_and_no_component_name(monkeypatch):
    """The walk lifts what the resolver calls a task, at every slot, and reads
    a section's keys as names: `lift_tasks` is checked against the resolver,
    not against `drafts/authored._walk`, which visits containers."""
    library = TemplateLibrary.from_yaml_dir(TEMPLATES)
    candidate, id = library.with_chain(TEMPLATES / "chains" / "slots.yaml", yaml.safe_load(SLOTS))
    model = authored.load(SLOTS)
    # `a.fix_loop.judge` is task `j`, `g.auto_review` task `rv`: ids, not path tails.
    tasks = sorted(authored.at(model, p)["id"] for p in candidate.resolve_chain(id).task_paths)
    seen = []
    monkeypatch.setattr(
        tm, "lift_config", lambda t, *, stored: (seen.append(t.get("id")), (dict(t), ()))[1]
    )
    tm.lift_tasks(yaml.safe_load(SLOTS))
    assert sorted(seen) == tasks

    seen.clear()
    tm.lift_tasks(
        {
            "extends": "default",
            "overrides": {"a.main.t": {"policy": {}}},
            "add": [
                {"after": "a", "node": {"id": "n", "kind": "exec", "tasks": [{"id": "added"}]}}
            ],
        }
    )
    assert sorted(seen, key=str) == sorted([None, "added"], key=str)  # the override has no id

    monkeypatch.undo()
    named = {
        "tasks": {"pipeline": {"kind": "forge", "target": "mr.ci", "reviewers": None}},
        "steps": {"reviewers": {"tasks": [{"id": "t", "kind": "forge", "target": "mr.ci"}]}},
    }
    assert tm.lift_tasks(named) == ["tasks.pipeline.reviewers"]
    assert set(named["tasks"]) == {"pipeline"} and set(named["steps"]) == {"reviewers"}


@pytest.mark.parametrize(
    "data",
    [
        {"nodes": 5},
        {"nodes": ["x", None]},
        {"overrides": [{"a": 1}]},
        {"tasks": None, "steps": "x"},
        {"nodes": [{"id": "n", "tasks": ["x", 3], "steps": [None], "fix_loop": []}]},
        {"add": [{"node": "x"}, 3]},
    ],
    ids=[
        "nodes-a-number",
        "a-scalar-node",
        "overrides-a-list",
        "sections-of-the-wrong-type",
        "scalar-tasks-and-steps",
        "a-scalar-added-node",
    ],
)
def test_lift_tasks_skips_what_it_cannot_walk(data):
    """A malformed file still reaches the loader's own error, not an `AttributeError`."""
    assert tm.lift_tasks(data) == []
