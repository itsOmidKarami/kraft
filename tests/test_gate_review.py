"""`gate_review.review` for a gate whose reviewer only checks first
(`auto_review_mode: checks_first`): it may reject, or hand the gate to the
person with `pass`, and never approve.

Beside `tests/skills/test_gate_review.py`, which is at its line budget; its
helpers are reused, and `run_agent_task` is monkeypatched the same way.
"""

from __future__ import annotations

import json

import pytest
from inplace.test_taking import _spec
from skills.test_gate_review import (  # noqa: F401
    _IN_PLACE,
    _chain2,
    _claude_profile,
    _fake_agent,
    _gate,
    _passthrough_approve,
    _review_from_gate,
    _seed,
    _seed_at_gate,
    _stub_review,
    _stub_walk,
)
from support.harness import fake_harness_home, git, write
from support.worktree import ensure

from kraft import events, executor, gate_review, inplace, store
from kraft import policy as _policy
from kraft.executor import gates
from kraft.templates.models import CHECKS_FIRST


def _no_edits(monkeypatch):
    """A faked run has no checkout to compare: it reads as one nobody edited."""
    monkeypatch.setattr(gate_review.read_only, "snapshot", lambda *a: {})
    monkeypatch.setattr(gate_review.read_only, "changed", lambda *a: [])


async def _review(monkeypatch, database, run_dirs, result, *, mode=CHECKS_FIRST):
    """One review of `_seed`'s gate, its reviewer in `mode` (None: unset,
    which is `decides`). Returns `(verdict, note, seen)`."""
    seen = {}
    _no_edits(monkeypatch)
    monkeypatch.setattr("kraft.gate_review._agent.run_agent_task", _fake_agent(result, seen))
    await _seed(database, run_dirs, "w1")
    verdict, note = await gate_review.review(
        database,
        run_dirs,
        work_item_id="w1",
        gate="spec_approval",
        node=_gate(run_dirs, **({"auto_review_mode": mode} if mode else {})),
        launch=executor.LaunchContext(repo_entry=None, skills_dir=None),
    )
    return verdict, note, seen


@pytest.mark.parametrize(
    "mode,result,expected",
    [
        # The reviewer meant "good"; the person still decides.
        pytest.param(
            CHECKS_FIRST, {"status": "done", "verdict": "approve"}, "pass", id="approve-is-pass"
        ),
        pytest.param(
            CHECKS_FIRST, {"verdict": "pass", "concerns": "read it all"}, "pass", id="pass"
        ),
        pytest.param(CHECKS_FIRST, {"verdict": "pass"}, "pass", id="pass-without-concerns"),
        pytest.param(
            CHECKS_FIRST,
            {"verdict": "reject", "concerns": "redo the migration"},
            "reject",
            id="reject",
        ),
        pytest.param(
            CHECKS_FIRST,
            {"verdict": "reject", "concerns": ""},
            "undecided",
            id="reject-without-concerns",
        ),
        # It may not edit, so it has nothing to have fixed.
        pytest.param(
            CHECKS_FIRST, {"verdict": "fixed", "concerns": "fixed it"}, "undecided", id="fixed"
        ),
        pytest.param(CHECKS_FIRST, {"verdict": "undecided"}, "undecided", id="undecided"),
        pytest.param(CHECKS_FIRST, {"verdict": "APPROVE!"}, "undecided", id="unknown"),
        pytest.param(
            CHECKS_FIRST,
            {"status": "failed", "verdict": "pass"},
            "undecided",
            id="failed-session",
        ),
        pytest.param(CHECKS_FIRST, None, "undecided", id="no-result"),
        # `pass` exists only for a reviewer that checks first.
        pytest.param(
            None, {"status": "done", "verdict": "pass"}, "undecided", id="decides-has-no-pass"
        ),
    ],
)
async def test_checks_first_verdict_resolution(
    monkeypatch, database, run_dirs, mode, result, expected
):
    verdict, note, _seen = await _review(monkeypatch, database, run_dirs, result, mode=mode)
    assert (verdict, note) == (expected, (result or {}).get("concerns", ""))


@pytest.mark.parametrize(
    "mode,offered,withheld",
    [
        pytest.param(
            CHECKS_FIRST,
            ('"verdict": "reject"', '"verdict": "pass"'),
            ('"verdict": "approve"', '"verdict": "fixed"'),
            id="checks-first",
        ),
        pytest.param(
            None,
            ('"verdict": "approve"', '"verdict": "fixed"', '"verdict": "reject"'),
            ('"verdict": "pass"',),
            id="decides",
        ),
    ],
)
async def test_checks_first_instruction_offers_only_reject_and_pass(
    monkeypatch, database, run_dirs, mode, offered, withheld
):
    *_, seen = await _review(
        monkeypatch, database, run_dirs, {"verdict": "pass", "concerns": "ok"}, mode=mode
    )
    instruction = seen["kwargs"]["task_instruction"]
    assert all(v in instruction for v in offered), instruction
    assert not any(v in instruction for v in withheld), instruction


# -- a reviewer that edits the work ------------------------------------------------------
# These run the real `run_agent_task` on a fake harness in a real checkout:
# when the "before" is read is the whole point, and `_fake_agent` skips it.

_COMMIT = "echo x > new.txt && git add new.txt && git commit -qm reviewer"
_REJECT = {"verdict": "reject", "concerns": "redo it"}
_PASS = {"verdict": "pass", "concerns": "read it all"}


async def _at_gate(database, run_dirs, tmp_path, repo, edit, result, *, mode, in_place):
    """An item at a reviewed gate whose reviewer runs `edit` in its checkout,
    then reports `result`. In place it is on its first take, with a spec to
    commit; otherwise in its own worktree. Returns the gate."""
    script = (
        f"{edit}\nprintf '%s' '{json.dumps({'status': 'done', **result})}' > \"$KRAFT_RESULT_PATH\""
    )
    # A harness that cannot deny tools still runs a checks-first reviewer: the
    # snapshot, not a tool denial, is what catches an edit.
    fake_harness_home(tmp_path, ["sh", "-c", script])
    keys = {"auto_review_mode": mode} if mode else {}
    chain = _chain2(run_dirs, target=_IN_PLACE if in_place else None, harness="fake", **keys)
    await _seed_at_gate(database, run_dirs, "w1", chain, auto_gate=True, repo=repo)
    if in_place:
        spec = json.dumps([_spec(tmp_path)])
        await database.write(lambda c: c.execute("UPDATE work_items SET attachments = ?", (spec,)))
        await inplace.prepare(database, run_dirs, _row(database), base="main")
    else:
        await ensure(database, run_dirs, repo)
    return chain.chain.nodes[1]


def _row(database):
    return database.read(lambda c: c.execute("SELECT * FROM work_items").fetchone())


def _run(database, run_dirs, gate):
    return gate_review.review(
        database,
        run_dirs,
        work_item_id="w1",
        gate="human_review_approval",
        node=gate,
        launch=executor.LaunchContext(repo_entry=None, skills_dir=None),
    )


def _spy_snapshots(monkeypatch, calls: list, *, result=...):
    """Record each "before" read (`result`, if given, stands in for it)."""
    real = gate_review.read_only.snapshot

    def snapshot(*a):
        calls.append("snapshot")
        return real(*a) if result is ... else result

    monkeypatch.setattr(gate_review.read_only, "snapshot", snapshot)


def _sessions(database):
    return [
        r["status"]
        for r in database.read(lambda c: c.execute("SELECT status FROM worker_sessions").fetchall())
    ]


_IGNORED = (
    "The reviewer changed files, so its verdict was ignored; its changes are still in the "
    "worktree: "
)
_25 = "for i in $(seq 25); do echo x > f$i.txt; done"


@pytest.mark.parametrize(
    "mode,in_place,edit,result,expected",
    [
        pytest.param(
            CHECKS_FIRST,
            False,
            _COMMIT,
            _REJECT,
            ("undecided", _IGNORED + "new.txt"),
            id="reject-commits",
        ),
        pytest.param(
            CHECKS_FIRST,
            False,
            _COMMIT,
            _PASS,
            ("undecided", _IGNORED + "new.txt"),
            id="pass-commits",
        ),
        pytest.param(
            CHECKS_FIRST,
            False,
            "echo x > new.txt",
            _REJECT,
            ("undecided", _IGNORED + "new.txt"),
            id="reject-leaves-dirty",
        ),
        pytest.param(
            CHECKS_FIRST,
            False,
            "echo x > new.txt",
            _PASS,
            ("undecided", _IGNORED + "new.txt"),
            id="pass-leaves-dirty",
        ),
        # Sorted, so f3 is the twentieth.
        pytest.param(
            CHECKS_FIRST,
            False,
            _25,
            _PASS,
            (
                "undecided",
                _IGNORED
                + ", ".join(sorted(f"f{i}.txt" for i in range(1, 26))[:20])
                + " and 5 more",
            ),
            id="names-at-most-twenty",
        ),
        pytest.param(CHECKS_FIRST, False, ":", _PASS, ("pass", "read it all"), id="no-edit"),
        # The first take commits the spec on the branch: Kraft's, not the reviewer's.
        pytest.param(
            CHECKS_FIRST,
            True,
            ":",
            _PASS,
            ("pass", "read it all"),
            id="in-place-first-take-is-not-an-edit",
        ),
        pytest.param(
            CHECKS_FIRST,
            True,
            _COMMIT,
            _PASS,
            ("undecided", _IGNORED + "new.txt"),
            id="in-place-commits",
        ),
        # A reviewer that decides may edit (`fixed`): no check, no "before" read.
        pytest.param(
            None,
            False,
            _COMMIT,
            {"verdict": "approve"},
            ("approve", ""),
            id="decides-is-not-checked",
        ),
    ],
)
async def test_checks_first_edits_void_the_verdict(
    database, run_dirs, tmp_path, repo, monkeypatch, mode, in_place, edit, result, expected
):
    gate = await _at_gate(
        database, run_dirs, tmp_path, repo, edit, result, mode=mode, in_place=in_place
    )
    reads = []
    _spy_snapshots(monkeypatch, reads)

    assert await _run(database, run_dirs, gate) == expected
    assert reads == (["snapshot"] if mode else [])


@pytest.mark.parametrize(
    "in_place,refused,sessions,ran,unreadable",
    [
        # Refused before launch, as a session of its own: nothing paid for.
        pytest.param(False, False, ["config_error"], False, True, id="worktree-launches-nothing"),
        # The hook cannot refuse a launch under way: the verdict is ignored.
        pytest.param(True, False, ["done"], True, True, id="in-place-verdict-ignored"),
        # Refused before the checkout was read (a machine's refusal): no review, no read to blame.
        pytest.param(True, True, [], False, False, id="in-place-refused-before-the-read"),
    ],
)
async def test_checks_first_unreadable_checkout_is_never_clean(
    database, run_dirs, tmp_path, repo, monkeypatch, in_place, refused, sessions, ran, unreadable
):
    gate = await _at_gate(
        database,
        run_dirs,
        tmp_path,
        repo,
        "touch ran",
        _PASS,
        mode=CHECKS_FIRST,
        in_place=in_place,
    )
    _spy_snapshots(monkeypatch, [], result=None)
    if refused:
        fake = _fake_agent({"status": "config_error"}, {})
        monkeypatch.setattr(gate_review._agent, "run_agent_task", fake)

    verdict, note = await _run(database, run_dirs, gate)

    assert (verdict, "could not read the checkout" in note) == ("undecided", unreadable), note
    ran_in = inplace.item_folder(run_dirs, _row(database))
    assert (_sessions(database), (ran_in / "ran").exists()) == (sessions, ran)


async def test_checks_first_snapshots_a_worktree_item_before_its_session_exists(
    database, run_dirs, tmp_path, repo, monkeypatch
):
    """Read any later, the reviewer's own pending session counts as live, and
    on a sandboxed item the live-session guard refuses every read."""
    gate = await _at_gate(
        database, run_dirs, tmp_path, repo, ":", _PASS, mode=CHECKS_FIRST, in_place=False
    )
    order = []
    _spy_snapshots(monkeypatch, order)
    create_session = store.create_session

    def created(*a, **kw):
        order.append("session")
        return create_session(*a, **kw)

    monkeypatch.setattr(store, "create_session", created)

    assert await _run(database, run_dirs, gate) == ("pass", "read it all")
    assert order == ["snapshot", "session"]


async def test_checks_first_compares_a_remote_review_on_the_servers_worktree(
    database, run_dirs, tmp_path, repo, monkeypatch
):
    """A remote run's commits are fetched into the server's worktree before its
    session ends (`remote.runs._publish`), so the same comparison catches them."""
    gate = await _at_gate(
        database, run_dirs, tmp_path, repo, ":", _PASS, mode=CHECKS_FIRST, in_place=False
    )
    monkeypatch.setattr(gate_review._placement, "runner_for", lambda *a: "m1")
    seen = {}
    fake = _fake_agent(_PASS, seen)

    async def remote_run(db, rd, **kw):
        assert kw["runner"] == "m1"
        write(kw["cwd"], "new.txt", "x\n")
        git(kw["cwd"], "add", "new.txt")
        git(kw["cwd"], "commit", "-qm", "from the machine")
        return await fake(db, rd, **kw)

    monkeypatch.setattr(gate_review._agent, "run_agent_task", remote_run)

    assert await _run(database, run_dirs, gate) == ("undecided", _IGNORED + "new.txt")


_GATE = "human_review_approval"


def _skips(database):
    return [
        e["payload"]
        for e in database.read(lambda c: events.read_after(c, 0, "w1"))
        if e["type"] == "gate_auto_review_skipped"
    ]


@pytest.mark.parametrize(
    "verdict,note,status,walked,skip",
    [
        # However `review()` got it wrong, nothing but a reject moves the gate.
        pytest.param("approve", "", "awaiting_gate", [], {"reason": "passed"}, id="approve"),
        pytest.param(
            "pass",
            "read it",
            "awaiting_gate",
            [],
            {"reason": "passed", "note": "read it"},
            id="pass",
        ),
        pytest.param(
            "fixed",
            "tidied",
            "awaiting_gate",
            [],
            {"reason": "undecided", "note": "tidied"},
            id="fixed",
        ),
        pytest.param("reject", "redo it", "completed", [(0, "redo it")], None, id="reject"),
    ],
)
async def test_checks_first_never_clears_the_gate(
    monkeypatch, database, run_dirs, verdict, note, status, walked, skip
):
    calls = []
    _stub_review(monkeypatch, verdict, note)
    _stub_walk(monkeypatch, calls)
    chain = _chain2(run_dirs, auto_review_mode=CHECKS_FIRST)

    assert await _review_from_gate(database, run_dirs, chain, auto_gate=True) == status
    assert calls == walked
    assert _skips(database)[-1:] == ([{"gate": _GATE, **skip}] if skip else [])
    types = [e["type"] for e in database.read(lambda c: events.read_after(c, 0, "w1"))]
    assert "gate_approved" not in types


async def test_a_pass_ends_the_round(monkeypatch, database, run_dirs):
    """A pass spends the request's remaining attempts: no second paid review of
    work the person is reading. A new request, or a run boundary, starts fresh."""
    _stub_review(monkeypatch, "pass", "")
    _stub_walk(monkeypatch, [])
    policy = _policy.Policy(
        loops={}, default=_policy.Cap(attempts=2, wall_clock_s=3600), auto_review_attempts=2
    )
    chain = _chain2(run_dirs, auto_review_mode=CHECKS_FIRST)
    await _seed_at_gate(database, run_dirs, "w1", chain, auto_gate=True)
    status = await executor.review_gates(
        "awaiting_gate",
        database,
        run_dirs,
        work_item_id="w1",
        policy=policy,
        launch=executor.LaunchContext(repo_entry=None, skills_dir=None),
        on_approve=_passthrough_approve,
    )
    assert status == "awaiting_gate"

    def due():
        evts = database.read(lambda c: events.read_after(c, 0, "w1"))
        return gates.auto_check_due(_row(database), _GATE, evts, policy)

    assert not due()
    for later, payload in [("gate_requested", {"gate": _GATE}), ("work_item_resumed", {})]:
        await database.write(lambda c, t=later, p=payload: events.append(c, "w1", t, p))
        assert due(), later
        await database.write(
            lambda c: events.append(
                c, "w1", "gate_auto_review_skipped", {"gate": _GATE, "reason": "passed"}
            )
        )
        assert not due(), later
