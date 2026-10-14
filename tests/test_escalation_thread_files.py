"""An escalation thread's result and summary files are one pair of names for
every turn in it (Kraft-s7c04.54), driven through the real `escalate.dispatch`,
`run_agent_task` and `run_task`, with only the harness's argv swapped for a
shell script that plays the turn."""

from __future__ import annotations

import asyncio
import json
import shlex
from pathlib import Path

import pytest
from support.harness import v1_chain, write_harness_profiles

from kraft import escalate, executor, item_paths, store
from kraft.adapters import subprocess as sp

_CHAIN = v1_chain(
    [
        {
            "id": "implementation",
            "kind": "exec",
            "tasks": [{"id": "work", "kind": "subprocess", "command": "true"}],
        }
    ],
    repo="/r",
)
_INIT = json.dumps({"type": "system", "subtype": "init", "session_id": "cli-1"})
_LAUNCH = executor.LaunchContext(repo_entry=None, skills_dir=None)


@pytest.fixture(autouse=True)
def escalation_profile(tmp_path, monkeypatch):
    templates = tmp_path / "escalation-templates"
    write_harness_profiles(templates, {"claude": {"provider": "claude"}})
    monkeypatch.setenv("KRAFT_CONFIG_DIR", str(templates))


@pytest.fixture
def turns(monkeypatch, database, run_dirs):
    """`await turns(script, ..., between=None)`: one escalation turn per shell
    script, each run in place of the harness argv, and `await between(i,
    played)` after turn `i`, so a test can rewrite a row before the next
    turn. Returns each turn's status, its argv (the context it was handed)
    and its session id."""
    played = []

    async def play(db, run_dirs, *, cmd, **kw):
        played.append({"argv": " ".join(cmd), "session_id": kw["session_id"]})
        script = f"printf '%s\\n' {shlex.quote(_INIT)}; {scripts.pop(0)}"
        return await real_run_task(db, run_dirs, cmd=["sh", "-c", script], **kw)

    real_run_task = sp.run_task
    monkeypatch.setattr("kraft.adapters.agent._subprocess.run_task", play)
    scripts: list[str] = []

    async def go(*steps: str, between=None) -> list[dict]:
        await database.write(
            lambda c: store.create_work_item(
                c,
                id="w1",
                bead_id=None,
                title="t",
                repo=str(run_dirs.base),
                chain_template="quick-task",
                chain_definition="{}",
                materialized_chain=_CHAIN.to_json(),
            )
        )
        await database.write(lambda c: store.enter_node(c, "w1", "implementation"))
        await database.write(
            lambda c: store.mark_needs_human(c, "w1", "implementation", "x", kind="failed")
        )
        (run_dirs.worktrees / "w1" / ".engineering" / "sessions").mkdir(parents=True)
        scripts.extend(steps)
        for i, _ in enumerate(steps):
            status = await escalate.dispatch(
                database, run_dirs, work_item_id="w1", message=f"turn {i + 1}", launch=_LAUNCH
            )
            played[i]["status"] = status
            if between is not None:
                await between(i, played)
        return played

    return go


def _row(database, sid):
    return database.read(
        lambda c: c.execute("SELECT * FROM worker_sessions WHERE id = ?", (sid,)).fetchone()
    )


#: Where every turn of a thread that starts now writes, under the run folder.
_THREAD = "items/w1/sessions/escalation-1/out"
_DONE = shlex.quote(json.dumps({"status": "done"}))


async def test_a_resumed_turn_that_writes_where_it_remembers_is_done(turns, run_dirs):
    """`a-resumed-escalation-turn-writes-where-it-remembers` (b5afe84c): the
    second turn ignores this turn's instruction and writes its result to the
    literal path the first turn wrote to. That is this turn's path too."""
    remembered = run_dirs.base / "memory"
    first = f'echo "$KRAFT_RESULT_PATH" > {remembered}; echo {_DONE} > "$KRAFT_RESULT_PATH"'
    second = f'echo {_DONE} > "$(cat {remembered})"'

    played = await turns(first, second)

    assert [t["status"] for t in played] == ["done", "done"]
    summary = str(run_dirs.base / _THREAD / "summary.md")
    assert summary in played[0]["argv"] and summary in played[1]["argv"]
    assert remembered.read_text().strip() == str(run_dirs.base / _THREAD / "result.json")
    assert "--resume cli-1" in played[1]["argv"]


@pytest.mark.parametrize("started", ["after-upgrade", "before-upgrade"])
async def test_a_turn_starts_with_no_result_file_from_the_last(turns, database, run_dirs, started):
    """The shared name must not hand a turn that wrote nothing the last
    turn's result: it ends `failed`, as a turn with no result file does. Nor
    the last turn's summary: it records none. An older thread's summary stays
    in the worktree for every turn, so there Kraft records none itself, and
    the worker's own ref decides, as before the upgrade."""
    summary = run_dirs.base / _THREAD / "summary.md"
    legacy_result, legacy_summary = item_paths.legacy_thread_files(run_dirs, "w1", 1)
    shared_ref = legacy_summary.relative_to(run_dirs.worktrees / "w1").as_posix()

    async def make_turn_one_old(i, played):
        if i == 0 and started == "before-upgrade":
            sid = played[0]["session_id"]
            legacy_summary.write_text("# one\n")
            legacy_result.write_text(json.dumps({"status": "done"}))
            await database.write(
                lambda c: c.execute(
                    "UPDATE worker_sessions SET log_path = ?, result_path = ?, "
                    "session_summary_ref = ? WHERE id = ?",
                    (str(run_dirs.logs / f"{sid}.log"), str(legacy_result), shared_ref, sid),
                )
            )

    played = await turns(
        f'echo "# one" > {summary}; echo \'{{"status":"done"}}\' > "$KRAFT_RESULT_PATH"',
        "true",
        between=make_turn_one_old,
    )

    assert [t["status"] for t in played] == ["done", "failed"]
    first = (
        f".engineering/sessions/{played[0]['session_id']}.md"
        if started == "before-upgrade"
        else item_paths.session_files(run_dirs, "w1", played[0]["session_id"])
        .summary.relative_to(run_dirs.base)
        .as_posix()
    )
    assert [_row(database, t["session_id"])["session_summary_ref"] for t in played] == [
        first,
        None,
    ]


async def test_each_turns_result_and_summary_stay_readable_from_its_row(turns, database, run_dirs):
    """Per-turn history survives the shared name: the last turn's result goes
    back into its own session folder, and its summary is copied there, before
    the next turn writes, its row following them; the current turn's row
    points at the shared name, and Kraft records the summary it wrote."""
    shared = run_dirs.base / _THREAD

    def turn(n):
        result = json.dumps({"status": "done", "n": n})
        summary = f"echo 'turn {n}' > {shared / 'summary.md'}"
        return f'{summary}; echo {shlex.quote(result)} > "$KRAFT_RESULT_PATH"'

    first, second = await turns(turn(1), turn(2))

    one, two = _row(database, first["session_id"]), _row(database, second["session_id"])
    own = item_paths.session_files(run_dirs, "w1", first["session_id"])
    assert one["result_path"] == str(own.result)
    assert json.loads(own.result.read_text())["n"] == 1
    assert item_paths.exit_file(one["log_path"], one["result_path"]).read_text().strip() == "0"
    assert one["session_summary_ref"] == own.summary.relative_to(run_dirs.base).as_posix()
    assert own.summary.read_text() == "turn 1\n"
    assert two["result_path"] == str(shared / "result.json")
    assert json.loads(Path(two["result_path"]).read_text())["n"] == 2
    assert two["session_summary_ref"] == f"{_THREAD}/summary.md"
    assert (shared / "summary.md").read_text() == "turn 2\n"


@pytest.mark.parametrize("started", ["before-upgrade", "after-upgrade"])
async def test_a_thread_keeps_its_first_turn_s_paths_for_every_turn(
    turns, database, run_dirs, started
):
    """A resumed conversation remembers the literal paths an earlier turn
    used. A thread is from before the upgrade when its first turn's log is not
    in a session folder; `result_path` is no test, because archiving a turn
    rewrites it. Each turn's log is its own either way. So is how a turn's
    summary is kept: an older thread's stays the worker's to name, even for a
    turn that ran after the upgrade, so a turn that wrote none records none
    and has nothing of the last turn's copied into its folder."""
    record = run_dirs.base / "paths"
    turn = f'echo "$KRAFT_RESULT_PATH" >> {record}; echo {_DONE} > "$KRAFT_RESULT_PATH"'
    legacy, legacy_summary = item_paths.legacy_thread_files(run_dirs, "w1", 1)
    shared_ref = legacy_summary.relative_to(run_dirs.worktrees / "w1").as_posix()

    async def make_turn_one_old(i, played):
        if i == 0 and started == "before-upgrade":
            sid = played[0]["session_id"]
            await database.write(
                lambda c: c.execute(
                    "UPDATE worker_sessions SET log_path = ?, result_path = ?, "
                    "session_summary_ref = ? WHERE id = ?",
                    (str(run_dirs.logs / f"{sid}.log"), str(legacy), shared_ref, sid),
                )
            )
            legacy.write_text(json.dumps({"status": "done"}))
            legacy_summary.write_text("# turn one\n")

    played = await turns(turn, turn, turn, between=make_turn_one_old)

    paths = record.read_text().splitlines()
    want = legacy if started == "before-upgrade" else run_dirs.base / _THREAD / "result.json"
    assert paths[1:] == [str(want), str(want)]
    # The last turn's result went back under its own name: an older turn's
    # beside it in `results/`, as before the upgrade, a newer one's in its
    # own folder.
    first, second = (_row(database, p["session_id"]) for p in played[:2])
    own = [item_paths.session_files(run_dirs, "w1", p["session_id"]) for p in played[:2]]
    older = started == "before-upgrade"
    assert first["result_path"] == str(
        run_dirs.results / f"{played[0]['session_id']}.json" if older else own[0].result
    )
    assert second["result_path"] == str(own[1].result)
    assert all(Path(r["result_path"]).is_file() for r in (first, second))
    assert all(
        not item_paths.is_legacy_log(_row(database, p["session_id"])["log_path"])
        for p in played[1:]
    )
    if older:
        assert second["session_summary_ref"] is None
        assert not own[1].summary.exists()


async def test_a_folder_planted_at_the_thread_s_summary_does_not_stop_the_next_turn(
    turns, run_dirs, monkeypatch
):
    """A worker can leave a folder where the thread's summary belongs. The
    next turn starts with the name cleared, as it would a file, and the
    removal, a walk of whatever the worker put there, runs off the event
    loop."""
    summary = run_dirs.base / _THREAD / "summary.md"
    plant = f'rm -f {summary}; mkdir -p {summary}/x; echo {_DONE} > "$KRAFT_RESULT_PATH"'
    real = item_paths.remove_tree
    walked, on_loop = [], []

    def remove_tree(path):
        walked.append(path)
        try:
            asyncio.get_running_loop()
            on_loop.append(path)
        except RuntimeError:
            pass
        return real(path)

    monkeypatch.setattr(item_paths, "remove_tree", remove_tree)

    played = await turns(plant, f'echo {_DONE} > "$KRAFT_RESULT_PATH"')

    assert [t["status"] for t in played] == ["done", "done"]
    assert summary.is_file() and not summary.is_symlink()
    assert walked and on_loop == []
