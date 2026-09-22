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

from support.harness import entry_of, fake_harness_home, write_agent_profiles

from kraft import events, executor, gate_review
from kraft.adapters import agent as agent_mod
from kraft.api.routes import artifacts as artifacts_route
from kraft.api.routes import gates as gates_route
from kraft.executor import gates as gates_module
from kraft.executor.context import LaunchContext
from kraft.templates import revision
from kraft.templates.library import TemplateLibrary

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
        db=it.database, run_dirs=it.run_dirs, indexer=_Indexer(), library=library
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
        (it.run_dirs.results / f"{kw['session_id']}.json").write_text(
            json.dumps({"status": "done", "verdict": "approve", "concerns": "looks right"})
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


async def test_auto_review_refuses_a_profiles_own_fallback_list(item_on, tmp_path, monkeypatch):
    reviewer = _profile_reviewer("deep")
    it = await item_on(
        [
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
        ],
        auto_gate=True,
    )
    fake_harness_home(tmp_path, ["true"])
    write_agent_profiles(
        Path(os.environ["KRAFT_HOME"]) / "templates",
        {"deep": {"model": {"fake": "opus"}, "fallback": [{"model": "haiku"}]}},
    )
    launched = []

    async def _capture(_db, _rd, **kw):
        launched.append(kw)
        (it.run_dirs.results / f"{kw['session_id']}.json").write_text(
            json.dumps({"status": "done", "verdict": "approve", "concerns": "fine"})
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

    assert launched == [], (
        "the reviewer launched despite its profile's fallback list -- "
        "gate_review.review has no loop to walk it, so it must never launch "
        "a candidate it cannot fall back from"
    )
    assert verdict == "undecided"
    assert "deep" in note
