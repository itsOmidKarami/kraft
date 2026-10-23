"""`mr.automated_review` through the executor against a `FakeForge`: which
reviewers reach the task (`dispatch._reviewers`), and where a task may name
them. What each reviewer's read returns is test_automated_review.py."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from support.harness import entry_of, v1_resolved

from kraft import executor, store
from kraft.adapters import forge
from kraft.automated_review import as_reviewers

from .nodes import forge_node

_BOT = {"bot": "coderabbitai"}
_TASK_BOT = {"bot": "task-bot"}
_ITEM_CHECK = {"check": "item-check"}


@pytest.mark.parametrize(
    "item, in_repo, task, asked",
    [
        (None, _BOT, None, [_BOT]),
        (None, [_BOT, {"check": "scan"}], None, [_BOT, {"check": "scan"}]),
        (None, None, [_TASK_BOT], [_TASK_BOT]),
        (None, _BOT, [_TASK_BOT], [_BOT]),
        # `[]` is a layer saying "none", not an unset one.
        (None, [], [_TASK_BOT], []),
        ([_ITEM_CHECK], _BOT, [_TASK_BOT], [_ITEM_CHECK]),
        (None, None, None, []),
    ],
    ids=[
        "repo",
        "repo-list",
        "task",
        "repo-over-task",
        "repo-says-none",
        "item-over-repo-and-task",
        "none-anywhere",
    ],
)
async def test_the_nearest_layer_s_reviewers_reach_the_review_task(
    item_on, database, run_dirs, monkeypatch, item, in_repo, task, asked
):
    """Ruling 171: which reviewers is the item's node override's, else the
    repository's (`repos.yaml`), else the review task's own -- handed to the
    backend at dispatch."""
    fake = forge.FakeForge()
    monkeypatch.setattr(forge.run, "resolve", lambda name: fake)
    node = forge_node(
        "review", "mr.automated_review", **({} if task is None else {"reviewers": task})
    )
    it = await item_on([node])
    if item is not None:
        await database.write(
            lambda c: store.set_node_overrides(c, it.id, {"review": {"reviewers": item}})
        )
    entry = entry_of(
        {
            "setup_command": "",
            "forge": "github",
            **({} if in_repo is None else {"automated_review": in_repo}),
        }
    )

    await executor.run(
        database,
        run_dirs,
        work_item_id=it.id,
        launch=executor.LaunchContext(repo_entry=entry),
    )

    assert fake.reviewers_asked == [as_reviewers(asked)]


@pytest.mark.parametrize(
    "target, reviewers, refused",
    [
        ("mr.automated_review", [{"bot": "b"}], False),
        ("mr.ci", [{"bot": "b"}], True),
        ("mr.automated_review", [{"login": "b"}], True),
    ],
    ids=["the-review", "another-wait", "malformed-entry"],
)
def test_only_the_review_task_names_reviewers(target, reviewers, refused):
    """The chain may name *which* reviewers, on the one task that waits for
    them; any other forge task naming them, or a malformed entry, is refused at
    load."""
    task = {"id": "t", "kind": "forge", "target": target, "reviewers": reviewers}
    nodes = [{"id": "feedback", "kind": "exec", "tasks": [task]}]
    if refused:
        with pytest.raises(ValidationError):
            v1_resolved(nodes)
    else:
        v1_resolved(nodes)
