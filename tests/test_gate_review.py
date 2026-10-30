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

from kraft import events, executor, gate_review, inplace, item_paths, store
from kraft import policy as _policy
from kraft.executor import gates
from kraft.templates.models import CHECKS_FIRST


def _no_edits(monkeypatch):
    """A faked run has no checkout to compare: it reads as one nobody edited."""
    monkeypatch.setattr(gate_review.read_only, "snapshot", lambda *a, **kw: {})
    monkeypatch.setattr(gate_review.read_only, "changed", lambda *a, **kw: [])


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

#: A copy of an in-place item's branch has no identity of its own to commit as.
_ID = "git -c user.email=t@t -c user.name=t"
_COMMIT = f"echo x > new.txt && git add new.txt && {_ID} commit -qm reviewer"
_REJECT = {"verdict": "reject", "concerns": "redo it"}
_PASS = {"verdict": "pass", "concerns": "read it all"}
_DOC = "# the review\n"


async def _at_gate(
    database, run_dirs, tmp_path, repo, edit, result, *, mode, in_place, doc=_DOC, artifact="review"
):
    """An item at a reviewed gate whose reviewer runs `edit` in its checkout,
    then reports `result`. The gate decides its `artifact` document (None: it
    has none), which reads `doc` (None: not written yet). In place its
    branch is made, with a spec attached, and the person's checkout stays on
    `main`; otherwise it is in its own worktree. Returns the gate."""
    script = (
        f"{edit}\nprintf '%s' '{json.dumps({'status': 'done', **result})}' > \"$KRAFT_RESULT_PATH\""
    )
    # A harness that cannot deny tools still runs a checks-first reviewer: the
    # snapshot, not a tool denial, is what catches an edit.
    fake_harness_home(tmp_path, ["sh", "-c", script])
    keys = {
        **({"artifact": artifact} if artifact else {}),
        **({"auto_review_mode": mode} if mode else {}),
    }
    _doc(run_dirs).parent.mkdir(parents=True, exist_ok=True)
    if doc is not None:
        _doc(run_dirs).write_text(doc)
    chain = _chain2(run_dirs, target=_IN_PLACE if in_place else None, harness="fake", **keys)
    await _seed_at_gate(database, run_dirs, "w1", chain, auto_gate=True, repo=repo)
    if in_place:
        spec = json.dumps([_spec(tmp_path)])
        await database.write(lambda c: c.execute("UPDATE work_items SET attachments = ?", (spec,)))
        await inplace.prepare(database, run_dirs, _row(database), base="main")
    else:
        await ensure(database, run_dirs, repo)
    return chain.chain.nodes[1]


def _doc(run_dirs):
    return item_paths.docs_dir(run_dirs, "w1") / "review.md"


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

    def snapshot(*a, **kw):
        calls.append("snapshot")
        return real(*a, **kw) if result is ... else result

    monkeypatch.setattr(gate_review.read_only, "snapshot", snapshot)


def _copies(run_dirs):
    """What is left in any session's copy of the branch."""
    return list((run_dirs.base / item_paths.ITEMS / "w1" / "sessions").glob("*/copy/*"))


def _types(database):
    return [e["type"] for e in database.read(lambda c: events.read_after(c, 0, "w1"))]


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
#: An old-place document, there before the review in place of `doc`.
_LEGACY = object()


@pytest.mark.parametrize(
    "mode,doc,edit,result,expected",
    [
        pytest.param(
            CHECKS_FIRST,
            _DOC,
            _COMMIT,
            _REJECT,
            ("undecided", _IGNORED + "new.txt"),
            id="reject-commits",
        ),
        pytest.param(
            CHECKS_FIRST,
            _DOC,
            _COMMIT,
            _PASS,
            ("undecided", _IGNORED + "new.txt"),
            id="pass-commits",
        ),
        pytest.param(
            CHECKS_FIRST,
            _DOC,
            "echo x > new.txt",
            _REJECT,
            ("undecided", _IGNORED + "new.txt"),
            id="reject-leaves-dirty",
        ),
        pytest.param(
            CHECKS_FIRST,
            _DOC,
            "echo x > new.txt",
            _PASS,
            ("undecided", _IGNORED + "new.txt"),
            id="pass-leaves-dirty",
        ),
        # Sorted, so f3 is the twentieth.
        pytest.param(
            CHECKS_FIRST,
            _DOC,
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
        pytest.param(CHECKS_FIRST, _DOC, ":", _PASS, ("pass", "read it all"), id="no-edit"),
        # Outside the checkout, so git never sees it.
        pytest.param(
            CHECKS_FIRST,
            _DOC,
            'echo x > "$KRAFT_DOCS_DIR/review.md"',
            _PASS,
            ("undecided", _IGNORED + "{doc}"),
            id="rewrites-the-gate-document",
        ),
        pytest.param(
            CHECKS_FIRST,
            None,
            'echo x > "$KRAFT_DOCS_DIR/review.md"',
            _PASS,
            ("undecided", _IGNORED + "{doc}"),
            id="creates-the-gate-document",
        ),
        # Where an item from before the upgrade kept it: read there when the new
        # place has none, and unseen by git in a repo that ignores the folder.
        pytest.param(
            CHECKS_FIRST,
            None,
            "mkdir -p .engineering/reviews && echo x > .engineering/reviews/w1.md",
            _PASS,
            ("undecided", _IGNORED + "{legacy}"),
            id="creates-an-old-place-gate-document",
        ),
        # The new place is read first, so writing there replaces the old one.
        pytest.param(
            CHECKS_FIRST,
            _LEGACY,
            'echo x > "$KRAFT_DOCS_DIR/review.md"',
            _PASS,
            ("undecided", _IGNORED + "{doc}"),
            id="shadows-an-old-place-gate-document",
        ),
        # Hashed whole, not as far as a person's read of it goes.
        pytest.param(
            CHECKS_FIRST,
            "x" * (item_paths.DOC_MAX_BYTES + 10),
            f'printf y | dd of="$KRAFT_DOCS_DIR/review.md" bs=1 seek={item_paths.DOC_MAX_BYTES + 5}'
            " conv=notrunc 2>/dev/null",
            _PASS,
            ("undecided", _IGNORED + "{doc}"),
            id="edits-past-the-read-limit",
        ),
        # No longer a file it can read without a link: changed all the same.
        pytest.param(
            CHECKS_FIRST,
            _DOC,
            'ln -sf /dev/null "$KRAFT_DOCS_DIR/review.md"',
            _PASS,
            ("undecided", _IGNORED + "{doc}"),
            id="links-the-gate-document",
        ),
        # A reviewer that decides may edit (`fixed`): no check, no "before" read.
        pytest.param(
            None,
            _DOC,
            _COMMIT,
            {"verdict": "approve"},
            ("approve", ""),
            id="decides-is-not-checked",
        ),
    ],
)
async def test_checks_first_edits_void_the_verdict(
    database, run_dirs, tmp_path, repo, monkeypatch, mode, doc, edit, result, expected
):
    gate = await _at_gate(
        database,
        run_dirs,
        tmp_path,
        repo,
        edit,
        result,
        mode=mode,
        in_place=False,
        doc=None if doc is _LEGACY else doc,
    )
    # As Kraft's own repo does: an old-place document is no file git sees.
    with (repo / ".git" / "info" / "exclude").open("a") as fh:
        fh.write(".engineering/\n")
    legacy = item_paths.path_of(run_dirs, "w1", item_paths.legacy_document_name("w1", "review"))
    if doc is _LEGACY:
        legacy.parent.mkdir(parents=True)
        legacy.write_text(_DOC)
    reads = []
    _spy_snapshots(monkeypatch, reads)

    verdict, note = await _run(database, run_dirs, gate)
    assert (verdict, note) == (expected[0], expected[1].format(doc=_doc(run_dirs), legacy=legacy))
    assert reads == (["snapshot"] if mode else [])


async def test_checks_first_gate_with_no_document_checks_the_checkout(
    database, run_dirs, tmp_path, repo, monkeypatch
):
    gate = await _at_gate(
        database,
        run_dirs,
        tmp_path,
        repo,
        "echo x > new.txt",
        _PASS,
        mode=CHECKS_FIRST,
        in_place=False,
        artifact=None,
    )
    assert await _run(database, run_dirs, gate) == ("undecided", _IGNORED + "new.txt")


_IN_PLACE_IGNORED = (
    "The reviewer changed files in its copy of the branch, so its verdict was ignored; "
    "Kraft removed the copy and left your working files as they were: "
)
_TIP_MOVED = "The item's branch moved during the review, so the verdict was ignored: "
_PERSONS = "# the person's edit\n"
#: A commit on the person's branch by a route around the copy, which it then undoes.
_PUSH = (
    f"echo x > tip.txt && git add tip.txt && {_ID} commit -qm tip"
    " && git push -q {repo} HEAD:refs/heads/kraft/the-widget-w1 && git reset -q --hard HEAD~1"
)


@pytest.mark.parametrize(
    "dirty,edit,expected",
    [
        pytest.param(False, ":", ("pass", "read it all"), id="in-place-copy-no-edit"),
        pytest.param(
            False,
            _COMMIT,
            ("undecided", _IN_PLACE_IGNORED + "new.txt"),
            id="in-place-commits-in-the-copy",
        ),
        pytest.param(
            True,
            "echo agent >> calc.py",
            ("undecided", _IN_PLACE_IGNORED + "calc.py"),
            id="in-place-edits-a-file-the-person-had-dirty",
        ),
        pytest.param(True, ":", ("pass", "read it all"), id="in-place-person-mid-work"),
        pytest.param(
            False,
            _PUSH,
            ("undecided", _TIP_MOVED + "tip.txt"),
            id="in-place-moves-the-branch-tip",
        ),
        pytest.param(
            False,
            _PUSH + " && echo agent >> calc.py",
            ("undecided", _IN_PLACE_IGNORED + "calc.py. " + _TIP_MOVED + "tip.txt"),
            id="in-place-edits-the-copy-and-moves-the-branch-tip",
        ),
    ],
)
async def test_an_in_place_checks_first_reviewer_reads_a_copy_of_the_branch(
    database, run_dirs, tmp_path, repo, monkeypatch, dirty, edit, expected
):
    """Never the person's checkout: it is not switched, their uncommitted work
    is not touched, and the copy is gone after. A write in the copy, or a
    commit on their branch, voids the verdict without stopping the item, and
    the note says which: a moved branch may be the person's own commit."""
    gate = await _at_gate(
        database,
        run_dirs,
        tmp_path,
        repo,
        edit.replace("{repo}", str(repo)),
        _PASS,
        mode=CHECKS_FIRST,
        in_place=True,
    )
    if dirty:
        write(repo, "calc.py", _PERSONS)
    status = _row(database)["status"]
    branch = f"refs/heads/{store.branch_for(_row(database))}"
    tip = git(repo, "rev-parse", branch)
    reads = []
    _spy_snapshots(monkeypatch, reads)

    assert await _run(database, run_dirs, gate) == expected
    assert reads == ["snapshot", "snapshot"]
    assert git(repo, "symbolic-ref", "HEAD") == "refs/heads/main"
    assert git(repo, "status", "--porcelain") == ("M calc.py" if dirty else "")
    assert ((repo / "calc.py").read_text() == _PERSONS) == dirty
    assert (git(repo, "rev-parse", branch) == tip) == ("tip.txt" not in expected[1])
    assert "checkout_switched" not in _types(database)
    assert _row(database)["status"] == status
    assert _copies(run_dirs) == []


async def test_an_in_place_review_whose_copy_fails_is_undecided(database, run_dirs, tmp_path, repo):
    """The person deleted the item's branch: no reviewer runs anywhere, the
    attempt counts, the item is not stopped and the branch is not made again."""
    ran = tmp_path / "ran"
    gate = await _at_gate(
        database, run_dirs, tmp_path, repo, f"touch {ran}", _PASS, mode=CHECKS_FIRST, in_place=True
    )
    status = _row(database)["status"]
    branch = store.branch_for(_row(database))
    git(repo, "branch", "-D", branch)

    verdict, note = await _run(database, run_dirs, gate)

    assert verdict == "undecided"
    assert note.startswith(f"Kraft could not make a copy of {branch} to work in: "), note
    assert not ran.exists()
    assert _types(database).count("gate_auto_review_started") == 1
    assert _sessions(database) == ["config_error"]
    assert _row(database)["status"] == status
    assert git(repo, "for-each-ref", f"refs/heads/{branch}") == ""
    assert _copies(run_dirs) == []


_TOLD_TO_EDIT = ("Commit everything you change", "Run the tests your change touches")


@pytest.mark.parametrize(
    "mode,in_place,told",
    [
        pytest.param(CHECKS_FIRST, False, False, id="checks-first"),
        pytest.param(CHECKS_FIRST, True, False, id="checks-first-in-place"),
        pytest.param(None, False, True, id="decides"),
    ],
)
async def test_only_a_reviewer_that_may_edit_is_told_to_commit_and_test(
    database, run_dirs, tmp_path, repo, monkeypatch, mode, in_place, told
):
    """Its own instruction says do not edit; the session's context must not
    then say commit, or run tests that can leave files behind. In place it is
    told it works in a throwaway copy of the branch, never the person's checkout."""
    argv = tmp_path / "argv.txt"
    gate = await _at_gate(
        database,
        run_dirs,
        tmp_path,
        repo,
        f"printf '%s' \"$*\" > {argv}",
        _PASS,
        mode=mode,
        in_place=in_place,
    )
    await _run(database, run_dirs, gate)
    context = argv.read_text()
    assert "You are working on a Kraft work item." in context
    assert [line in context for line in _TOLD_TO_EDIT] == [told, told]
    assert ("throwaway copy of this work item's branch" in context) == in_place
    assert "`git stash`" not in context
    # The read-only paragraph ends the contract: one blank line, not two, before the next.
    after = context.partition("Kraft compares it after you.")[2]
    assert ("is kept." in after) == in_place
    assert not after.partition("is kept.")[2 if in_place else 0].startswith("\n\n\n")


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
        f"touch {tmp_path / 'ran'}",
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
    assert (_sessions(database), (tmp_path / "ran").exists()) == (sessions, ran)
    assert _copies(run_dirs) == []


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
