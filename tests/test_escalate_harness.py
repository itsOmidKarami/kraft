"""Which harness an escalation turn runs on is policy's `escalation_harness`
(Kraft-wge0e): a `harnesses.yaml` profile, or `item` for the one the item's
own work ran on. Split from test_escalate.py for its line budget; the same
seam -- `_agent.run_agent_task` faked, a log written for the reader."""

from __future__ import annotations

import json

import pytest
from support.harness import v1_resolved, write_harness_profiles

from kraft import escalate, events, executor, store
from kraft.policy import InstancePolicy, InstancePolicyInput
from kraft.templates.environment import WorkItemTarget

#: One agent task on `cx`, a profile on the real codex provider.
_NODES = [
    {
        "id": "implementation",
        "kind": "exec",
        "tasks": [{"id": "work", "kind": "agent", "harness": "cx", "prompt": "Build it."}],
    }
]
_WORK = "implementation.main.work"
LAUNCH = executor.LaunchContext(repo_entry=None, skills_dir=None)


@pytest.fixture(autouse=True)
def profiles(tmp_path, monkeypatch):
    templates = tmp_path / "templates"
    write_harness_profiles(
        templates,
        {
            "claude": {"provider": "claude"},
            "cx": {"provider": "codex"},
            "cx-backup": {"provider": "codex"},
        },
    )
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(templates))


@pytest.fixture
def launched(monkeypatch):
    """Every escalation launch's kwargs. Each writes a codex log naming thread
    `thread-1`, which only codex's reader reads."""
    seen: list[dict] = []

    async def fake(db, run_dirs, *, session_id, **kw):
        seen.append(kw)
        log = run_dirs.logs / f"{session_id}.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(json.dumps({"type": "thread.started", "thread_id": "thread-1"}) + "\n")
        return "done"

    monkeypatch.setattr("kraft.escalate._agent.run_agent_task", fake)
    return seen


async def _item(database, run_dirs, defaults=None, item_policy=None, nodes=_NODES):
    chain = v1_resolved(nodes).materialize(
        target=WorkItemTarget.for_repository("target"),
        effective_policy=InstancePolicy.from_input(
            InstancePolicyInput.model_validate({"defaults": defaults or {}})
        ),
    )
    if item_policy is not None:
        chain.with_item_policy(item_policy)  # the check every door makes
    await database.write(
        lambda c: store.create_work_item(
            c,
            id="w1",
            bead_id=None,
            title="t",
            description="",
            repo=str(run_dirs.base),
            chain_template="t",
            chain_definition="{}",
            materialized_chain=chain.to_json(),
            policy_override=item_policy,
        )
    )
    await database.write(lambda c: store.enter_node(c, "w1", "implementation"))
    await database.write(lambda c: store.mark_needs_human(c, "w1", "implementation", "stuck"))


async def _ran(database, run_dirs, sid, hook_point):
    await database.write(
        lambda c: store.create_session(
            c,
            id=sid,
            work_item_id="w1",
            node_id="implementation",
            hook_point=hook_point,
            log_path=str(run_dirs.logs / f"{sid}.log"),
            result_path=str(run_dirs.results / f"{sid}.json"),
        )
    )


async def _escalate(database, run_dirs):
    return await escalate.dispatch(
        database, run_dirs, work_item_id="w1", message="help", launch=LAUNCH
    )


async def test_an_unset_escalation_harness_is_claude(database, run_dirs, launched):
    """The default changes nothing: an install that names no harness
    escalates on `claude`, as it always has."""
    await _item(database, run_dirs)

    await _escalate(database, run_dirs)

    assert (launched[0]["harness"], launched[0]["harness_id"]) == ("claude", "claude")


@pytest.mark.parametrize(
    ("defaults", "grants"),
    [
        (None, ("git-commit", "git-rebase", "git-push")),
        ({"escalation_grants": ["git-commit"]}, ("git-commit",)),
        ({"escalation_grants": []}, ()),
    ],
    ids=["default", "narrowed", "none"],
)
async def test_an_escalation_holds_policy_yaml_s_escalation_grants(
    database, run_dirs, launched, defaults, grants
):
    """Kraft-4in7z: an escalation has to rebase and push, so unless
    `defaults.escalation_grants` says otherwise its launch holds all three."""
    await _item(database, run_dirs, defaults)

    await _escalate(database, run_dirs)

    assert launched[0]["grants"] == grants


@pytest.mark.parametrize(
    ("defaults", "item_policy"),
    [({"escalation_harness": "cx"}, None), (None, {"escalation_harness": "cx"})],
    ids=["policy-yaml", "item-override"],
)
async def test_an_escalation_runs_on_the_policy_s_harness_and_keeps_its_thread(
    database, run_dirs, launched, defaults, item_policy
):
    """`policy.yaml`'s `defaults.escalation_harness`, or the item's own
    override of it, picks the profile. The thread id is read with that
    harness's reader, so the next turn can resume a codex thread."""
    await _item(database, run_dirs, defaults, item_policy)

    await _escalate(database, run_dirs)

    assert (launched[0]["harness"], launched[0]["harness_id"]) == ("codex", "cx")
    row = database.read(lambda c: c.execute("SELECT * FROM work_items WHERE id = 'w1'").fetchone())
    assert row["escalation_session_id"] == "thread-1"


@pytest.mark.parametrize("switched", [False, True], ids=["declared", "fallback"])
async def test_item_follows_the_harness_the_item_s_work_last_ran_on(
    database, run_dirs, launched, switched
):
    """`escalation_harness: item` runs on the profile the item's latest agent
    session ran on: its task's own, or the one a fallback switched it to.
    An earlier escalation turn is not the item's work."""
    await _item(database, run_dirs, {"escalation_harness": "item"})
    await _ran(database, run_dirs, "s1", _WORK)
    await _ran(database, run_dirs, "s2", "escalation")
    if switched:
        payload = {"task": _WORK, "session_id": "s1", "to": {"harness": "cx-backup"}}
        await database.write(lambda c: events.append(c, "w1", "launch_fallback", payload))

    await _escalate(database, run_dirs)

    assert launched[0]["harness_id"] == ("cx-backup" if switched else "cx")


async def test_item_with_no_agent_work_yet_launches_nothing(database, run_dirs, launched):
    """Nothing ran to follow: the turn stops as a config error saying so,
    rather than guessing a harness."""
    await _item(database, run_dirs, {"escalation_harness": "item"})

    status = await _escalate(database, run_dirs)

    assert status == "config_error"
    assert launched == []
    [log] = database.read(
        lambda c: c.execute(
            "SELECT log_path FROM worker_sessions WHERE hook_point = 'escalation'"
        ).fetchall()
    )
    assert "no agent task" in open(log["log_path"]).read()


async def test_item_skips_gate_reviews_and_escalation_turns(database, run_dirs, launched):
    """A gate's reviewer and an escalation turn ran later than the item's own
    agent task, on other profiles; neither is the item's work, so `item`
    still follows the task's profile."""
    gate = {
        "id": "review",
        "kind": "gate",
        "auto_review": {"id": "rev", "kind": "agent", "harness": "cx-backup", "prompt": "Review."},
    }
    await _item(database, run_dirs, {"escalation_harness": "item"}, nodes=[*_NODES, gate])
    await _ran(database, run_dirs, "s1", _WORK)
    await _ran(database, run_dirs, "s2", "review.auto_review")
    await _ran(database, run_dirs, "s3", "escalation")

    await _escalate(database, run_dirs)

    assert launched[0]["harness_id"] == "cx"
