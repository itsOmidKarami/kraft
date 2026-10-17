"""Auto-intake and the checkout lease: a pickup never starts in a busy checkout."""

from __future__ import annotations

import yaml
from support.harness import v1_named_chain
from test_intake_poller import _ready, _state, _work_items

from kraft import events
from kraft import intake as intake_mod
from kraft.templates.environment import WorkItemTarget


async def test_a_pickup_behind_a_working_in_place_item_is_queued_not_started(
    tmp_path, monkeypatch, stub_app, item_on, run_dirs
):
    monkeypatch.setattr(
        intake_mod.beads, "ready", _ready([{"id": "B-1", "title": "t", "priority": 3}])
    )
    app = stub_app(**_state(tmp_path, repo_entry={"worktrees": False}))
    repo = yaml.safe_load((app.state.templates_dir / "repos.yaml").read_text())["repos"][0]
    in_place = WorkItemTarget.for_repository("sample", in_place=True)
    await item_on(v1_named_chain(run_dirs.base / "t"), wid="a", repo=repo["path"], target=in_place)

    (wid,) = await intake_mod.tick(app)

    assert wid not in app.state.tasks, "a walk was started in a busy checkout"
    assert {r["id"]: r["status"] for r in _work_items(app)}[wid] == "queued"
    [queued] = [
        e["payload"]
        for e in app.state.db.read(lambda c: events.read_after(c, 0, wid))
        if e["type"] == "work_item_queued"
    ]
    assert queued["held_by"] == "a"
