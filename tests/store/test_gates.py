import pytest
from support.store_fixtures import mk_item

from kraft import events, store
from kraft.vocab import ChainEvent, GateEvent, WorkItemEvent
from kraft.vocab.stop import StopKind

REQ = (GateEvent.REQUESTED, {"gate": "g", "node_id": "g"})


def approved(by="human"):
    return (GateEvent.APPROVED, {"gate": "g", "by": by})


def rejected(note="redo", by="assistant", node="plan", **more):
    return (GateEvent.REJECTED, {"gate": "g", "note": note, "node": node, "by": by, **more})


def cap_stop():
    return (
        WorkItemEvent.NEEDS_HUMAN,
        {"node_id": "g", "kind": StopKind.CAP.value, "reason": "g exhausted"},
    )


async def rounds_of(database, evs):
    await mk_item(database)
    await database.write(lambda c: [events.append(c, "w1", t, p) for t, p in evs])
    return database.read(lambda c: store.gate_rounds(c, "w1"))


def project(r):
    return (
        r["n"],
        r["outcome"],
        r["by"],
        r["note"],
        r["sent_to"],
        r["reopened"],
        r["requested_at"] is None,
        r["ended_at"] is None,
    )


@pytest.mark.parametrize(
    ("evs", "expected"),
    [
        pytest.param(
            [REQ, approved()],
            [(1, "approved", "human", None, None, False, False, False)],
            id="approved-once",
        ),
        pytest.param(
            [REQ, rejected(), REQ, approved("assistant")],
            [
                (1, "rejected", "assistant", "redo", "plan", False, False, False),
                (2, "approved", "assistant", None, None, False, False, False),
            ],
            id="rejected-then-approved",
        ),
        pytest.param(
            [REQ, REQ, approved()],
            [(1, "approved", "human", None, None, False, False, False)],
            id="restart-repeats-the-request",
        ),
        pytest.param(
            [
                (GateEvent.CHAIN_REVISION_UNCHANGED, {"gate": "g", "rationale": "same"}),
                approved("kraft"),
            ],
            [(1, "approved", "kraft", None, None, False, True, False)],
            id="unchanged-revision-has-no-request",
        ),
        pytest.param(
            [rejected("must fix: x", by="kraft", verdict="review_threads")],
            [(1, "rejected", "kraft", "must fix: x", "plan", False, True, False)],
            id="bounce-rejects-before-the-ask",
        ),
        pytest.param(
            [REQ, rejected(verdict="fixed")],
            [(1, "fixed", "assistant", "redo", "plan", False, False, False)],
            id="fixed-verdict",
        ),
        pytest.param(
            [REQ, rejected(), cap_stop()],
            [(1, "rejected", "assistant", "redo", None, False, False, False)],
            id="cap-breach-sends-nothing-back",
        ),
        pytest.param(
            [
                REQ,
                rejected(),
                (
                    WorkItemEvent.NEEDS_HUMAN,
                    {"node_id": "g", "kind": StopKind.FAILED.value, "reason": "crashed"},
                ),
            ],
            [(1, "rejected", "assistant", "redo", "plan", False, False, False)],
            id="a-stop-that-is-not-the-cap-still-sent-work-back",
        ),
        pytest.param(
            [
                REQ,
                (ChainEvent.NODE_SKIPPED, {"node_id": "plan", "gate": "g", "note": "not needed"}),
            ],
            [(1, "skipped", None, "not needed", None, False, False, False)],
            id="skip-ends-the-round",
        ),
        pytest.param(
            [
                REQ,
                (ChainEvent.NODE_SKIPPED, {"node_id": "verification", "gate": None, "note": None}),
            ],
            [(1, "pending", None, None, None, False, False, True)],
            id="skip-of-a-node-with-no-gate-is-ignored",
        ),
        pytest.param(
            [
                REQ,
                rejected(),
                cap_stop(),
                (ChainEvent.NODE_SKIPPED, {"node_id": "g", "gate": None, "note": None}),
            ],
            [
                (1, "rejected", "assistant", "redo", None, False, False, False),
                (2, "skipped", None, None, None, False, True, False),
            ],
            id="skip-with-no-open-round",
        ),
        pytest.param(
            [REQ, (WorkItemEvent.COMPLETED, {"reason": "done"})],
            [(1, "closed", None, None, None, False, False, False)],
            id="completed-closes-a-pending-round",
        ),
        pytest.param(
            [REQ, approved(), (GateEvent.REOPENED, {"gate": "g", "reason": "retry"})],
            [(1, "approved", "human", None, None, True, False, False)],
            id="reopen-marks-the-round-before",
        ),
        pytest.param(
            [REQ, (GateEvent.REOPENED, {"gate": "g", "reason": "retry"})],
            [(1, "pending", None, None, None, False, False, True)],
            id="reopen-of-an-open-round-marks-nothing",
        ),
        pytest.param([REQ], [(1, "pending", None, None, None, False, False, True)], id="pending"),
        pytest.param(
            [
                (GateEvent.REQUESTED, {"gate": "h"}),
                (GateEvent.APPROVED, {"gate": "h", "by": "human"}),
            ],
            None,
            id="other-gates-ignored",
        ),
    ],
)
async def test_a_gates_rounds(database, evs, expected):
    found = await rounds_of(database, evs)
    if expected is None:
        assert "g" not in found
    else:
        assert [project(r) for r in found["g"]] == expected
    # A skip of a node with no gate is no gate's round.
    assert "verification" not in found
    assert None not in found


@pytest.mark.parametrize(
    ("evs", "expected"),
    [
        pytest.param(
            [
                REQ,
                (
                    GateEvent.AUTO_REVIEW_SKIPPED,
                    {"gate": "g", "reason": "passed", "note": "looks right"},
                ),
            ],
            [{"kind": "ready", "note": "looks right"}],
            id="passed",
        ),
        pytest.param(
            [
                REQ,
                (
                    GateEvent.AUTO_REVIEW_SKIPPED,
                    {"gate": "g", "reason": "undecided", "note": "unsure"},
                ),
            ],
            [{"kind": "undecided", "note": "unsure"}],
            id="undecided",
        ),
        pytest.param(
            [REQ, (GateEvent.AUTO_REVIEW_SKIPPED, {"gate": "g", "reason": "budget", "note": "n"})],
            [None],
            id="budget-is-no-verdict",
        ),
        pytest.param(
            [REQ, rejected("not good", by="agent")],
            [{"kind": "reject", "note": "not good"}],
            id="agent-rejects",
        ),
        pytest.param(
            [REQ, approved("agent")],
            [{"kind": "approve", "note": ""}],
            id="agent-approves-with-no-note",
        ),
        pytest.param(
            [REQ, approved("human")],
            [None],
            id="a-human-decision-is-no-reviewer",
        ),
        pytest.param(
            [
                REQ,
                (GateEvent.AUTO_REVIEW_SKIPPED, {"gate": "g", "reason": "passed", "note": "ok"}),
                rejected(),
                REQ,
            ],
            [{"kind": "ready", "note": "ok"}, None],
            id="a-verdict-stays-with-its-own-round",
        ),
        pytest.param(
            [
                REQ,
                (GateEvent.AUTO_REVIEW_SKIPPED, {"gate": "g", "reason": "passed", "note": "ok"}),
                (GateEvent.AUTO_REVIEW_SKIPPED, {"gate": "g", "reason": "undecided", "note": "?"}),
            ],
            [{"kind": "undecided", "note": "?"}],
            id="the-later-verdict-wins",
        ),
    ],
)
async def test_a_rounds_reviewer_verdict(database, evs, expected):
    found = await rounds_of(database, evs)
    assert [r["reviewer"] for r in found["g"]] == expected


def at(minute: int) -> str:
    return f"2026-11-10T10:{minute:02d}:00+00:00"


def req_at(minute):
    return (minute, REQ)


def end_at(minute, event):
    return (minute, event)


async def seeded(database, evs, threads=(), reviews=(), drafts=()):
    """Events, threads (minute, gate, label, state, body) and reviews (minute, gate,
    outcome, summary) at chosen minutes. A gate-less review stamps the non-draft threads
    as submitted; the draft threads come after it, so no review does."""
    await mk_item(database)

    def thread(c, spec):
        minute, gate, label, state, body = spec
        tid = store.create_thread(
            c, wid="w1", gate=gate, anchor_sha="s", body=body, file_path="a.py",
            start_line=3, end_line=4, side="new", label=label,
        )  # fmt: skip
        c.execute(
            "UPDATE review_threads SET created_at = ?, state = ? WHERE id = ?",
            (at(minute), state, tid),
        )
        c.execute(
            "UPDATE review_comments SET created_at = ? WHERE thread_id = ?", (at(minute), tid)
        )

    def write(c):
        for minute, (kind, payload) in evs:
            events.append(c, "w1", kind, payload)
            c.execute(
                "UPDATE events SET created_at = ? WHERE seq = "
                "(SELECT max(seq) FROM events WHERE work_item_id = 'w1')",
                (at(minute),),
            )
        for spec in threads:
            thread(c, spec)
        store.record_review(
            c, wid="w1", gate=None, outcome="comment", summary="", head_sha="h0", base_sha="b"
        )
        for minute, gate, outcome, summary in reviews:
            rid = store.record_review(
                c,
                wid="w1",
                gate=gate,
                outcome=outcome,
                summary=summary,
                head_sha="h1",
                base_sha="b",
            )
            c.execute("UPDATE reviews SET submitted_at = ? WHERE id = ?", (at(minute), rid))
        for spec in drafts:
            thread(c, spec)

    await database.write(write)
    return database.read(lambda c: store.gate_rounds(c, "w1"))["g"]


def contents(r):
    review = r["review"]
    return (
        review and (review["outcome"], review["summary"], review["head_sha"]),
        r["threads"],
        [t["body"] for t in r["first_threads"]],
    )


NONE = {"total": 0, "open": 0, "by_label": {"must_fix": 0, "question": 0, "nit": 0}}


def counts(total, open_, must_fix=0, question=0, nit=0):
    return {
        "total": total,
        "open": open_,
        "by_label": {"must_fix": must_fix, "question": question, "nit": nit},
    }


@pytest.mark.parametrize(
    ("evs", "threads", "reviews", "drafts", "expected"),
    [
        pytest.param(
            [req_at(1), end_at(5, rejected()), req_at(6), end_at(9, approved())],
            [
                (3, "g", "must_fix", "open", "fix x\nsecond line"),
                (4, "g", "question", "resolved", "why y"),
                (7, "g", "nit", "open", "later"),
            ],
            [(5, "g", "request_changes", "two things")],
            [],
            [
                (
                    ("request_changes", "two things", "h1"),
                    counts(2, 1, must_fix=1, question=1),
                    ["fix x", "why y"],
                ),
                (None, counts(1, 1, nit=1), ["later"]),
            ],
            id="a-review-and-threads-belong-to-the-round-they-were-made-in",
        ),
        pytest.param(
            [req_at(1), end_at(5, rejected()), req_at(8), end_at(9, approved())],
            [(6, "g", "nit", "open", "n1")],
            [(6, "g", "comment", "walk")],
            [],
            [
                (None, NONE, []),
                (("comment", "walk", "h1"), counts(1, 1, nit=1), ["n1"]),
            ],
            id="made-between-two-rounds-belongs-to-the-later",
        ),
        pytest.param(
            [end_at(5, rejected(by="kraft", verdict="review_threads"))],
            [(3, "g", "must_fix", "open", "m")],
            [],
            [],
            [(None, counts(1, 1, must_fix=1), ["m"])],
            id="a-round-with-no-request-still-has-a-window",
        ),
        pytest.param(
            [req_at(1), end_at(5, approved())],
            [(3, "other", "must_fix", "open", "o"), (3, None, "must_fix", "open", "n")],
            [(3, "other", "approve", "o")],
            [(3, "g", "must_fix", "open", "draft")],
            [(None, NONE, [])],
            id="drafts-and-other-gates-are-no-part-of-a-round",
        ),
        pytest.param(
            [req_at(1), end_at(9, approved())],
            [
                (2, "g", "nit", "open", "n1"),
                (3, "g", "question", "open", "q1"),
                (4, "g", "must_fix", "open", "m1"),
                (5, "g", "must_fix", "open", "m2"),
                (6, "g", "nit", "open", "n2"),
            ],
            [(4, "g", "comment", "old"), (8, "g", "request_changes", "newest")],
            [],
            [
                (
                    ("request_changes", "newest", "h1"),
                    counts(5, 5, must_fix=2, question=1, nit=2),
                    ["m1", "m2", "n1"],
                )
            ],
            id="first-three-are-must-fix-first-then-oldest-and-the-newest-review-wins",
        ),
        pytest.param(
            [req_at(1), end_at(5, rejected()), req_at(6), end_at(9, approved())],
            [(3, "g", "must_fix", "resolved", "m")],
            [],
            [],
            [(None, counts(1, 0, must_fix=1), ["m"]), (None, NONE, [])],
            id="a-thread-shows-its-state-now-on-the-round-it-was-opened-in",
        ),
        pytest.param(
            [req_at(1), end_at(9, approved())],
            [(3, "g", "must_fix", "claimed", "m")],
            [],
            [],
            [(None, counts(1, 1, must_fix=1), ["m"])],
            id="a-claimed-thread-is-still-open",
        ),
        pytest.param(
            [req_at(1), end_at(9, approved())],
            [],
            [(5, "g", "comment", None)],
            [],
            [(("comment", "", "h1"), NONE, [])],
            id="a-review-with-no-summary-has-an-empty-one",
        ),
        pytest.param(
            [req_at(1), end_at(9, approved())],
            [(3, "g", None, "open", "u"), (4, "g", "nit", "open", "n")],
            [],
            [],
            [(None, counts(2, 2, nit=1), ["u", "n"])],
            id="an-unlabelled-thread-counts-in-the-total-only",
        ),
    ],
)
async def test_a_rounds_review_and_threads(database, evs, threads, reviews, drafts, expected):
    rounds = await seeded(database, evs, threads, reviews, drafts)
    assert [contents(r) for r in rounds] == expected


@pytest.mark.parametrize(
    ("evs", "expected"),
    [
        pytest.param(
            [rejected("must fix: x", by="kraft", verdict="review_threads")],
            ["review_threads"],
            id="the-thread-bounce-names-itself",
        ),
        pytest.param([REQ, rejected(by="human")], [None], id="a-plain-rejection-has-none"),
        pytest.param(
            [REQ, rejected(verdict="fixed"), REQ, approved()],
            ["fixed", None],
            id="an-approval-has-none",
        ),
    ],
)
async def test_a_rejections_verdict_rides_on_its_round(database, evs, expected):
    found = await rounds_of(database, evs)
    assert [r["verdict"] for r in found["g"]] == expected
