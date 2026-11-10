"""A plan's `## Slices` section as Lite reads it (`kl_slices`), and the coverage
`plan-slices --check` holds a proposal to: every plan task in a slice. That
Kraft reads the same section the same way is tests/plugins/test_kraft_lite_parity.py's,
in the Kraft repo."""

from __future__ import annotations

import json

import pytest

import kl_slices


def plan(slices: str, tasks: int = 4, done: tuple[int, ...] = ()) -> str:
    headings = "\n".join(
        f"### Task {n}: Step {n}{' [DONE]' if n in done else ''}" for n in range(1, tasks + 1)
    )
    return f"# Plan\n\n{headings}\n\n## Slices\n\n{slices}\n"


@pytest.mark.parametrize(
    "section, expected",
    [
        pytest.param(
            "- **Slice 1: Writers.** Tasks 1-2.\n- **Slice 2: CLI.** Tasks 3, 4.",
            [
                {"key": "1", "title": "Writers", "tasks": [1, 2], "after": []},
                {"key": "2", "title": "CLI", "tasks": [3, 4], "after": []},
            ],
            id="independent",
        ),
        pytest.param(
            "- **Slice 1: Writers.** Tasks 2-3.\n- **Slice 2: CLI.** Task 4.",
            [
                {"key": "groundwork", "title": "Groundwork", "tasks": [1], "after": []},
                {"key": "1", "title": "Writers", "tasks": [2, 3], "after": ["groundwork"]},
                {"key": "2", "title": "CLI", "tasks": [4], "after": ["groundwork"]},
            ],
            id="a_task_in_no_slice_is_groundwork_built_first",
        ),
        pytest.param(
            "- **Slice 1: Writers.** Tasks 1-3.\n- **Slice 2: CLI.** Task 4. Comes after slice 1.",
            [
                {"key": "1", "title": "Writers", "tasks": [1, 2, 3], "after": []},
                {"key": "2", "title": "CLI", "tasks": [4], "after": ["1"]},
            ],
            id="lite_after",
        ),
        pytest.param(
            "- **Slice 1: A.** Task 2.\n- **Slice 2: B.** Task 3.\n"
            "- **Slice 3: C.** Task 4. Comes after slices 1 and 2.",
            [
                {"key": "groundwork", "title": "Groundwork", "tasks": [1], "after": []},
                {"key": "1", "title": "A", "tasks": [2], "after": ["groundwork"]},
                {"key": "2", "title": "B", "tasks": [3], "after": ["groundwork"]},
                # Only a slice with no `after` of its own waits for the groundwork:
                # one more link would cost it its early start.
                {"key": "3", "title": "C", "tasks": [4], "after": ["1", "2"]},
            ],
            id="after_with_groundwork",
        ),
    ],
)
def test_a_slices_section_becomes_a_proposals_slices(section, expected):
    split = kl_slices.parse_slices(plan(section))

    assert kl_slices.proposal_slices(split) == expected


@pytest.mark.parametrize(
    "section, tasks, done, reason",
    [
        pytest.param(
            "- **Slice 1: A.** Tasks 1-2.\n- **Slice 2: B.** Tasks 2-3.",
            4,
            (),
            "task 2 is in two slices (1 and 2)",
            id="task_in_two_slices",
        ),
        pytest.param(
            "- **Slice 1: A.** Task 1.\n- **Slice 2: B.** Task 7.",
            4,
            (),
            "slice 2 names task 7, which the plan does not have",
            id="task_the_plan_lacks",
        ),
        pytest.param(
            "- **Slice 1: A.** Task 1.\n- **Slice 2: B.** Task 2.",
            4,
            (2,),
            "slice 2 names task 2, which is tagged [DONE]",
            id="done_task",
        ),
        pytest.param(
            "- **Slice 1: A.** Tasks 1-4.", 4, (), "a split needs at least two slices", id="one"
        ),
        pytest.param(
            "- **Slice 1: A.** Task 1.\n- **Slice 2: B.** Task 2. Depends on slice 1.",
            4,
            (),
            "slice 2 names what it comes after other than as `Comes after slice N.`",
            id="after_in_other_words",
        ),
        pytest.param(
            "- **Slice 1: A.** Task 1.\n- **Slice 2: B.** Task 2. Comes after slice 3.",
            4,
            (),
            "slice 2 comes after slice 3, which is not another slice",
            id="after_no_such_slice",
        ),
    ],
)
def test_a_section_that_does_not_cover_the_plan_once_is_refused(section, tasks, done, reason):
    assert kl_slices.parse_slices(plan(section, tasks, done)) == reason


@pytest.mark.parametrize(
    "slices, reason",
    [
        pytest.param([[1, 2], [3, 4]], None, id="covered"),
        pytest.param([[1], [3, 4]], "task 2 in no slice", id="task_in_no_slice"),
        pytest.param([[1, 2], [3, 4, 5]], "names task 5, which the plan does not have", id="extra"),
    ],
)
def test_coverage_needs_every_task_in_a_slice(slices, reason):
    specs = [{"key": str(i), "tasks": t} for i, t in enumerate(slices)]

    found = kl_slices.coverage_refusal(plan("", 4), specs)

    assert (found is None) if reason is None else (reason in found)


def test_a_done_task_needs_no_slice():
    slices = [{"key": "a", "tasks": [1, 3]}]

    assert kl_slices.coverage_refusal(plan("", 3, done=(2,)), slices) is None


# --- through the real CLI ---------------------------------------------------------


@pytest.fixture
def at_slice_gate(walk, tmp_path):
    """A chain whose newest recorded plan has four tasks and a `## Slices`
    section, blocked at the slice gate. The walk writes its own `plan.md`."""
    path = tmp_path / "sliced-plan.md"
    path.write_text(plan("- **Slice 1: Writers.** Tasks 2-3.\n- **Slice 2: CLI.** Task 4."))
    walk.to_chain_revision(tmp_path)
    walk.run(tmp_path, "produced", "--kind", "plan", "--path", str(path))
    walk.run(tmp_path, "close")
    walk.run(tmp_path, "gate")
    return tmp_path


def proposal(cwd, slices):
    path = cwd / "proposal.json"
    path.write_text(
        json.dumps({"feature_branch": "feature/x", "state_branch": "s", "slices": slices})
    )
    return str(path)


def test_plan_slices_reads_the_chains_plan_and_approve_takes_it(at_slice_gate, kl_cli):
    slices = kl_cli(at_slice_gate, "plan-slices")["slices"]
    path = proposal(at_slice_gate, slices)

    assert [s["key"] for s in slices] == ["groundwork", "1", "2"]
    assert kl_cli(at_slice_gate, "plan-slices", "--check", path)["covered"] is True
    kl_cli(at_slice_gate, "approve", "--slices", path)
    assert kl_cli(at_slice_gate, "slices")["ready"] == ["groundwork"]


@pytest.mark.parametrize(
    "write, named",
    [
        pytest.param(
            lambda p: p.write_text(
                json.dumps({"slices": [{"key": "a", "tasks": [2, 3]}, {"key": "b", "tasks": [4]}]})
            ),
            "task 1 in no slice",
            id="leaves_a_task_out",
        ),
        pytest.param(lambda p: p.write_text("{nope"), "cannot read the slices", id="not_json"),
        pytest.param(lambda p: p.write_text("[]"), "cannot read the slices", id="no_slices"),
    ],
)
def test_check_refuses_a_proposal_that_leaves_a_task_unbuilt(at_slice_gate, write, named, kl_cli):
    path = at_slice_gate / "proposal.json"
    write(path)

    refused = kl_cli(at_slice_gate, "plan-slices", "--check", str(path), ok=False)

    assert named in refused.stderr and "Traceback" not in refused.stderr
