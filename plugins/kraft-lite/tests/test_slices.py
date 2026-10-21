"""Slice and run records: the plan a controller splits a feature into, kept in
the same store as the chain it belongs to without ever being read as part of it."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN))

import kl  # noqa: E402

CHAIN_ID = "kl-feature1"


def plan(**over):
    base = {
        "feature_branch": "feature/x",
        "state_branch": "kraft-lite/state",
        "slices": [
            {"key": "a", "title": "Alpha", "tasks": [1, 2]},
            {"key": "b", "title": "Beta", "tasks": [3], "after": ["a"]},
            {"key": "c", "title": "Gamma", "tasks": [4, 5]},
        ],
    }
    return {**base, **over}


def by_key(records):
    return {kl.label_value(r, kl.SLICE_KEY_LABEL): r for r in kl.slice_records(records, CHAIN_ID)}


def with_status(records, key, status):
    target = by_key(records)[key]
    return [kl.set_label(r, kl.SLICE_STATUS_LABEL, status) if r is target else r for r in records]


def test_materialize_slices_makes_a_run_record_then_one_record_per_slice():
    records = kl.materialize_slices(plan(), CHAIN_ID)

    assert len(records) == 4
    run = records[0]
    assert run["id"].startswith("ksr-") and run["status"] == "open"
    assert kl.label_value(run, kl.RUN_LABEL) == CHAIN_ID
    assert kl.label_value(run, kl.MODE_LABEL) == "feature"
    assert kl.label_value(run, kl.FEATURE_BRANCH_LABEL) == "feature/x"
    assert kl.label_value(run, kl.STATE_BRANCH_LABEL) == "kraft-lite/state"
    assert kl.run_record(records, CHAIN_ID) is run

    slices = records[1:]
    assert [r["_type"] for r in slices] == ["issue"] * 3
    assert all(r["id"].startswith("ks-") and "." not in r["id"] for r in slices)
    assert [kl.label_value(r, kl.SLICE_STATUS_LABEL) for r in slices] == ["waiting"] * 3
    assert [kl.label_value(r, kl.SLICE_OF_LABEL) for r in slices] == [CHAIN_ID] * 3
    assert [kl.label_value(r, kl.SLICE_POS_LABEL) for r in slices] == ["0", "1", "2"]
    assert [kl.label_value(r, kl.TASKS_LABEL) for r in slices] == ["1,2", "3", "4,5"]
    assert [r["title"] for r in slices] == ["Alpha", "Beta", "Gamma"]


def test_after_becomes_a_bd_shaped_blocks_dependency():
    records = kl.materialize_slices(plan(), CHAIN_ID)
    slices = by_key(records)

    assert slices["b"]["dependencies"] == [
        {"issue_id": slices["b"]["id"], "depends_on_id": slices["a"]["id"], "type": "blocks"}
    ]
    assert slices["a"]["dependencies"] == []


@pytest.mark.parametrize(
    "bad, named",
    [
        pytest.param(
            plan(
                slices=[
                    {"key": "a", "title": "A", "tasks": [1], "after": ["b"]},
                    {"key": "b", "title": "B", "tasks": [2], "after": ["a"]},
                ]
            ),
            "cycle",
            id="cycle",
        ),
        pytest.param(
            plan(slices=[{"key": "a", "title": "A", "tasks": [1], "after": ["ghost"]}]),
            "ghost",
            id="unknown_after",
        ),
        pytest.param(
            plan(
                slices=[
                    {"key": "a", "title": "A", "tasks": [1]},
                    {"key": "a", "title": "Again", "tasks": [2]},
                ]
            ),
            "'a'",
            id="duplicate_key",
        ),
        pytest.param(
            plan(slices=[{"key": "a", "title": "A", "tasks": []}]), "'a'", id="empty_tasks"
        ),
        pytest.param(plan(mode="sideways"), "sideways", id="unknown_mode"),
        pytest.param(
            plan(feature_branch=None), "feature_branch", id="feature_mode_without_feature_branch"
        ),
        pytest.param(
            {k: v for k, v in plan().items() if k != "state_branch"},
            "state_branch",
            id="no_state_branch",
        ),
    ],
)
def test_a_broken_slice_plan_is_refused(bad, named):
    with pytest.raises(SystemExit) as refused:
        kl.materialize_slices(copy.deepcopy(bad), CHAIN_ID)
    assert named in str(refused.value)


@pytest.mark.parametrize("mode_default", kl.MODES)
def test_a_plan_without_a_mode_takes_the_default(mode_default):
    records = kl.materialize_slices(plan(), CHAIN_ID, mode_default)
    assert kl.label_value(kl.run_record(records, CHAIN_ID), kl.MODE_LABEL) == mode_default


def test_main_mode_allows_no_feature_branch():
    records = kl.materialize_slices(plan(mode="main", feature_branch=None), CHAIN_ID)
    run = kl.run_record(records, CHAIN_ID)
    assert kl.label_value(run, kl.MODE_LABEL) == "main"
    assert kl.label_value(run, kl.FEATURE_BRANCH_LABEL) is None


def test_ready_slices_waits_for_what_a_slice_comes_after():
    records = kl.materialize_slices(plan(), CHAIN_ID)
    keys = lambda rs: [kl.label_value(r, kl.SLICE_KEY_LABEL) for r in rs]  # noqa: E731

    assert keys(kl.ready_slices(records, CHAIN_ID)) == ["a", "c"]
    records = with_status(records, "a", "merged")
    assert keys(kl.ready_slices(records, CHAIN_ID)) == ["b", "c"]


def test_a_slice_that_has_started_is_not_ready_again():
    records = with_status(kl.materialize_slices(plan(), CHAIN_ID), "a", "running")
    ready = [kl.label_value(r, kl.SLICE_KEY_LABEL) for r in kl.ready_slices(records, CHAIN_ID)]
    assert ready == ["c"]


def test_in_main_mode_ready_to_merge_counts_as_done():
    records = kl.materialize_slices(plan(mode="main", feature_branch=None), CHAIN_ID)
    records = with_status(records, "a", "ready_to_merge")
    ready = [kl.label_value(r, kl.SLICE_KEY_LABEL) for r in kl.ready_slices(records, CHAIN_ID)]
    assert "b" in ready

    # In feature mode the same status is not enough: the slice has to be merged.
    feature = with_status(kl.materialize_slices(plan(), CHAIN_ID), "a", "ready_to_merge")
    ready = [kl.label_value(r, kl.SLICE_KEY_LABEL) for r in kl.ready_slices(feature, CHAIN_ID)]
    assert "b" not in ready


def test_slice_records_come_back_in_plan_order_whatever_the_store_returns():
    records = kl.materialize_slices(plan(), CHAIN_ID)
    keys = [
        kl.label_value(r, kl.SLICE_KEY_LABEL) for r in kl.slice_records(records[::-1], CHAIN_ID)
    ]
    assert keys == ["a", "b", "c"]


def test_slices_of_another_chain_are_not_ours():
    other = kl.materialize_slices(plan(), "kl-other")
    mine = kl.materialize_slices(plan(), CHAIN_ID)
    assert kl.run_record([*other, *mine], CHAIN_ID) is mine[0]
    assert len(kl.slice_records([*other, *mine], CHAIN_ID)) == 3


class FakeBd:
    """A bd that holds what was imported and exports it newest first, as
    `bd export` promises no order."""

    def __init__(self):
        self.stored: dict[str, dict] = {}

    def __call__(self, cmd, **kwargs):
        if cmd[1] == "import":
            for line in kwargs["input"].splitlines():
                record = json.loads(line)
                self.stored[record["id"]] = record
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        lines = [json.dumps(r) for r in reversed(list(self.stored.values()))]
        return subprocess.CompletedProcess(cmd, 0, stdout="\n".join(lines) + "\n", stderr="")


@pytest.mark.parametrize("backend", ["jsonl", "bd"])
def test_slice_records_are_invisible_to_the_chain_machinery(backend, tmp_path):
    chain = json.loads((PLUGIN / "chains" / "default.json").read_text())
    store = (
        kl.JsonlStore(tmp_path / "chain.jsonl") if backend == "jsonl" else kl.BdStore(run=FakeBd())
    )
    store.write(kl.materialize(chain, "A feature", CHAIN_ID))
    store.write(kl.materialize_slices(plan(), CHAIN_ID))

    everything = store.load()
    chain_only = [r for r in everything if not r["id"].startswith(("ks-", "ksr-"))]
    assert len(everything) == len(chain_only) + 4

    assert kl._ours(everything, CHAIN_ID) == kl._ours(chain_only, CHAIN_ID)
    assert kl._ours(everything) == kl._ours(chain_only)
    assert kl.chain_summaries(everything) == kl.chain_summaries(chain_only)
    assert kl.summary(kl._ours(everything, CHAIN_ID), chain) == kl.summary(
        kl._ours(chain_only, CHAIN_ID), chain
    )
