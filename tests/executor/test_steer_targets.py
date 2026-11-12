"""A steer on resume reaches every paused agent task unless the operator
addresses tasks individually, and a paused agent task resumes its own session
when it can (docs/templates-v1-design.md "Operator controls and run forks")."""

from __future__ import annotations

import json

import pytest
from support.harness import harness_without, write_harness_profiles

from kraft import executor, store
from kraft.executor import dispatch, prompts
from kraft.paths import config_dir

#: Two agent tasks and a subprocess task running side by side in one step.
CHAIN = """
- id: work
  kind: exec
  tasks:
    - {id: front, kind: agent, harness: fake, prompt: Build the front end.}
    - {id: back, kind: agent, harness: fake, prompt: Build the back end.}
    - {id: lint, kind: subprocess, command: "true"}
"""

LAUNCH = executor.LaunchContext(repo_entry=None)


@pytest.fixture
def launched(monkeypatch, fake_agent):
    """Every agent launch's keyword arguments, by task path, on the `fake`
    harness (which declares `resume`)."""
    seen: dict[str, dict] = {}

    async def fake(db, run_dirs, *, hook_point, **kwargs):
        seen[hook_point] = kwargs
        return "done"

    monkeypatch.setattr(dispatch._agent, "run_agent_task", fake)
    return seen


async def _paused(item_on, *paths, ended: dict[str, str] | None = None):
    """The item with a session per path, each `paused` unless `ended` says how
    it ended (`needs_context`: it asked). The item stays `paused`: dispatch
    reads only the session row."""
    it = await item_on(CHAIN, "work", status="paused")
    for i, path in enumerate(paths):
        await it.session(f"s{i}", path, (ended or {}).get(path, "paused"))
    return it


async def _dispatch(it, path, steer):
    # As a resume does before its walk dispatches anything: a paused item launches nothing.
    await it.database.write(lambda c: store.claim_for_run(c, it.id, from_statuses=["paused"]))
    node = it.chain.chain.nodes[0]
    task = next(t for t in node.steps[0].tasks if t.path == path)
    await dispatch.dispatch_node(
        it.database, it.run_dirs, task, node, it.row(), it.repo, launch=LAUNCH, steer=steer
    )


def _steer_for(it, text, steers=None):
    return executor.resume_steer(it.database, it.row(), text, steers or {})


async def test_one_steer_reaches_every_paused_agent_task(item_on, launched):
    """`steer-defaults-to-all-paused-agent-tasks`. It used to be good for one
    launch, whichever got there first."""
    it = await _paused(item_on, "work.main.front", "work.main.back", "work.main.lint")
    steer = executor.Steer(source="human", to=_steer_for(it, "keep the old API"))

    for path in ("work.main.front", "work.main.back"):
        await _dispatch(it, path, steer)

    for path in ("work.main.front", "work.main.back"):
        assert "keep the old API" in launched[path]["task_instruction"], path


async def test_a_task_addressed_individually_gets_its_own_steer(item_on, launched):
    """`steer-can-address-paused-agent-tasks-individually`: `back` gets its own
    words and `front` still gets the steer every paused task gets."""
    it = await _paused(item_on, "work.main.front", "work.main.back")
    targets = _steer_for(it, "keep the old API", {"work.main.back": "use the new schema"})
    steer = executor.Steer(source="human", to=targets)

    # Launched in the other order than paused: each takes its own, not the next.
    for path in ("work.main.back", "work.main.front"):
        await _dispatch(it, path, steer)

    assert "keep the old API" in launched["work.main.front"]["task_instruction"]
    assert "use the new schema" in launched["work.main.back"]["task_instruction"]
    assert "keep the old API" not in launched["work.main.back"]["task_instruction"]


@pytest.mark.parametrize(
    "ended",
    [{}, {"work.main.front": "needs_context", "work.main.back": "failed"}],
    ids=["paused", "asked"],
)
async def test_a_task_that_was_not_paused_gets_no_default_steer(item_on, ended):
    """A task that failed or finished gets no steer; one that asked gets the answer."""
    it = await _paused(
        item_on, "work.main.front", *(["work.main.back"] if ended else []), ended=ended
    )

    assert _steer_for(it, "keep the old API") == {"work.main.front": "keep the old API"}


@pytest.mark.parametrize(
    ("path", "says"),
    [
        ("work.main.lint", "is a subprocess task"),
        ("work.main.nope", "has no task 'nope'"),
        ("work.main", "is not a task"),
        ("work.main.back", "is neither paused nor waiting for an answer"),
    ],
    ids=["non-agent", "unknown", "a-step", "not-paused"],
)
async def test_a_steer_that_cannot_land_is_refused_naming_its_task(item_on, path, says):
    """`steer-can-address-paused-agent-tasks-individually`: a steer SHALL NOT
    target a non-agent task."""
    it = await _paused(item_on, "work.main.front", "work.main.lint")

    with pytest.raises(executor.SteerError) as refused:
        _steer_for(it, None, {path: "x"})

    assert refused.value.field == f"steers.{path}"
    assert says in str(refused.value)


def _log_init(it, sid, cli_session):
    log = it.run_dirs.logs / f"{sid}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(json.dumps({"type": "system", "subtype": "init", "session_id": cli_session}))


@pytest.mark.parametrize(
    ("ended", "answer", "note"),
    [
        ("paused", "mind the tests", prompts.AGENT_RESUMED_NOTE),
        ("needs_context", "mind the tests", prompts.AGENT_ANSWERED_NOTE),
        ("needs_context", None, prompts.AGENT_UNANSWERED_NOTE),
    ],
    ids=["paused", "asked", "asked-no-answer"],
)
async def test_a_paused_agent_task_resumes_its_own_session(item_on, launched, ended, answer, note):
    """`resumed-agent-task-preserves-its-session-when-possible`: the paused (or
    asking) session's provider id rides `--resume`, and the prompt is the steer
    or answer and a note to carry on -- not the whole brief again."""
    it = await _paused(item_on, "work.main.front", ended={"work.main.front": ended})
    _log_init(it, "s0", "cli-front")

    await _dispatch(it, "work.main.front", executor.Steer(answer, source="human"))
    sent = launched["work.main.front"]
    assert sent["resume_session_id"] == "cli-front"
    if answer:
        assert answer in sent["task_instruction"]
    assert note in sent["task_instruction"]
    assert "Build the front end." not in sent["task_instruction"]
    assert it.events("agent_session_resumed")[0]["payload"] == {
        "task": "work.main.front",
        "session_id": "s0",
    }


@pytest.mark.parametrize(
    "why",
    ["no-provider-id", "not-paused", "before-a-retry", "asked-on-a-harness-without-resume"],
)
async def test_an_agent_task_that_cannot_resume_restarts_with_its_instruction(
    item_on, launched, monkeypatch, why
):
    """The fallback half: no session the provider can resume, one that ended
    some other way, or one from before a retry (a retry never reuses a
    pre-retry session) -- the task restarts with its original instruction and
    the steer."""
    asked = {"work.main.front": "needs_context"} if why.startswith("asked") else None
    it = await _paused(item_on, *(["work.main.front"] if why != "not-paused" else []), ended=asked)
    if why.startswith("asked"):
        # Built before patching: `harness_without` itself calls `harness.load`.
        table = harness_without("resume", "fake")
        monkeypatch.setattr(dispatch._harness, "load", lambda *_a, **_k: table)
    if why == "not-paused":
        await it.session("s0", "work.main.front", "failed")
    if why != "no-provider-id":
        _log_init(it, "s0", "cli-front")
    if why == "before-a-retry":
        await it.database.write(lambda c: store.fork_run(c, it.id, None, by_person=True))

    await _dispatch(it, "work.main.front", executor.Steer("mind the tests", source="human"))

    sent = launched["work.main.front"]
    assert sent["resume_session_id"] is None
    assert "Build the front end." in sent["task_instruction"]
    assert "mind the tests" in sent["task_instruction"]


async def test_a_paused_codex_task_resumes_the_thread_its_own_log_names(item_on, launched):
    """Kraft-wge0e: the provider id is read with the task's harness's reader.
    A codex log names its thread on `thread.started`, which claude's reader
    never sees, so the task would restart instead of resuming."""
    # The live table a launch reads; the seeded `codex` profile is on `fake`.
    write_harness_profiles(
        config_dir(),
        {"real-codex": {"provider": "codex"}},
    )
    it = await item_on(
        CHAIN.replace("harness: fake", "harness: real-codex"), "work", status="paused"
    )
    await it.session("s0", "work.main.front", "paused")
    log = it.run_dirs.logs / "s0.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(json.dumps({"type": "thread.started", "thread_id": "thread-front"}))

    await _dispatch(it, "work.main.front", executor.Steer("mind the tests", source="human"))

    assert launched["work.main.front"]["resume_session_id"] == "thread-front"
