"""Two follow-ups to gate auto-review, both about `GateNode.auto_review`'s
own contract:

* Kraft-rndd1 -- an agent's `approve` verdict must bind to a digest of
  exactly the artifact it read, the way a person's approval already binds to
  a digest of what `GET .../artifact` rendered for them
  (`kraft.templates.revision`, Kraft-ze1yj). `gate_review.review` reads the
  artifact straight from the worktree and never recorded a digest of what it
  read, so its approval bound to nothing: whatever was on disk at approval
  time, not what it judged.
* Kraft-t4y8g -- `GateNode._no_handler` refuses a gate whose `auto_review`
  task declares its own `fallback:` list, because `gate_review.review`
  launches its reviewer once and never runs the fallback loop
  (`kraft.executor.fallback`, `dispatch.dispatch_node`'s alone). It does not
  refuse a task whose `profile:` names an agent profile that itself carries
  one -- profiles are read live from `harnesses.yaml`
  (`kraft.adapters.profiles`), not at template load, so that list was
  silently ignored and a rate-limited review just went undecided.
"""

from __future__ import annotations

import functools
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from support import worktree as wtree
from support.addons import drop_store, install_release, pinned_chain
from support.harness import (
    commit_all,
    entry_of,
    fake_harness_home,
    git,
    write_agent_profiles,
    write_own_result,
)

from kraft import events, executor, gate_review, store
from kraft.adapters import agent as agent_mod
from kraft.api.routes import artifacts as artifacts_route
from kraft.api.routes import gates as gates_route
from kraft.executor import gates as gates_module
from kraft.executor.context import LaunchContext
from kraft.templates import revision
from kraft.templates.library import TemplateLibrary

from .test_gates import _requested, _resolved, _review, _reviewed

GATE = "revision_approval"

LIBRARY = TemplateLibrary.from_mappings(
    {
        "tasks": {"check": {"kind": "subprocess", "command": "true"}},
        "nodes": {"extra_check": {"kind": "exec", "tasks": [{"id": "run", "extends": "check"}]}},
    },
    (),
    library_path=Path("library.yaml"),
)

#: A wired launch (`deps.launch`'s own shape): the reviewer's approval binds
#: to `revision.artifact_digest` over `LIBRARY`, the same as production binds
#: it to `st.library`.
REVISION_LAUNCH = LaunchContext(
    repo_entry=entry_of({"setup_command": ""}),
    chain_revision_digest=functools.partial(revision.artifact_digest, library=LIBRARY),
)

REVIEWER = {"id": "reviewer", "kind": "agent", "harness": "fake", "prompt": "review it"}


def _run(id: str) -> dict:
    return {
        "id": id,
        "kind": "exec",
        "tasks": [{"id": "run", "kind": "subprocess", "command": "true"}],
    }


def _artifact_body(change: dict) -> str:
    return f"---\nwork_item_ids: [w1]\n---\n```json\n{json.dumps(change)}\n```\n"


def _revision_chain(tmp_path: Path, change: dict, *, auto_review: dict | None) -> list[dict]:
    """revise (writes `change` as its chain_revision) -> revision_approval ->
    build -> brief. A twin of `tests/executor/test_chain_revision.py`'s
    `_chain`, with an optional `auto_review` on the gate."""
    source = tmp_path / "proposal.md"
    source.write_text(_artifact_body(change))
    write = (
        "sh -c 'mkdir -p .engineering/chain_revisions && "
        f"cp {source} .engineering/chain_revisions/w1.md'"
    )
    gate = {"id": GATE, "kind": "gate", "artifact": "chain_revision", "reject_to": "revise"}
    if auto_review is not None:
        gate["auto_review"] = auto_review
    return [
        {
            "id": "revise",
            "kind": "exec",
            "tasks": [{"id": "write", "kind": "subprocess", "command": write}],
        },
        gate,
        _run("build"),
        _run("brief"),
    ]


#: Skips `brief`, adds `checked` -- a real change, so an approval that applies
#: it is observable on the timeline (`chain_revised`).
PROPOSAL = {
    "rationale": "the plan needs a second check and no brief",
    "skip": [{"node": "brief", "evidence": "plan: docs only"}],
    "add": [
        {"after": "build", "node": {"id": "checked", "extends": "extra_check"}, "evidence": "plan"}
    ],
}

#: What the artifact is rewritten to after the reviewer has (notionally) read
#: `PROPOSAL` -- a different, and also non-empty, change set.
MUTATED = {
    "rationale": "actually the chain is fine as it stands, add a different check",
    "add": [
        {"after": "build", "node": {"id": "checked2", "extends": "extra_check"}, "evidence": "x"}
    ],
}


def _artifact_file(it) -> Path:
    return it.run_dirs.worktrees / it.id / ".engineering" / "chain_revisions" / "w1.md"


def _state(it, library=LIBRARY):
    class _Indexer:
        async def ingest_gate_artifact(self, **kw):
            pass

    return SimpleNamespace(
        db=it.database,
        run_dirs=it.run_dirs,
        indexer=_Indexer(),
        library=library,
        # No repos.yaml: the item's repository has no id, so no repo library.
        templates_dir=it.run_dirs.worktrees.parent / "config",
    )


async def _walk(it, launch):
    return await executor.run_once(it.database, it.run_dirs, work_item_id=it.id, launch=launch)


def _approving_agent(it, monkeypatch, *, on_dispatch=None):
    """The mocked reviewer launch: writes an `approve` verdict, and runs
    `on_dispatch(it)` first -- the mutation/render each race test stages
    while the reviewer is "working"."""

    async def _capture(_db, _rd, **kw):
        if on_dispatch is not None:
            await on_dispatch()
        write_own_result(
            it.run_dirs,
            kw["work_item_id"],
            kw["session_id"],
            {"status": "done", "verdict": "approve", "concerns": "looks right"},
        )
        return "done"

    monkeypatch.setattr(agent_mod, "run_agent_task", _capture)


async def _review_and_approve(it, launch):
    """`review_gates`, with `on_approve` wired to the real approval door --
    the same callback `deps._on_approve` binds in production."""
    return await gates_module.review_gates(
        "awaiting_gate",
        it.database,
        it.run_dirs,
        work_item_id=it.id,
        launch=launch,
        on_approve=lambda row, gate, **kw: gates_route.apply_approval(_state(it), row, gate, **kw),
    )


# -- Kraft-rndd1 --------------------------------------------------------------


async def test_agent_approval_of_an_unedited_artifact_still_applies(item_on, tmp_path, monkeypatch):
    """The positive case the two refusal tests below need alongside them:
    when nothing touches the artifact between dispatch and approval, the
    digest `review_gates` captures matches what `apply_approval` recomputes,
    and the agent's approval applies exactly as it would have before this
    fix -- the new check is a real digest comparison, not a check that just
    happens to always refuse."""
    it = await item_on(_revision_chain(tmp_path, PROPOSAL, auto_review=REVIEWER), auto_gate=True)
    launch = REVISION_LAUNCH
    fake_harness_home(tmp_path, ["true"])
    _approving_agent(it, monkeypatch)

    status = await _walk(it, launch)
    assert status == "awaiting_gate"

    await _review_and_approve(it, launch)

    [event] = it.events("chain_revised")
    assert event["payload"]["gate"] == GATE


async def test_agent_approval_refuses_an_artifact_edited_after_its_read(
    item_on, tmp_path, monkeypatch
):
    """Nobody has ever rendered this gate (`store.shown_revision` is None), so
    without the fix `apply_approval`'s agent-path check is skipped entirely
    and whatever is on disk at approval time is applied -- not what the
    reviewer actually read. The reviewer here verdicts `approve` while
    "reading" `PROPOSAL`; before that verdict is applied the artifact is
    rewritten to `MUTATED`. `review_gates` now captures its own digest right
    before dispatching the reviewer, so the mismatch is caught."""
    it = await item_on(_revision_chain(tmp_path, PROPOSAL, auto_review=REVIEWER), auto_gate=True)
    launch = REVISION_LAUNCH
    fake_harness_home(tmp_path, ["true"])

    async def _edit():
        _artifact_file(it).write_text(_artifact_body(MUTATED))

    _approving_agent(it, monkeypatch, on_dispatch=_edit)

    status = await _walk(it, launch)
    assert status == "awaiting_gate"

    await _review_and_approve(it, launch)

    assert it.events("chain_revised") == [], (
        "the agent's approval applied the artifact as it stood at approval "
        "time, not what it actually read -- it was never bound to a digest "
        "of its own read, the way a person's approval is bound to theirs"
    )


async def test_agent_approval_refuses_even_when_a_person_renders_the_new_version(
    item_on, tmp_path, monkeypatch
):
    """The second half of the race: a person's render of the (already
    mutated) artifact writes `store.shown_revision`, and without the fix
    `apply_approval`'s non-viewer path reads whatever was *last* shown,
    unconditionally -- satisfied by it, so the agent's approval ends up bound
    to the person's view, not to what the agent itself judged, even though
    the agent never looked at the mutated version at all. `review_gates` now
    supplies its own `seen`, which `apply_approval` honours over the store's
    the moment it is not None -- the store lookup is a fallback for a caller
    that has nothing of its own, not an override."""
    it = await item_on(_revision_chain(tmp_path, PROPOSAL, auto_review=REVIEWER), auto_gate=True)
    launch = REVISION_LAUNCH
    fake_harness_home(tmp_path, ["true"])

    async def _edit_then_render():
        _artifact_file(it).write_text(_artifact_body(MUTATED))
        request = SimpleNamespace(app=SimpleNamespace(state=_state(it)))
        await artifacts_route.get_work_item_artifact(it.id, request)

    _approving_agent(it, monkeypatch, on_dispatch=_edit_then_render)

    status = await _walk(it, launch)
    assert status == "awaiting_gate"

    await _review_and_approve(it, launch)

    assert it.events("chain_revised") == [], (
        "a person's render of the mutated artifact satisfied the agent's "
        "approval check by coincidence, not because the agent verified "
        "anything -- 'last shown' is not 'what this approver read'"
    )


# -- Kraft-t4y8g --------------------------------------------------------------


def _profile_reviewer(profile: str) -> dict:
    return {
        "id": "reviewer",
        "kind": "agent",
        "harness": "fake",
        "prompt": "review it",
        "profile": profile,
    }


def _spec_gate_chain(reviewer: dict) -> list[dict]:
    return [
        {
            "id": "spec",
            "kind": "exec",
            "tasks": [{"id": "write", "kind": "subprocess", "command": "true"}],
        },
        {
            "id": "spec_approval",
            "kind": "gate",
            "artifact": "spec",
            "reject_to": "spec",
            "auto_review": reviewer,
        },
        _run("implementation"),
    ]


async def _review_spec_gate(it, monkeypatch):
    """`gate_review.review` of `spec_approval` with the reviewer's launch
    captured: `(verdict, note, launches)`."""
    launched = []

    async def _capture(_db, _rd, **kw):
        launched.append(kw)
        write_own_result(
            it.run_dirs,
            kw["work_item_id"],
            kw["session_id"],
            {"status": "done", "verdict": "approve", "concerns": "fine"},
        )
        return "done"

    monkeypatch.setattr(agent_mod, "run_agent_task", _capture)
    await it.database.write(
        lambda c: events.append(
            c, it.id, "gate_requested", {"gate": "spec_approval", "node_id": "spec_approval"}
        )
    )
    verdict, note = await gate_review.review(
        it.database,
        it.run_dirs,
        work_item_id=it.id,
        gate="spec_approval",
        node=it.chain.chain.nodes[1],
        launch=LaunchContext(repo_entry=entry_of({"setup_command": ""})),
    )
    return verdict, note, launched


async def test_auto_review_refuses_a_profiles_own_fallback_list(item_on, tmp_path, monkeypatch):
    it = await item_on(_spec_gate_chain(_profile_reviewer("deep")), auto_gate=True)
    fake_harness_home(tmp_path, ["true"])
    write_agent_profiles(
        Path(os.environ["KRAFT_HOME"]) / "config",
        {"deep": {"model": {"fake": "opus"}, "fallback": [{"model": "haiku"}]}},
    )

    verdict, note, launched = await _review_spec_gate(it, monkeypatch)

    assert launched == [], (
        "the reviewer launched despite its profile's fallback list -- "
        "gate_review.review has no loop to walk it, so it must never launch "
        "a candidate it cannot fall back from"
    )
    assert verdict == "undecided"
    assert "deep" in note


async def test_the_profile_fallback_check_reads_restored_pins(item_on, repo, tmp_path, monkeypatch):
    """The item pinned an add-on whose profile carries the list, and its store
    is gone: the check restores it first, or it reads a table without it and
    lets a reviewer launch that has no loop to walk the list."""
    fake_harness_home(tmp_path, ["true"])
    pins, store = install_release(tmp_path)
    drop_store(store)
    chain = pinned_chain(_spec_gate_chain(_profile_reviewer("release:deep")), pins, repo=repo)
    it = await item_on(chain, auto_gate=True)

    verdict, note, launched = await _review_spec_gate(it, monkeypatch)

    assert (verdict, launched) == ("undecided", [])
    assert "release:deep" in note
    assert [e["payload"]["reason"] for e in it.events("gate_auto_review_skipped")] == [
        "profile_fallback"
    ]
    assert store.is_dir()  # restored before the table was read


async def test_a_retried_items_run_chain_pins_decide_the_profile_fallback_refusal(
    item_on, repo, tmp_path, monkeypatch
):
    """A retry runs on `run_chain`: its pins are the version the reviewer
    launches on, so a list the older `materialized_chain` pin lacks still refuses."""
    fake_harness_home(tmp_path, ["true"])
    old, _ = install_release(tmp_path, "1.0.0", fallback=False)
    new, _ = install_release(tmp_path, "1.1.0", fallback=True)
    nodes = _spec_gate_chain(_profile_reviewer("release:deep"))
    it = await item_on(pinned_chain(nodes, old, repo=repo), auto_gate=True)
    retried = pinned_chain(nodes, new, repo=repo).to_json()
    await it.database.write(
        lambda c: c.execute("UPDATE work_items SET run_chain = ? WHERE id = ?", (retried, it.id))
    )

    verdict, note, launched = await _review_spec_gate(it, monkeypatch)

    assert (verdict, launched) == ("undecided", [])
    assert [e["payload"]["reason"] for e in it.events("gate_auto_review_skipped")] == [
        "profile_fallback"
    ]


# -- Review flow: must-fix outranks an agent's approve --------------------


async def test_a_published_must_fix_thread_downgrades_an_approve_to_undecided(
    item_on, tmp_path, monkeypatch
):
    """An agent's `approve` verdict must not clear a gate a person has left an
    unresolved must-fix thread on (review flow §3)."""

    it = await item_on(_revision_chain(tmp_path, PROPOSAL, auto_review=REVIEWER), auto_gate=True)
    launch = REVISION_LAUNCH
    fake_harness_home(tmp_path, ["true"])
    _approving_agent(it, monkeypatch)

    status = await _walk(it, launch)
    assert status == "awaiting_gate"

    await it.database.write(
        lambda c: store.create_thread(
            c, wid=it.id, gate=GATE, anchor_sha="h", body="x", label="must_fix"
        )
    )
    await it.database.write(
        lambda c: store.submit_review(
            c, wid=it.id, gate=GATE, outcome="comment", summary=None, head_sha="h", base_sha="h"
        )
    )

    await _review_and_approve(it, launch)

    assert it.status() == "needs_human"
    assert it.events("chain_revised") == []
    [skipped] = it.events("gate_auto_review_skipped")
    assert skipped["payload"]["reason"] == "undecided"
    # The approval's own note ("looks right") is not why it went to a person.
    assert "note" not in skipped["payload"]


async def test_a_review_that_refused_to_launch_says_why_in_its_skipped_event(
    item_on, tmp_path, monkeypatch
):
    """A gate review that never ran (here a model 1.4 stored that is no model
    id) leaves the gate to a person. The event the board and `kraft view
    events` show must say why, not only `undecided`."""

    it = await item_on(_revision_chain(tmp_path, PROPOSAL, auto_review=REVIEWER), auto_gate=True)
    launch = REVISION_LAUNCH
    fake_harness_home(tmp_path, ["true"])
    _approving_agent(it, monkeypatch)
    assert await _walk(it, launch) == "awaiting_gate"
    await it.database.write(
        lambda c: store.set_agent_overrides(c, it.id, json.dumps({"model": "sonnet 4"}))
    )

    await _review_and_approve(it, launch)

    assert it.events("chain_revised") == []
    [skipped] = it.events("gate_auto_review_skipped")
    assert skipped["payload"]["reason"] == "undecided"
    assert f"kraft item set-overrides {it.id} --clear" in skipped["payload"]["note"]


async def test_review_gates_stops_config_when_on_approve_refuses(item_on, monkeypatch):
    """`review_gates`' approve branch, when `on_approve` itself reports the
    approval could not apply (`approved is None`) -- the gate route's own
    refusal path (`kraft.api.routes.gates.apply_approval`), reached here
    through the escalation/auto-review door instead of a person's click."""
    it = await item_on(_reviewed(), auto_gate=True)
    monkeypatch.setattr(
        gates_module.gate_review, "review", lambda *a, **kw: _resolved(("approve", None))
    )

    async def _on_approve(row, gate, **_kw):
        return None, "the artifact changed since the review ran"

    await _requested(it)
    result = await _review(it, on_approve=_on_approve)

    assert result == "needs_human"
    assert (it.status(), it.row()["stop_kind"]) == ("needs_human", "config")


@pytest.mark.parametrize("commits", [False, True], ids=["unmoved", "the-reviewer-commits"])
async def test_an_agents_approval_of_the_final_gate_pins_the_head(
    item_on, repo, monkeypatch, commits
):
    """A final gate an agent reviews and approves passes the head it read, as
    a person's approval does: what the merge may push (`kraft.reviewed_head`).
    A head that moved while it worked -- its own commit -- is not what it
    read, so the approval goes to a person instead of pinning it."""
    nodes = _reviewed()
    nodes[1]["chain_finalized"] = True
    it = await item_on(nodes, auto_gate=True)
    await wtree.ensure(it.database, it.run_dirs, repo)
    head = git(it.worktree, "rev-parse", f"refs/heads/{store.branch_for(it.row())}")

    async def _reviewer(*_a, tips, **_kw):
        # What it reads, recorded on its session for a person's approval that
        # stops it (`reviewed_head.moved_under`).
        assert tips == {str(it.worktree): head}
        if commits:
            (it.worktree / "reviewer.txt").write_text("the reviewer's own edit\n")
            commit_all(it.worktree, "the reviewer's own edit")
        return "approve", None

    monkeypatch.setattr(gates_module.gate_review, "review", _reviewer)

    async def _on_approve(row, gate, **_kw):
        return it.chain.chain.nodes, None

    await _requested(it)
    await _review(it, on_approve=_on_approve)

    if commits:
        assert it.events("gate_approved") == []
        [skipped] = it.events("gate_auto_review_skipped")
        assert skipped["payload"]["reason"] == "undecided"
        assert "branch moved" in skipped["payload"]["note"]
        return
    [approved] = it.events("gate_approved")
    assert approved["payload"] == {
        "gate": "spec_approval",
        "by": "agent",
        "tips": {str(it.worktree): head},
    }


async def test_the_reviewer_runs_where_its_gate_is_placed(item_on, tmp_path, monkeypatch):
    nodes = _spec_gate_chain(_profile_reviewer("deep"))
    nodes[1]["runner"] = "gpu"  # the gate
    it = await item_on(nodes, auto_gate=True)
    fake_harness_home(tmp_path, ["true"])
    write_agent_profiles(
        Path(os.environ["KRAFT_HOME"]) / "config", {"deep": {"model": {"fake": "opus"}}}
    )

    _, _, launched = await _review_spec_gate(it, monkeypatch)

    assert [kw["runner"] for kw in launched] == ["gpu"]
