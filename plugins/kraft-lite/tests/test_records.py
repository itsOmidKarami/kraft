"""State is a list of bd-shaped issue records. These are the rules that decide
which node runs next, so they are the part that must not be prose."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN))

import kl  # noqa: E402


@pytest.fixture
def chain():
    return json.loads((PLUGIN / "chains" / "default.json").read_text())


def test_materialize_makes_one_record_per_node_plus_an_epic(chain):
    records = kl.materialize(chain, "Add a flag", "kl-abc123")
    assert records[0]["id"] == "kl-abc123"
    assert records[0]["title"] == "Add a flag"
    assert f"{kl.CHAIN_LABEL}default" in records[0]["labels"]
    assert len(records) == len(chain["nodes"]) + 1


def test_nodes_are_chained_by_dependency(chain):
    records = kl.materialize(chain, "t", "kl-abc123")
    nodes = records[1:]
    assert kl.dep_ids(nodes[0]) == ["kl-abc123"]
    for earlier, later in zip(nodes, nodes[1:], strict=False):
        assert kl.dep_ids(later) == [earlier["id"]]


def test_dependencies_are_bd_shaped_objects_not_id_strings(chain):
    """bd's schema wants objects. Emitting strings makes `bd import` of the
    fallback file fail, which is the one promise the shared format exists for."""
    node = kl.materialize(chain, "t", "kl-abc123")[1]
    (dep,) = node["dependencies"]
    assert dep["issue_id"] == node["id"]
    assert dep["depends_on_id"] == "kl-abc123"
    assert dep["type"] == kl.DEP_TYPE


def test_walk_order_is_the_chain_not_the_stores_order(chain):
    """`bd export` returns arbitrary order; reading it as the chain's order walks
    the wrong node."""
    import random

    records = kl.materialize(chain, "t", "kl-abc123")
    shuffled = records[:]
    random.Random(0).shuffle(shuffled)
    assert [r["id"] for r in kl.ordered(shuffled)] == [r["id"] for r in records]

    for r in shuffled:
        if r["id"] in ("kl-abc123", "kl-abc123.spec"):
            r["status"] = "closed"
    assert kl.label_value(kl.current(shuffled), kl.NODE_LABEL) == "plan"


def test_every_node_record_carries_its_node_id(chain):
    records = kl.materialize(chain, "t", "kl-abc123")
    labelled = [kl.label_value(r, kl.NODE_LABEL) for r in records[1:]]
    assert labelled == [n["id"] for n in chain["nodes"]]


def test_current_is_the_first_node_whose_dependencies_are_closed(chain):
    records = kl.materialize(chain, "t", "kl-abc123")
    records[0]["status"] = "closed"
    assert kl.label_value(kl.current(records), kl.NODE_LABEL) == "spec"

    records[1]["status"] = "closed"
    assert kl.label_value(kl.current(records), kl.NODE_LABEL) == "plan"


def test_current_returns_a_blocked_node_rather_than_skipping_it(chain):
    """A gate must stop the walk, not get stepped over."""
    records = kl.materialize(chain, "t", "kl-abc123")
    records[0]["status"] = "closed"
    records[1]["status"] = "blocked"
    node = kl.current(records)
    assert node["status"] == "blocked"
    assert kl.label_value(node, kl.NODE_LABEL) == "spec"


def test_current_is_none_when_everything_is_closed(chain):
    records = kl.materialize(chain, "t", "kl-abc123")
    for r in records:
        r["status"] = "closed"
    assert kl.current(records) is None


def test_attempts_start_at_zero_and_a_new_label_replaces_the_old(chain):
    record = kl.materialize(chain, "t", "kl-abc123")[1]
    assert kl.attempts(record) == 0
    record = kl.set_label(record, kl.ATTEMPT_LABEL, "1")
    record = kl.set_label(record, kl.ATTEMPT_LABEL, "2")
    assert kl.attempts(record) == 2
    assert len([x for x in record["labels"] if x.startswith(kl.ATTEMPT_LABEL)]) == 1


def test_jsonl_round_trips_and_the_last_write_wins(tmp_path, chain):
    path = tmp_path / "chain.jsonl"
    records = kl.materialize(chain, "t", "kl-abc123")
    kl.append_jsonl(path, records)
    updated = dict(records[1], status="closed")
    kl.append_jsonl(path, [updated])

    loaded = kl.read_jsonl(path)
    assert len(loaded) == len(records)
    assert [r["id"] for r in loaded] == [r["id"] for r in records], "order must survive"
    assert loaded[1]["status"] == "closed"


def test_read_jsonl_of_a_missing_file_is_empty(tmp_path):
    assert kl.read_jsonl(tmp_path / "nope.jsonl") == []


def test_the_shipped_helper_imports_nothing_beyond_the_stdlib():
    """The plugin lands in repos that never installed Kraft or anything else."""
    import ast

    tree = ast.parse((PLUGIN / "kl.py").read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module.split(".")[0])
    extra = imported - set(sys.stdlib_module_names)
    assert not extra, f"non-stdlib import: {extra}"


def walked(chain, stamps, statuses=None):
    """A chain part-way through a run: `stamps[i]` is when record `i` was last
    written, and `statuses` overrides the default all-closed walk."""
    records = kl.materialize(chain, "Add a flag", "kl-abc123")
    out = []
    for offset, record in enumerate(records):
        status = "closed" if statuses is None else statuses[offset]
        out.append(dict(record, status=status, updated_at=stamps[offset]))
    return out


def stamp(minute):
    return f"2026-09-07T10:{minute:02d}:00Z"


def test_summary_times_the_run_from_the_epic_to_the_last_node(chain):
    count = len(chain["nodes"])
    records = walked(chain, [stamp(m) for m in range(count + 1)])
    out = kl.summary(records, chain)

    assert out["chain_id"] == "kl-abc123"
    assert out["title"] == "Add a flag"
    assert out["status"] == "done"
    assert out["started"] == stamp(0)
    assert out["finished"] == stamp(count)
    assert out["duration_seconds"] == count * 60
    # Each node is timed from the close of the one before it, the epic for the first.
    assert [n["seconds"] for n in out["nodes"]] == [60] * count
    assert out["totals"]["nodes"] == count
    assert out["totals"]["closed"] == count


def test_summary_carries_attempts_caps_gates_and_rejections(chain):
    count = len(chain["nodes"])
    records = walked(chain, [stamp(m) for m in range(count + 1)])
    loop_at = next(i for i, n in enumerate(chain["nodes"]) if n.get("fix_loop"))
    records[loop_at + 1] = kl.set_label(records[loop_at + 1], kl.ATTEMPT_LABEL, "2")
    records[1] = dict(records[1], description="body\n" + kl.NOTE_PREFIX + "wrong scope")

    out = kl.summary(records, chain)
    node = out["nodes"][loop_at]
    assert (node["attempts"], node["cap"]) == (2, chain["loops"]["verify_fix_loop"]["attempts"])
    assert out["nodes"][0]["rejected"] == "wrong scope"
    assert out["nodes"][0]["gate"] == chain["nodes"][0]["gate_after"]
    assert out["totals"]["attempts"] == 2
    assert out["totals"]["rejections"] == 1
    assert out["totals"]["gates"] == sum(1 for n in chain["nodes"] if n.get("gate_after"))


def test_summary_reports_a_chain_still_running(chain):
    count = len(chain["nodes"])
    statuses = ["closed", "closed", "blocked"] + ["open"] * (count - 2)
    records = walked(chain, [stamp(m) for m in range(count + 1)], statuses)

    out = kl.summary(records, chain)
    assert out["status"] == "blocked"
    assert out["finished"] is None
    assert out["duration_seconds"] is None
    assert out["totals"]["closed"] == 1
    # An unreached node has no time to report, and zero would read as instant.
    assert out["nodes"][-1]["seconds"] is None


def test_summary_never_reports_a_node_taking_negative_time(chain):
    """`_bump_updated` moves each record's stamp forward on its own, so a node
    written several times — gate, reject, close is three — ends up stamped later
    than the node that ran after it. Timed naively that reads as minus two
    seconds, which is not a thing a run can take."""
    count = len(chain["nodes"])
    stamps = [stamp(0), stamp(9)] + [stamp(m) for m in range(2, count + 1)]
    records = walked(chain, stamps)

    out = kl.summary(records, chain)
    assert all(n["seconds"] >= 0 for n in out["nodes"]), out["nodes"]
    assert out["duration_seconds"] == kl._seconds(stamp(0), max(stamps))


def test_summary_of_an_empty_directory_is_unstarted_not_done(chain):
    """`_state` draws this distinction and says why: reporting `done` tells the
    human a chain that never existed has finished."""
    out = kl.summary([], chain)
    assert out["status"] == "unstarted"
    assert out["nodes"] == []
    assert out["totals"]["nodes"] == 0
    # Same keys either way: a caller rendering the totals should not have to ask
    # whether a chain exists first.
    assert set(out["totals"]) == set(kl.summary(walked(chain, [stamp(0)] * 12), chain)["totals"])


def test_summary_counts_gates_answered_not_gates_declared(chain):
    """The template's gate count is known before the run starts, so reporting it
    as the run's cost says nothing - and says it as though work had been done."""
    count = len(chain["nodes"])
    statuses = ["closed", "closed"] + ["open"] * (count - 1)
    records = walked(chain, [stamp(m) for m in range(count + 1)], statuses)

    out = kl.summary(records, chain)
    assert out["totals"]["gates"] == 1, "only spec has been closed through its gate"


def test_summary_counts_every_rejection_of_a_node_not_every_node_rejected(chain):
    count = len(chain["nodes"])
    records = walked(chain, [stamp(m) for m in range(count + 1)])
    note = f"body\n{kl.NOTE_PREFIX}wrong scope\n{kl.NOTE_PREFIX}still wrong"
    records[1] = dict(records[1], description=note)

    out = kl.summary(records, chain)
    assert out["totals"]["rejections"] == 2
    assert out["nodes"][0]["rejected"] == "still wrong", "the latest reason leads"


def test_a_rewind_does_not_erase_the_attempts_the_run_already_spent(chain):
    """`rewind` clears the attempt label on purpose - the fix loop's budget starts
    again. The retries still happened, and they are what made the run expensive."""
    records = walked(chain, [stamp(m) for m in range(len(chain["nodes"]) + 1)])
    loop_at = next(i for i, n in enumerate(chain["nodes"]) if n.get("fix_loop"))
    node = kl.spend_attempt(records[loop_at + 1])
    node = kl.spend_attempt(node)
    records[loop_at + 1] = node
    assert kl.attempts(node) == 2

    rewound = kl.rewind(records, chain["nodes"][loop_at]["id"], "again")
    assert kl.attempts(rewound[0]) == 0, "the loop's budget resets"

    merged = records[: loop_at + 1] + rewound
    assert kl.summary(merged, chain)["totals"]["attempts"] == 2


def test_summary_splits_out_the_time_a_node_spent_waiting_on_a_human(chain):
    """A gated node's time is mostly the human's, and charging it to the agent is
    the one number a chain like this exists to show."""
    count = len(chain["nodes"])
    records = walked(chain, [stamp(m) for m in range(count + 1)])
    # Half of the minute the node took was the human's.
    records[1] = kl.set_label(records[1], kl.WAITED_LABEL, "30")

    out = kl.summary(records, chain)
    assert out["nodes"][0]["seconds"] == 60
    assert out["nodes"][0]["blocked_seconds"] == 30
    assert out["nodes"][1]["blocked_seconds"] is None, "a node with no gate never waited"
    assert out["totals"]["blocked_seconds"] == 30


def test_a_gate_answered_in_no_time_banks_nothing_and_says_so(chain):
    """Zero is a different answer from null here: one node waited and was answered
    at once, the other never had a gate to wait at."""
    count = len(chain["nodes"])
    records = walked(chain, [stamp(m) for m in range(count + 1)])
    just_now = kl._now().strftime(kl.BD_TIME)
    records[1] = kl.bank_wait(kl.set_label(records[1], kl.BLOCKED_LABEL, just_now))

    out = kl.summary(records, chain)
    assert out["nodes"][0]["blocked_seconds"] == 0
    assert out["nodes"][1]["blocked_seconds"] is None


def test_a_block_stamp_from_the_future_never_banks_negative_time(chain):
    """A clock that went backwards between the gate and the answer is not a reason
    to report that the human handed time back."""
    record = kl.set_label(
        kl.materialize(chain, "t", "kl-abc123")[1], kl.BLOCKED_LABEL, "2099-01-01T00:00:00Z"
    )
    assert kl.waited(kl.bank_wait(record)) == 0


def test_a_rewind_clears_the_block_stamp_of_the_node_it_reopens(chain):
    """Left in place, the redo is timed from the gate the *previous* attempt hit,
    and the human is billed for the hours in between twice."""
    records = walked(chain, [stamp(m) for m in range(len(chain["nodes"]) + 1)])
    records[1] = kl.set_label(records[1], kl.BLOCKED_LABEL, stamp(0))

    rewound = kl.rewind(records, chain["nodes"][0]["id"], "again")
    assert kl.label_value(rewound[0], kl.BLOCKED_LABEL) is None


def test_time_waited_is_banked_and_survives_a_rewind(chain):
    """Like the attempts a rewind clears: the hours the human already spent at a
    gate were spent, whatever the redo goes on to cost."""
    records = walked(chain, [stamp(m) for m in range(len(chain["nodes"]) + 1)])
    records[1] = kl.set_label(records[1], kl.WAITED_LABEL, "600")

    rewound = kl.rewind(records, chain["nodes"][0]["id"], "again")
    assert kl.label_value(rewound[0], kl.WAITED_LABEL) == "600"
    assert kl.summary(records[:1] + rewound, chain)["totals"]["blocked_seconds"] == 600


def test_a_second_gate_round_does_not_erase_the_first_ones_wait(chain):
    """`gate` on a node already blocked - a re-run, or a chain steered by hand -
    overwrote the open stamp, and an hour already waited went with it."""
    record = kl.materialize(chain, "t", "kl-abc123")[1]
    record = kl.set_label(record, kl.WAITED_LABEL, "3600")
    record = kl.set_label(record, kl.BLOCKED_LABEL, kl._now().strftime(kl.BD_TIME))

    assert kl.waited(kl.open_gate(record, "spec_approval")) == 3600


def test_a_redone_node_is_timed_from_the_rewind_not_from_the_first_pass(chain):
    """The first rewound node's predecessor still holds its original close stamp,
    so timing from it charges the redo for every node the first pass walked."""
    count = len(chain["nodes"])
    records = walked(chain, [stamp(m) for m in range(count + 1)])
    records = [kl.set_label(r, kl.AT_LABEL, r["updated_at"]) for r in records]
    # The chain ran to the end, then node 1 and everything after it was reopened
    # at minute 20, and node 1 was redone a minute later.
    for offset in range(1, count + 1):
        records[offset] = kl.set_label(dict(records[offset], status="open"), kl.AT_LABEL, stamp(20))
    records[1] = kl.set_label(dict(records[1], status="closed"), kl.AT_LABEL, stamp(21))

    assert kl.summary(records, chain)["nodes"][0]["seconds"] == 60


def test_a_rejection_note_quoting_a_rejection_is_still_one_rejection(chain):
    count = len(chain["nodes"])
    records = walked(chain, [stamp(m) for m in range(count + 1)])
    note = f"hooks: x\n{kl.NOTE_PREFIX}Rejected: by CI, see run 12"
    records[1] = dict(records[1], description=note)

    out = kl.summary(records, chain)
    assert out["totals"]["rejections"] == 1
    assert out["nodes"][0]["rejected"] == "Rejected: by CI, see run 12"
