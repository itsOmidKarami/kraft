"""Canonical paths an operator control addresses, and what a retry override
does to a fork's copy of the chain."""

from __future__ import annotations

import pytest
from support.harness import v1_chain

from kraft.templates.forks import ChainPath, ControlScope, PathError, RunFork
from kraft.templates.retry import validate_retry_override

NODES = """
- id: build
  kind: exec
  skippable: false
  steps:
    - id: compile
      skippable: false
      tasks:
        - {id: cc, kind: agent, harness: claude, prompt: p, skippable: false}
        - {id: lint, kind: subprocess, command: "true"}
    - id: test
      tasks: [{id: suite, kind: subprocess, command: "true"}]
- {id: review, kind: gate}
- {id: ship, kind: exec, tasks: [{id: push, kind: subprocess, command: "true"}]}
"""


@pytest.fixture
def chain():
    return v1_chain(NODES, repo="/r")


@pytest.mark.parametrize(
    ("path", "scope", "node_index", "step_index"),
    [
        ("build", ControlScope.NODE, 0, 0),
        ("review", ControlScope.NODE, 1, 0),
        ("build.test", ControlScope.STEP, 0, 1),
        ("build.compile.lint", ControlScope.TASK, 0, 0),
        ("ship.main.push", ControlScope.TASK, 2, 0),
    ],
)
def test_a_canonical_path_resolves_to_its_node_step_and_task(
    chain, path, scope, node_index, step_index
):
    target = ChainPath.parse(chain, path)

    assert (target.scope, target.node_index, target.step_index) == (scope, node_index, step_index)
    assert target.task is None or target.task.path == path


@pytest.mark.parametrize(
    ("path", "says"),
    [
        ("nope", "is not a node"),
        ("build.nope", "has no step 'nope'"),
        ("build.test.nope", "has no task 'nope'"),
        ("review.main", "is a gate"),
        ("build.on_failure.fix.x", "is not a node"),
    ],
)
def test_a_path_the_chain_does_not_have_is_refused_naming_it(chain, path, says):
    with pytest.raises(PathError, match=says):
        ChainPath.parse(chain, path)


@pytest.mark.parametrize(
    ("path", "skippable"),
    [
        ("build", False),
        ("build.compile", False),
        ("build.compile.cc", False),
        ("build.compile.lint", True),
        ("build.test", True),
        ("ship", True),
        ("ship.main", True),
        ("review", True),
    ],
)
def test_every_component_is_skippable_unless_it_says_otherwise(chain, path, skippable):
    """`task-step-and-node-are-skippable-by-default`. Only the component's own
    flag counts: `build.compile.lint` sits in a step and a node that refuse, and
    is still skippable itself."""
    assert ChainPath.parse(chain, path).skippable is skippable


def test_a_path_contains_what_is_under_it_and_nothing_beside_it(chain):
    step = ChainPath.parse(chain, "build.compile")

    assert step.contains("build.compile.cc") and step.contains("build.compile")
    assert not step.contains("build.compile2.cc") and not step.contains("build.test.suite")


def test_a_validated_override_is_the_forks_copy_and_its_record(chain):
    """`validate_retry_override` writes the override into a copy of the chain;
    the fork keeps that copy as its own materialization and records what
    changed. The chain it forked from is untouched
    (`materialized-chain-is-immutable-work-item-input`)."""
    target = ChainPath.parse(chain, "build.compile.cc")
    override = validate_retry_override(
        chain, target.path, task_config={"model": "opus", "effort": "high"}
    )

    fork = RunFork.from_retry(
        work_item_id="w1", parent=None, chain=chain, target=target, override=override, after_seq=0
    )

    patched = ChainPath.parse(fork.chain, "build.compile.cc").task.task
    assert (patched.model, patched.effort) == ("opus", "high")
    assert target.task.task.model is None
    assert fork.override == {
        "path": "build.compile.cc",
        "task_config": {"model": "opus", "effort": "high"},
        "policy": None,
    }


def test_a_task_retry_preserves_its_step_siblings_and_nothing_wider(chain):
    def preserved(path):
        target = ChainPath.parse(chain, path) if path else None
        return RunFork.from_retry(
            work_item_id="w1", parent=None, chain=chain, target=target, override=None, after_seq=0
        ).preserved

    assert preserved("build.compile.lint") == {"build.compile.cc"}
    assert preserved("build.compile") == preserved("build") == preserved(None) == frozenset()
