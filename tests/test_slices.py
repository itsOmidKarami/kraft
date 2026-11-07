from __future__ import annotations

import pytest

from kraft.progress import parse_tasks
from kraft.slices import (
    Slice,
    SliceRefusal,
    SliceSplit,
    describe,
    parse_slices,
    plan_for_slice,
    prelude_note,
)

_TASKS = "\n".join(f"### Task {n}: Do {n}\n\n* [ ] step\n" for n in range(1, 7))

_SECTION = """## Slices

- **Slice 1: Parser.** Tasks 1-3.
- **Slice 2: CLI verb.** Tasks 4, 6.
- **Slice 3: Board row.** Task 5.
"""


def _plan(section: str = _SECTION, tasks: str = _TASKS) -> str:
    return f"# Plan\n\n{section}\n---\n\n{tasks}"


def test_a_slices_section_splits_the_plan():
    assert parse_slices(_plan(), max_slices=6) == SliceSplit(
        slices=(
            Slice(1, "Parser", (1, 2, 3)),
            Slice(2, "CLI verb", (4, 6)),
            Slice(3, "Board row", (5,)),
        ),
        prelude=(),
    )


def test_tasks_in_no_slice_are_the_prelude():
    section = _SECTION.replace("Tasks 4, 6.", "Task 4.")
    split = parse_slices(_plan(section), max_slices=6)
    assert isinstance(split, SliceSplit)
    assert split.prelude == (6,)


def test_a_plan_with_no_section_is_none():
    assert parse_slices(_plan(""), max_slices=6) is None


@pytest.mark.parametrize(
    ("section", "word"),
    [
        pytest.param(
            "## Slices\n\n- **Slice 1: A.** Tasks 1-3.\n- **Slice 2: B.** Tasks 3-4.\n",
            "two slices",
            id="task-in-two-slices",
        ),
        pytest.param(
            "## Slices\n\n- **Slice 1: A.** Tasks 1-3.\n- **Slice 2: B.**\n",
            "empty",
            id="empty-slice",
        ),
        pytest.param(
            "## Slices\n\n- **Slice 1: A.** Tasks 1-3.\n- **Slice 2: B.** Task 9.\n",
            "does not have",
            id="unknown-task",
        ),
        pytest.param(
            "## Slices\n\n- **Slice 1: A.** Tasks 1-2.\n- **Slice 2: B.** Task 4.\n",
            "[DONE]",
            id="done-task",
        ),
        pytest.param(
            "## Slices\n\n- **Slice 1: A.** Tasks 1-3.\n"
            "- **Slice 2: B.** Tasks 4-5, comes after slice 1.\n",
            "comes after",
            id="comes-after",
        ),
        pytest.param("## Slices\n\n- **Slice 1: A.** Tasks 1-6.\n", "at least two", id="one-slice"),
        pytest.param(
            "## Slices\n\n" + "".join(f"- **Slice {k}: S.** Task {k}.\n" for k in range(1, 7)),
            "more than",
            id="too-many",
        ),
        pytest.param(
            "## Slices\n\n- **Slice 1: A.** Tasks 1-3.\n- **Slice 3: B.** Tasks 4-5.\n",
            "consecutive",
            id="keys-not-consecutive",
        ),
        pytest.param(
            "## Slices\n\n- **Slice 1: A.** Tasks 1-3.\n- **Slice 2.** Tasks 4-5.\n",
            "no title",
            id="missing-title",
        ),
    ],
)
def test_a_refused_section_names_why(section, word):
    # `done-task` needs a plan whose task 3 is tagged; `too-many` needs the limit lowered.
    tasks = _TASKS.replace("### Task 3: Do 3", "### Task 3: Do 3 [DONE]")
    if word == "[DONE]":
        section = section.replace("Tasks 1-2.", "Task 3.")
    result = parse_slices(
        _plan(section, tasks if word == "[DONE]" else _TASKS),
        max_slices=4 if word == "more than" else 6,
    )
    assert isinstance(result, SliceRefusal)
    assert word in result.reason


def test_a_range_takes_a_hyphen_or_an_en_dash():
    dash = parse_slices(_plan(_SECTION.replace("Tasks 1-3", "Tasks 1–3")), max_slices=6)
    assert dash == parse_slices(_plan(), max_slices=6)


def test_a_slices_heading_inside_a_code_fence_is_ignored():
    fenced = "```\n" + _SECTION + "```\n"
    assert parse_slices(_plan(fenced), max_slices=6) is None


def test_a_slice_plan_tags_every_other_task_done_and_drops_the_section():
    out = plan_for_slice(_plan(), frozenset({4, 6}))
    assert [done for _, done in parse_tasks(out)] == [True, True, True, False, True, False]
    assert "## Slices" not in out
    assert "Parser" not in out
    assert plan_for_slice(out, frozenset({4, 6})) == out


def test_the_prelude_note_names_only_the_prelude_tasks():
    note = prelude_note(SliceSplit(slices=(), prelude=(2, 6)))
    assert "2, 6" in note
    assert "3" not in note


@pytest.mark.parametrize(
    ("plan", "says"),
    [
        pytest.param(_plan(), "Slice 2: CLI verb -- tasks 4, 6", id="split"),
        pytest.param(_plan(_SECTION.replace("Tasks 4, 6.", "Task 4.")), "task 6", id="prelude"),
        pytest.param(_plan(_SECTION.replace("Task 5.", "Task 4.")), "Not used", id="refused"),
    ],
)
def test_describe_says_how_the_plan_gate_reads_the_split(plan, says):
    assert says in describe(plan, max_slices=6)


def test_describe_is_silent_about_a_plan_with_no_section():
    assert describe(_plan(""), max_slices=6) is None
