"""What a fix-loop repair's outcome spends (Kraft-jdkoq): only a genuine
repair attempt costs one of the loop's attempts. A repair that never ran,
or whose own plumbing failed, stops under its own named cause instead of
spending a cycle and stopping as "stuck". And after a `/retry`, a repair is
dispatched again rather than reused from before it (Kraft-znsvg)."""

import json
import shlex
import sys

import pytest
from support.harness import entry_of, git, v1_node

from kraft import events, executor, store
from kraft import policy as _policy
from kraft.executor import prompts, walk
from kraft.vocab import GateEvent

NO_SETUP = entry_of({"setup_command": ""})
KEY = "build.fix_loop"


def _sub(task_id, argv=("true",)):
    return {"id": task_id, "kind": "subprocess", "command": shlex.join(argv)}


def _fails_until(marker, passes_on: int):
    """A command that fails until it has run `passes_on` times."""
    code = (
        f"import pathlib,sys; m=pathlib.Path({str(marker)!r}); "
        "n=int(m.read_text()) if m.exists() else 0; m.write_text(str(n+1)); "
        f"sys.exit(0 if n+1 >= {passes_on} else 1)"
    )
    return [sys.executable, "-c", code]


def _node(check, fix_steps, attempts=3):
    """A chain of one node, `build`: `check`, then a fix loop of `fix_steps`."""
    return [v1_node(tasks=[check], fix_loop={"steps": fix_steps, "max_attempts": attempts})]


def _policy_():
    return _policy.Policy(loops={}, default=_policy.Cap(9, 3600))


def _walk_node(it):
    return walk.walk_node(
        it.database,
        it.run_dirs,
        it.id,
        it.chain.chain.nodes[0],
        it.row(),
        it.repo,
        policy=_policy_(),
        launch=executor.LaunchContext(repo_entry=NO_SETUP),
    )


def _counter(it):
    return it.database.read(lambda c: store.read_counter(c, it.id, KEY))


def _reasons(it):
    return [e["payload"]["reason"] for e in it.events("work_item_needs_human")]


@pytest.mark.parametrize(
    ("outcome", "result", "status"),
    [
        ("rate_limited", "rate_limited", "rate_limited"),
        ("waiting", "waiting", "waiting"),
        ("infra_stop", "needs_human", "needs_human"),
        ("config_error", "needs_human", "needs_human"),
        ("paused", "paused", "active"),
    ],
)
async def test_a_repair_that_never_ran_spends_no_attempt_and_names_its_own_cause(
    item_on, script, outcome, result, status
):
    script.real = {"check"}
    script.plan = {"fix": [outcome]}
    it = await item_on(_node(_sub("check", ["false"]), [{"id": "repair", "tasks": [_sub("fix")]}]))

    assert await _walk_node(it) == result
    assert it.status() == status
    assert _counter(it) is None, "the attempt the repair never made was spent"
    assert [e["payload"]["outcome"] for e in it.events("fix_cycle_refunded")] == [outcome]
    assert not any("stuck" in r or "exhausted" in r for r in _reasons(it))
    # Looked up rather than a fifth parameter: the ids are pinned in docs/intent.
    kind = {"infra_stop": "infra", "config_error": "config"}.get(outcome)
    if kind is not None:
        assert it.row()["stop_kind"] == kind


async def test_a_failed_sync_step_stops_naming_it_without_spending_an_attempt(item_on, script):
    """The repair ran; the sync that would have published it did not. Nothing
    the next measurement sees was repaired, so this is the plumbing's stop."""
    script.real = {"check"}
    script.plan = {"sync": ["failed"]}
    fix_steps = [
        {"id": "repair", "tasks": [_sub("fix")]},
        {"id": "sync", "tasks": [{"id": "sync", "kind": "forge", "target": "mr.sync"}]},
    ]
    it = await item_on(_node(_sub("check", ["false"]), fix_steps))

    assert await _walk_node(it) == "needs_human"
    assert _counter(it) is None
    assert _reasons(it) == ["fix attempt 1 in node build could not finish: sync [forge] failed"]
    assert it.row()["stop_kind"] == "failed"


async def test_a_repair_that_ran_and_failed_is_a_spent_attempt(item_on, script):
    """Its fix commits, so the round changed something: spent, and measured again."""
    script.real = {"check"}
    script.plan = {"fix": ["failed"]}
    it = await item_on(
        _node(_sub("check", ["false"]), [{"id": "repair", "tasks": [_sub("fix")]}], attempts=1)
    )

    assert await _walk_node(it) == "needs_human"
    assert _counter(it)["count"] == 2
    assert _reasons(it)[-1].startswith(f"{KEY} exhausted after 1 fix cycle(s)")


async def test_a_refunded_cycle_does_not_read_as_no_progress_on_re_entry(item_on, script, tmp_path):
    """The rate-limit poller re-enters the node; the same failure measured
    again is not "unchanged across a fix", because no fix ran."""
    script.real = {"check"}
    script.plan = {"fix": ["rate_limited", "done"]}
    check = _sub("check", _fails_until(tmp_path / "runs", passes_on=3))
    it = await item_on(_node(check, [{"id": "repair", "tasks": [_sub("fix")]}]))

    assert await _walk_node(it) == "rate_limited"
    assert await _walk_node(it) == "ok"
    assert script.calls == ["check", "fix", "check", "fix", "check"]
    assert _reasons(it) == []


async def test_a_retried_node_dispatches_its_recovery_again_rather_than_reusing_it(item_on, script):
    """Kraft-znsvg, on the `on_failure` path: after `/retry` the rounds
    restart, so the pre-retry repair's `done` session sits at the same round
    and head -- and the steered repair must still run."""
    script.real = {"check", "repair"}
    node = {
        "id": "build",
        "kind": "exec",
        "tasks": [_sub("check", ["false"])],
        "on_failure": {"tasks": [_sub("repair")]},
    }
    it = await item_on([node])

    assert await _walk_node(it) == "needs_human"
    await it.database.write(lambda c: store.claim_for_run(c, it.id, from_statuses=["needs_human"]))
    await it.database.write(
        lambda c: store.retry_after_cap(c, it.id, "build", None, "try again", by_person=True)
    )
    assert await _walk_node(it) == "needs_human"

    assert script.calls.count("repair") == 2, script.calls


def _commits_a_file(name):
    code = (
        "import subprocess; "
        f"open({name!r}, 'w').write('x'); "
        f"subprocess.run(['git', 'add', {name!r}], check=True); "
        "subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@t', "
        "'commit', '-qm', 'fix'], check=True)"
    )
    return {"id": "fix", "kind": "subprocess", "command": shlex.join([sys.executable, "-c", code])}


@pytest.mark.parametrize(
    ("fix_task", "committed", "result"),
    [
        pytest.param(_commits_a_file("fixed.txt"), True, "ok", id="fix-commits"),
        pytest.param(_sub("fix"), False, "needs_human", id="fix-changes-nothing"),
    ],
)
async def test_a_finished_fix_round_records_whether_it_committed(
    item_on, script, tmp_path, fix_task, committed, result
):
    script.real = {"check", "fix"}
    check = _sub("check", _fails_until(tmp_path / "n", passes_on=2))
    it = await item_on(_node(check, [{"id": "repair", "tasks": [fix_task]}]))

    assert await _walk_node(it) == result
    [finished] = [e["payload"] for e in it.events("fix_cycle_finished")]
    head = git(it.repo, "rev-parse", "HEAD")
    assert finished == {"node_id": "build", "cycle": 1, "committed": committed, "head_sha": head}


async def test_a_fix_round_whose_head_cannot_be_read_records_null_and_the_walk_continues(
    item_on, script, tmp_path, monkeypatch
):
    script.real = {"check", "fix"}
    gone = tmp_path / "gone"
    fix = _sub("fix", [sys.executable, "-c", f"open({str(gone)!r}, 'w')"])
    check = _sub("check", _fails_until(tmp_path / "n", passes_on=2))
    it = await item_on(_node(check, [{"id": "repair", "tasks": [fix]}]))
    real = walk._config.git_read

    def git_read(cwd, *args, **kw):
        return None if gone.exists() and args == ("rev-parse", "HEAD") else real(cwd, *args, **kw)

    monkeypatch.setattr(walk._config, "git_read", git_read)

    assert await _walk_node(it) == "ok"
    [finished] = [e["payload"] for e in it.events("fix_cycle_finished")]
    assert finished["committed"] is None and finished["head_sha"] is None


async def test_a_refunded_fix_round_has_no_finish(item_on, script):
    script.real = {"check"}
    script.plan = {"fix": ["rate_limited"]}
    it = await item_on(_node(_sub("check", ["false"]), [{"id": "repair", "tasks": [_sub("fix")]}]))

    await _walk_node(it)
    assert it.events("fix_cycle_refunded") and not it.events("fix_cycle_finished")


DOUBT = "No code changed: these failures are the host's, not the branch's."


@pytest.mark.parametrize(
    ("fix_status", "tail", "earlier_thread_event"),
    [
        pytest.param("done", "The repair ended done and gave no concerns.", False, id="done"),
        pytest.param("failed", "The repair ended failed and gave no concerns.", False, id="failed"),
        pytest.param(
            "done_with_concerns", f"The repair said: fix: {DOUBT}", False, id="with-concerns"
        ),
        # Only a thread update appended during the repair counts: one from
        # before it must not hide that the round changed nothing.
        pytest.param(
            "done",
            "The repair ended done and gave no concerns.",
            True,
            id="after-an-earlier-thread-update",
        ),
    ],
)
async def test_a_fix_round_that_changed_nothing_stops_without_measuring_again(
    item_on, script, fix_status, tail, earlier_thread_event
):
    """Kraft-6rph9: 3k7w4qfc's repair changed nothing and said so, and the
    whole node -- a 14-minute suite and a code review -- ran again on the
    same tree."""
    script.real = {"check"}
    script.plan = {"fix": [fix_status]}
    script.unchanged = {"fix"}
    it = await item_on(_node(_sub("check", ["false"]), [{"id": "repair", "tasks": [_sub("fix")]}]))
    if earlier_thread_event:
        payload = {"thread_id": "t0", "state": "claimed"}
        await it.database.write(
            lambda c: events.append(c, it.id, GateEvent.THREAD_UPDATED, payload)
        )
    if fix_status == "done_with_concerns":

        async def concerned(_row):
            result = {"status": "done_with_concerns", "concerns": DOUBT}
            (it.run_dirs.results / "s-fix.json").write_text(json.dumps(result))
            await it.session("s-fix", "build.fix_loop.repair.fix", "done_with_concerns")

        script.effects["fix"] = concerned

    assert await _walk_node(it) == "needs_human"
    assert script.calls == ["check", "fix"]
    assert _counter(it)["count"] == 1
    assert _reasons(it) == [
        "stuck: fix attempt 1 in node build changed nothing, so measuring again "
        f"would see the same code. {tail}"
    ]
    assert it.row()["stop_kind"] == "stuck"
    assert prompts.FIX_NO_CHANGE_NOTE in script.instructions["fix"][0]


@pytest.mark.parametrize(
    "change", ["uncommitted-edit", "thread-reply", "snapshot-refused", "mr-meta-edit"]
)
async def test_a_fix_round_that_changed_anything_measures_again(
    item_on, script, tmp_path, monkeypatch, change
):
    check = _sub("check", _fails_until(tmp_path / "n", passes_on=2))
    fix = _sub("fix")
    script.real = {"check"}
    script.unchanged = {"fix"}
    if change == "uncommitted-edit":
        # A subprocess repair is never swept, so the file stays uncommitted.
        script.real = {"check", "fix"}
        fix = _sub("fix", [sys.executable, "-c", "open('left.txt', 'w').write('x')"])
    it = await item_on(_node(check, [{"id": "repair", "tasks": [fix]}]))
    if change == "thread-reply":

        async def answered(_row):
            payload = {"thread_id": "t1", "state": "claimed"}
            await it.database.write(
                lambda c: events.append(c, it.id, GateEvent.THREAD_UPDATED, payload)
            )

        script.effects["fix"] = answered
    if change == "snapshot-refused":
        monkeypatch.setattr(walk._read_only, "snapshot", lambda *a, **k: None)
    if change == "mr-meta-edit":
        # The repair edits the MR description document, which lives outside the
        # checkout; the loop's `sync` step then writes it and re-runs the checks.
        docs = iter(["old", "new"])
        monkeypatch.setattr(
            walk.forge_mod, "read_mr_meta", lambda *a: walk.forge_mod.MRMeta(description=next(docs))
        )

    assert await _walk_node(it) == "ok"
    assert script.calls == ["check", "fix", "check"]
