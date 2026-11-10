"""`approve --slices` holds a slicing to the plan on disk: every plan task in a
slice, and no slice naming a task the plan lacks."""

from __future__ import annotations

import pytest


@pytest.mark.parametrize(
    "plan_text, slices, named",
    [
        pytest.param(
            None,
            [{"key": "a", "title": "A", "tasks": [2, 3, 4, 5]}],
            "task 1 in no slice",
            id="leaves_a_task_out",
        ),
        pytest.param(
            None,
            [{"key": "a", "title": "A", "tasks": [1, 2, 3, 4, 5, 6]}],
            "task 6, which the plan does not have",
            id="names_a_task_the_plan_lacks",
        ),
        pytest.param(
            "# The plan\n",
            [{"key": "a", "title": "A", "tasks": [1]}],
            "does not have",
            id="plan_has_no_task_headings",
        ),
        pytest.param(
            False,
            [{"key": "a", "title": "A", "tasks": [1, 2, 3, 4, 5]}],
            "no plan on disk",
            id="plan_file_gone",
        ),
    ],
)
def test_approve_refuses_a_slicing_that_does_not_cover_the_plan(
    walk, kl_cli, tmp_path, plan_text, slices, named
):
    walk.to_slice_gate(tmp_path)
    if plan_text is False:
        (tmp_path / "plan.md").unlink()
    elif plan_text:
        (tmp_path / "plan.md").write_text(plan_text)
    path = walk.write_plan(tmp_path, walk.plan(slices=slices))

    refused = kl_cli(tmp_path, "approve", "--slices", str(path), ok=False)

    assert named in refused.stderr and "Traceback" not in refused.stderr
    assert kl_cli(tmp_path, "state")["status"] == "blocked"
    assert kl_cli(tmp_path, "slices")["slices"] == []
