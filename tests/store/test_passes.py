"""A node's passes: each time the chain runs it again, numbered from what the
event log says sent it back."""

import pytest

from kraft.store.passes import number_passes

ORDER = ["spec", "impl", "verify", "gate", "ship"]


def _s(at, node="verify", round=0, task="main.test"):
    return {
        "id": f"s{at}",
        "node_id": node,
        "hook_point": task if task == "escalation" else f"{node}.{task}",
        "round": round,
        "created_at": f"t{at:02}",
    }


def _e(at, type, **payload):
    return {"created_at": f"t{at:02}", "type": type, "payload": payload}


REJECT = _e(5, "gate_rejected", gate="gate", node="impl")

CASES = [
    pytest.param([_s(1), _s(2, round=1)], [], [1, 1], [{"pass": 1}], id="one-pass"),
    pytest.param(
        [_s(1), _s(2, round=1), _s(6, round=1)],
        [REJECT],
        [1, 1, 2],
        [{"pass": 1}, {"pass": 2, "reason": "reject", "gate": "gate"}],
        id="reject-at-the-round-it-left-off",
    ),
    pytest.param(
        [_s(1, "spec"), _s(6, "spec")],
        [REJECT],
        [1, 1],
        [{"pass": 1}],
        id="reject-re-entered-after-this-node",
    ),
    pytest.param(
        [_s(1, "ship"), _s(6, "ship")],
        [REJECT],
        [1, 1],
        [{"pass": 1}],
        id="reject-at-a-gate-before-this-node",
    ),
    pytest.param(
        [_s(1, "gate", task="auto_review"), _s(6, "gate", task="auto_review")],
        [REJECT],
        [None, None],
        None,
        id="the-gates-own-reviewer",
    ),
    pytest.param([_s(6)], [REJECT], [1], [{"pass": 1}], id="nothing-ran-before-it"),
    pytest.param(
        [_s(1), _s(8)],
        [REJECT, _e(7, "run_forked", scope="node", path="verify")],
        [1, 2],
        [{"pass": 1}, {"pass": 2, "reason": "retry"}],
        id="two-boundaries-and-nothing-between-are-one-pass",
    ),
    pytest.param(
        [_s(1), _s(6), _s(9)],
        [REJECT, _e(8, "gate_rejected", gate="gate", node="verify")],
        [1, 2, 3],
        [
            {"pass": 1},
            {"pass": 2, "reason": "reject", "gate": "gate"},
            {"pass": 3, "reason": "reject", "gate": "gate"},
        ],
        id="rejected-twice",
    ),
    pytest.param(
        [_s(1), _s(6)],
        [_e(5, "run_forked", scope="node", path="impl")],
        [1, 2],
        [{"pass": 1}, {"pass": 2, "reason": "retry"}],
        id="retry-of-an-earlier-node",
    ),
    pytest.param(
        [_s(1), _s(6)],
        [_e(5, "run_forked", scope="work_item", path=None)],
        [1, 2],
        [{"pass": 1}, {"pass": 2, "reason": "retry"}],
        id="restart-of-the-item",
    ),
    pytest.param(
        [_s(1), _s(6)],
        [_e(5, "run_forked", scope="task", path="verify.main.test")],
        [1, 1],
        [{"pass": 1}],
        id="retry-of-one-task-is-another-attempt",
    ),
    pytest.param(
        [_s(1), _s(6)],
        [_e(5, "run_forked", scope="node", path="ship")],
        [1, 1],
        [{"pass": 1}],
        id="retry-of-a-later-node",
    ),
    pytest.param(
        [_s(1), _s(6)],
        [_e(5, "base_change_restart", nodes=["impl", "verify"])],
        [1, 2],
        [{"pass": 1}, {"pass": 2, "reason": "base_change"}],
        id="base-change-restart",
    ),
    pytest.param(
        [_s(1), _s(6)],
        [_e(5, "base_change_restart", nodes=["impl"])],
        [1, 1],
        [{"pass": 1}],
        id="base-change-restart-of-other-nodes",
    ),
    pytest.param(
        [_s(1), _s(2, round=2), _s(3, round=0)],
        [],
        [1, 1, 2],
        [{"pass": 1}, {"pass": 2}],
        id="round-drop-with-no-event",
    ),
    pytest.param(
        [
            _s(1, round=1),
            _s(2, round=1, task="fix_loop.fix"),
            _s(3, round=0, task="fix_loop.judge"),
        ],
        [],
        [1, 1, 1],
        [{"pass": 1}],
        id="the-loops-own-tasks-drop-no-round",
    ),
    pytest.param(
        [_s(1), _s(2, round=1), _s(3, round=2, task="fix_loop.fix"), _s(4, round=1)],
        [],
        [1, 1, 1, 1],
        [{"pass": 1}],
        id="a-refunded-fix-cycle-measured-again",
    ),
    pytest.param(
        [_s(1, round=1), _s(2, round=-1), _s(3, round=1)],
        [],
        [1, 1, 1],
        [{"pass": 1}],
        id="the-re-measure-after-on-failure",
    ),
    pytest.param(
        [_s(1, round=2), _s(6, round=2), _s(7, round=0)],
        [REJECT],
        [1, 2, 3],
        [{"pass": 1}, {"pass": 2, "reason": "reject", "gate": "gate"}, {"pass": 3}],
        id="a-round-drop-inside-a-rejected-pass",
    ),
    pytest.param(
        [_s(1, round=2), _s(6, round=0)],
        [REJECT],
        [1, 2],
        [{"pass": 1}, {"pass": 2, "reason": "reject", "gate": "gate"}],
        id="reject-and-round-drop-are-one-boundary",
    ),
    pytest.param(
        [_s(6, "verify", task="escalation"), _s(7, task="escalation")],
        [REJECT],
        [None, None],
        None,
        id="an-escalation-turn-is-in-no-pass",
    ),
    pytest.param(
        [_s(1, "gone"), _s(6, "gone")],
        [_e(5, "gate_rejected", gate="old_gate", node=None)],
        [1, 2],
        [{"pass": 1}, {"pass": 2, "reason": "reject", "gate": "old_gate"}],
        id="a-node-the-chain-no-longer-names",
    ),
]


@pytest.mark.parametrize("sessions, boundaries, want, started", CASES)
def test_a_nodes_sessions_are_numbered_by_the_pass_they_ran_in(sessions, boundaries, want, started):
    passes, nodes = number_passes(sessions, boundaries, ORDER)

    assert [passes.get(s["id"]) for s in sessions] == want
    assert nodes.get(sessions[0]["node_id"]) == started


def test_each_node_counts_its_own_passes():
    sessions = [_s(1, "impl"), _s(2, "verify"), _s(6, "impl"), _s(7, "verify"), _s(9, "verify")]
    boundaries = [REJECT, _e(8, "run_forked", scope="node", path="verify")]

    passes, nodes = number_passes(sessions, boundaries, ORDER)

    assert passes == {"s1": 1, "s2": 1, "s6": 2, "s7": 2, "s9": 3}
    assert [len(nodes[n]) for n in ("impl", "verify")] == [2, 3]
