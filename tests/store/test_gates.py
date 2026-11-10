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
