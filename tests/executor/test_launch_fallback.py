"""Launch fallback (Kraft-0a3h8, `executor.fallback`): an agent task with a
`fallback:` list moves to its next candidate when a launch is rate-limited or
its harness is unavailable, remembers a limited harness+model until its reset,
and logs every skip or switch as one `launch_fallback` event.

Every launch here is `tests/support/fake_agent.py` on the `fake` provider, which
speaks claude's stream-json, so its `rate_limit_event` is read exactly as a real
claude one; `KRAFT_FAKE_AGENT_RATE_LIMIT_MODELS` limits only the named models."""

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from support.harness import v1_walk, write_harness_profiles

from kraft import db as _db
from kraft import events, store
from kraft.executor import dispatch, fallback
from kraft.paths import RunDirs

#: Two resets well ahead, the first earlier.
SOON = int(time.time()) + 3600
LATER = SOON + 3600


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, UTC).isoformat()


def _task(**fields):
    return {
        "id": "implement",
        "kind": "agent",
        "harness": "claude",
        "model": "opus",
        "prompt": "Do it.",
        **fields,
    }


def _chain(**fields):
    return [{"id": "build", "kind": "exec", "tasks": [_task(**fields)]}]


def _walk(tmp_path, repo, chain, **kwargs):
    from support.harness import v1_chain

    return v1_walk(tmp_path, v1_chain(chain, repo=repo), repo=repo, **kwargs)


def _limit(monkeypatch, *models, resets_at=SOON):
    monkeypatch.setenv("KRAFT_FAKE_AGENT_RATE_LIMIT_MODELS", ",".join(models))
    monkeypatch.setenv("KRAFT_FAKE_AGENT_RESETS_AT", str(resets_at))


def _models(fake_agent) -> list[str | None]:
    """The `--model` of every launch, in order."""
    out = []
    for argv in fake_agent.argv():
        out.append(argv[argv.index("--model") + 1] if "--model" in argv else None)
    return out


def _fallbacks(evts):
    return [e["payload"] for e in evts if e["type"] == "launch_fallback"]


def _profiles(fake_agent, profiles: dict) -> None:
    """Merge harness profiles into both places a launch may read them from."""
    write_harness_profiles(fake_agent.templates, profiles)
    write_harness_profiles(Path(os.environ["KRAFT_HOME"]) / "templates", profiles)


# -- a rate-limited launch moves on, in the same dispatch ----------------------


async def test_a_rate_limited_launch_falls_back_in_the_same_dispatch(
    tmp_path, repo, fake_agent, monkeypatch
):
    _limit(monkeypatch, "opus")

    status, evts, sessions, _ = await _walk(
        tmp_path, repo, _chain(effort="high", fallback=[{"model": "sonnet"}])
    )

    assert status == "completed"
    # One session row per attempt, the fallback's with its own id.
    assert [s["status"] for s in sessions] == ["rate_limited", "done"]
    assert len({s["id"] for s in sessions}) == 2
    assert _models(fake_agent) == ["opus", "sonnet"]
    (switch,) = _fallbacks(evts)
    assert switch == {
        "node_id": "build",
        "task": "build.main.implement",
        "from": {"harness": "claude", "model": "opus", "effort": "high"},
        # The entry omits effort, so the task's own is kept.
        "to": {"harness": "claude", "model": "sonnet", "effort": "high"},
        "reason": "rate_limit_hit",
        "resets_at_iso": _iso(SOON),
        "session_id": sessions[1]["id"],
        "override_not_carried": False,
    }
    # The hit names what it limited, for every later launch to skip.
    (hit,) = [e["payload"] for e in evts if e["type"] == "rate_limit_hit"]
    assert (hit["harness"], hit["model"]) == ("claude", "opus")


async def test_a_switch_bumps_no_rate_limit_counter(tmp_path, repo, fake_agent, monkeypatch):
    _limit(monkeypatch, "opus")
    rd = RunDirs(tmp_path / "run").ensure()

    await _walk(tmp_path, repo, _chain(fallback=[{"model": "sonnet"}]), run_dirs=rd)

    database = await _db.Database.open(rd.db)
    try:
        counters = database.read(
            lambda c: c.execute("SELECT * FROM retry_counters WHERE work_item_id = 'w1'").fetchall()
        )
    finally:
        await database.close()
    assert [dict(r) for r in counters] == []


async def test_the_fallback_is_told_about_the_limited_attempt_only_after_one_ran(
    tmp_path, repo, fake_agent, monkeypatch
):
    """The note goes after a launch that ran and was limited, and never to the
    first launch."""
    _limit(monkeypatch, "opus")

    await _walk(tmp_path, repo, _chain(fallback=[{"model": "sonnet"}]))

    first, second = fake_agent.prompts()
    assert "stopped by an API rate limit" not in first
    assert "An earlier attempt at this task on `claude / opus` was stopped" in second
    assert "check `git status` and `git diff`" in second


async def test_an_unavailable_skip_adds_no_rate_limit_note(tmp_path, repo, fake_agent):
    """Only a candidate that actually ran and was limited earns the note."""
    _profiles(fake_agent, {"off": {"provider": "fake", "enabled": False}})

    status, evts, _, _ = await _walk(
        tmp_path, repo, _chain(harness="off", fallback=[{"harness": "claude"}])
    )

    assert status == "completed"
    (prompt,) = fake_agent.prompts()
    assert "stopped by an API rate limit" not in prompt


# -- every candidate limited: park until the earliest reset --------------------


async def test_all_candidates_limited_parks_until_the_earliest_reset(
    tmp_path, repo, fake_agent, monkeypatch
):
    _limit(monkeypatch, "opus", resets_at=LATER)
    rd = RunDirs(tmp_path / "run").ensure()
    # sonnet was limited by another item, and resets sooner than opus.
    await _seed_hit(rd, "claude", "sonnet", SOON)

    status, evts, sessions, row = await _walk(
        tmp_path, repo, _chain(fallback=[{"model": "sonnet"}]), run_dirs=rd
    )

    assert status == "rate_limited"
    assert row["retry_at"] == _iso(SOON)
    assert [s["status"] for s in sessions] == ["rate_limited"]
    assert [(f["reason"], f["to"]) for f in _fallbacks(evts)] == [
        ("rate_limit_hit", {"harness": "claude", "model": "sonnet", "effort": None}),
        ("known_limited", None),
    ]


async def test_the_relaunch_starts_from_the_top_once_the_first_choice_is_back(
    tmp_path, repo, fake_agent, monkeypatch
):
    """Everything limited parks; the next walk (the poller's relaunch) skips
    what is still limited without launching, and uses the first choice again
    as soon as its reset has passed."""
    _limit(monkeypatch, "opus", "sonnet")
    rd = RunDirs(tmp_path / "run").ensure()
    chain = _chain(fallback=[{"model": "sonnet"}])

    status, *_ = await _walk(tmp_path, repo, chain, run_dirs=rd, wid="a")
    assert status == "rate_limited"
    assert _models(fake_agent) == ["opus", "sonnet"]

    status, evts, sessions, _ = await _walk(tmp_path, repo, chain, run_dirs=rd, wid="b")
    assert status == "rate_limited"
    assert sessions == []  # nothing launched: both known-limited
    assert [f["reason"] for f in _fallbacks(evts)] == ["known_limited", "known_limited"]

    await _seed_hit(rd, "claude", "opus", int(time.time()) - 60)
    monkeypatch.delenv("KRAFT_FAKE_AGENT_RATE_LIMIT_MODELS")
    status, evts, sessions, _ = await _walk(tmp_path, repo, chain, run_dirs=rd, wid="c")
    assert status == "completed"
    assert _models(fake_agent)[2:] == ["opus"]
    assert _fallbacks(evts) == []


# -- memory: known-limited across items, until reset ---------------------------


async def _seed_hit(rd, harness, model, resets_at, *, wid="other"):
    """A `rate_limit_hit` on another item, as `_subprocess.run_task` writes one
    (`harness` None: one written before hits carried it)."""
    database = await _db.Database.open(rd.db)
    try:

        def write(c):
            if c.execute("SELECT 1 FROM work_items WHERE id = ?", (wid,)).fetchone() is None:
                store.create_work_item(
                    c, id=wid, bead_id=None, title="o", repo="/r", chain_template="t",
                    chain_definition="{}",
                )  # fmt: skip
            payload = {"resets_at": resets_at, "resets_at_iso": _iso(resets_at), "node_id": "n"}
            if harness is not None:
                payload |= {"harness": harness, "model": model}
            events.append(c, wid, "rate_limit_hit", payload)

        await database.write(write)
    finally:
        await database.close()


async def test_a_limit_hit_on_one_item_is_skipped_by_another_until_reset(
    tmp_path, repo, fake_agent, monkeypatch
):
    rd = RunDirs(tmp_path / "run").ensure()
    await _seed_hit(rd, "claude", "opus", SOON)

    status, evts, sessions, _ = await _walk(
        tmp_path, repo, _chain(fallback=[{"model": "sonnet"}]), run_dirs=rd
    )

    assert status == "completed"
    assert _models(fake_agent) == ["sonnet"]
    (skip,) = _fallbacks(evts)
    assert skip["reason"] == "known_limited"
    assert skip["resets_at_iso"] == _iso(SOON)
    assert skip["from"] == {"harness": "claude", "model": "opus", "effort": None}
    assert skip["to"]["model"] == "sonnet"
    assert skip["session_id"] == sessions[0]["id"]


@pytest.mark.parametrize(
    ("harness", "model", "resets_at"),
    [
        (None, None, SOON),  # written before a hit named its harness
        ("claude", "opus", int(time.time()) - 60),  # its reset has passed
        ("codex", "opus", SOON),  # another harness
        ("claude", "haiku", SOON),  # another model
    ],
    ids=["pre-change-event", "reset-passed", "other-harness", "other-model"],
)
async def test_a_hit_that_does_not_match_is_not_remembered(
    tmp_path, repo, fake_agent, harness, model, resets_at
):
    rd = RunDirs(tmp_path / "run").ensure()
    await _seed_hit(rd, harness, model, resets_at)

    status, evts, _, _ = await _walk(
        tmp_path, repo, _chain(fallback=[{"model": "sonnet"}]), run_dirs=rd
    )

    assert status == "completed"
    assert _models(fake_agent) == ["opus"]
    assert _fallbacks(evts) == []


async def test_a_task_without_a_list_never_consults_memory(tmp_path, repo, fake_agent):
    """`fallback-is-opt-in`: no list, no skip, no event -- today's launch."""
    rd = RunDirs(tmp_path / "run").ensure()
    await _seed_hit(rd, "claude", "opus", SOON)

    for fields in ({}, {"fallback": []}):
        status, evts, _, _ = await _walk(
            tmp_path, repo, _chain(**fields), run_dirs=rd, wid=f"w{len(fields)}"
        )
        assert status == "completed"
        assert _fallbacks(evts) == []
    assert _models(fake_agent) == ["opus", "opus"]


async def test_a_task_without_a_list_parks_on_a_limit_as_before(
    tmp_path, repo, fake_agent, monkeypatch
):
    _limit(monkeypatch, "opus")

    status, evts, sessions, row = await _walk(tmp_path, repo, _chain(fallback=[]))

    assert status == "rate_limited"
    assert row["retry_at"] == _iso(SOON)
    assert [s["status"] for s in sessions] == ["rate_limited"]
    assert _fallbacks(evts) == []
    assert "launch_fallback_exhausted" not in [e["type"] for e in evts]


def test_the_known_limited_lookup_uses_the_events_type_index(tmp_path):
    conn = _db._connect(tmp_path / "k.db")
    _db.migrate(conn)
    plan = conn.execute(
        "EXPLAIN QUERY PLAN SELECT payload FROM events WHERE type = 'rate_limit_hit' "
        "AND json_extract(payload, '$.harness') = ? AND json_extract(payload, '$.model') IS ? "
        "ORDER BY seq DESC LIMIT 1",
        ("claude", None),
    ).fetchall()
    assert any("idx_events_type" in row["detail"] for row in plan), [dict(r) for r in plan]
    assert fallback.known_limited(conn, "claude", None) is None


# -- unavailable: skipped, never a stop, when a list exists --------------------


@pytest.mark.parametrize(
    ("profile", "why"),
    [
        (None, "defines no such profile"),
        ({"provider": "fake", "enabled": False}, "'primary' is disabled"),
        ({"provider": "fake", "defaults": {"autocompact": "50"}}, "does not apply"),
        ({"provider": "fake", "executable": "kraft-no-such-binary"}, "is not on PATH"),
    ],
    ids=["absent", "disabled", "unapplied-default", "not-on-path"],
)
async def test_an_unavailable_candidate_falls_back_to_the_next(
    tmp_path, repo, fake_agent, profile, why
):
    """Each way `test_an_unavailable_selected_harness_stops_for_a_human` makes a
    harness unavailable, on a task with a list: the next candidate launches and
    the skip says why."""
    if profile is not None:
        _profiles(fake_agent, {"primary": profile})

    status, evts, sessions, _ = await _walk(
        tmp_path, repo, _chain(harness="primary", fallback=[{"harness": "claude"}])
    )

    assert status == "completed"
    assert [s["status"] for s in sessions] == ["done"]
    (skip,) = _fallbacks(evts)
    assert skip["reason"] == "unavailable"
    assert why in skip["detail"], skip["detail"]
    assert skip["from"] == {"harness": "primary", "model": "opus", "effort": None}
    assert skip["to"] == {"harness": "claude", "model": "opus", "effort": None}


async def test_every_candidate_unavailable_stops_for_a_human_naming_each(
    tmp_path, repo, fake_agent
):
    _profiles(fake_agent, {"off": {"provider": "fake", "enabled": False}})

    status, evts, sessions, _ = await _walk(
        tmp_path, repo, _chain(harness="off", fallback=[{"harness": "gone"}])
    )

    assert status == "needs_human"
    assert [s["status"] for s in sessions] == ["config_error"]
    log = Path(sessions[0]["log_path"]).read_text()
    assert "no launch candidate is available" in log
    assert "off / opus: profile 'off' is disabled" in log
    assert "gone / opus:" in log and "defines no such profile" in log
    assert [(f["reason"], f["to"]) for f in _fallbacks(evts)] == [
        ("unavailable", {"harness": "gone", "model": "opus", "effort": None}),
        ("unavailable", None),
    ]


async def test_a_profile_on_an_unknown_provider_leaves_no_candidate(tmp_path, repo, fake_agent):
    """A profile on a provider this install lacks makes `harnesses.yaml` fail to
    load as a whole, so no candidate can resolve: the stop names each, and
    nothing launches on the provider of the same name."""
    _profiles(fake_agent, {"odd": {"provider": "nonesuch"}})

    status, _, sessions, _ = await _walk(
        tmp_path, repo, _chain(harness="odd", fallback=[{"harness": "claude"}])
    )

    assert status == "needs_human"
    assert fake_agent.argv() == []
    log = Path(sessions[0]["log_path"]).read_text()
    assert log.count("provider 'nonesuch' is not an installed harness") == 2, log


# -- overrides are the primary's; extra_prompt is carried ----------------------


@pytest.mark.parametrize(
    ("item", "node", "escalate"),
    [
        ({"model": "haiku"}, {"extra_prompt": "Mind the tabs."}, False),
        ({}, {"escalate_model": "haiku", "extra_prompt": "Mind the tabs."}, True),
    ],
    ids=["item-override", "escalation"],
)
async def test_overrides_apply_to_the_primary_only_and_extra_prompt_is_carried(
    item_on, fake_agent, monkeypatch, item, node, escalate
):
    """The override (or the escalation's model) picks the task's own launch;
    the fallback runs as its entry says, and the node's `extra_prompt`, part
    of the instruction, reaches it too."""
    _limit(monkeypatch, "haiku")
    it = await item_on(_chain(fallback=[{"model": "sonnet"}]))
    await it.database.write(lambda c: store.set_agent_overrides(c, it.id, json.dumps(item)))
    await it.database.write(lambda c: store.set_node_overrides(c, it.id, {"build": node}))
    (n,) = it.chain.chain.nodes

    status = await dispatch.dispatch_node(
        it.database, it.run_dirs, n.steps[0].tasks[0], n, it.row(), it.repo, escalate=escalate
    )

    assert status == "done"
    assert _models(fake_agent) == ["haiku", "sonnet"]
    assert all("Mind the tabs." in p for p in fake_agent.prompts())
    (switch,) = [e["payload"] for e in it.events("launch_fallback")]
    assert switch["override_not_carried"] is True


# -- budget: every launch is checked -------------------------------------------


async def test_a_fallback_launch_is_refused_by_the_budget(tmp_path, repo, fake_agent, monkeypatch):
    """The limited launch still reports its spend (the fake's $0.035), which
    spends a $0.03 task budget: the fallback is refused before it starts."""
    _limit(monkeypatch, "opus")

    status, evts, sessions, _ = await _walk(
        tmp_path,
        repo,
        _chain(fallback=[{"model": "sonnet"}], policy={"budget_usd": 0.03}),
    )

    assert status == "needs_human"
    assert [s["status"] for s in sessions] == ["rate_limited"]
    assert _models(fake_agent) == ["opus"]
    assert "scope_budget_reached" in [e["type"] for e in evts]
    (switch,) = _fallbacks(evts)
    assert switch["to"] is None
