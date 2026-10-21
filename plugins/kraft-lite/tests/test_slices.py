"""Slice and run records: the plan a controller splits a feature into, kept in
the same store as the chain it belongs to without ever being read as part of it."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN))

import kl  # noqa: E402

CHAIN_ID = "kl-feature1"


def plan(**over):
    base = {
        # Main mode has no feature branch, so a main-mode plan names none unless
        # a test says otherwise.
        "feature_branch": None if over.get("mode") == "main" else "feature/x",
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
        pytest.param(
            plan(
                slices=[
                    {"key": "a", "title": "A", "tasks": [1, 2]},
                    {"key": "b", "title": "B", "tasks": [2, 3]},
                ]
            ),
            "task 2 is in two slices, 'a' and 'b'",
            id="task_in_two_slices",
        ),
        pytest.param(
            plan(slices=[{"key": "a", "title": "A", "tasks": [1]}, {"title": "B", "tasks": [2]}]),
            "slice 2 has no key",
            id="missing_key",
        ),
        pytest.param(
            plan(slices=[{"key": "a", "tasks": [1]}]), "slice 1 has no title", id="missing_title"
        ),
        pytest.param(
            plan(slices=[{"key": "a", "title": "A"}]), "slice 1 ('a') has no tasks", id="no_tasks"
        ),
        pytest.param(plan(slices=[]), "no slices", id="no_slices"),
        pytest.param(
            plan(slices=[{"key": "a", "title": "A", "tasks": ["3-4"]}]),
            "slice 1 ('a') tasks must be a list of task numbers",
            id="tasks_not_numbers",
        ),
        pytest.param(
            plan(slices=[{"key": "a", "title": "A", "tasks": ["Task 3"]}]),
            "tasks must be a list of task numbers",
            id="tasks_words",
        ),
        pytest.param(
            plan(slices=[{"key": "a", "title": "A", "tasks": [True]}]),
            "tasks must be a list of task numbers",
            id="tasks_bool",
        ),
        pytest.param(
            plan(slices=[{"key": "a", "title": "A", "tasks": "1,2"}]),
            "tasks must be a list of task numbers",
            id="tasks_string",
        ),
        pytest.param(
            plan(slices=[{"key": "a", "title": "A", "tasks": [1], "after": None}]),
            "slice 1 ('a') after must be a list of slice keys",
            id="after_null",
        ),
        pytest.param(
            plan(
                slices=[
                    {"key": "a", "title": "A", "tasks": [1]},
                    {"key": "b", "title": "B", "tasks": [2], "after": "a"},
                ]
            ),
            "slice 2 ('b') after must be a list of slice keys",
            id="after_string",
        ),
        pytest.param(
            plan(slices=[{"key": "a", "title": "A", "tasks": [1], "after": [["a"]]}]),
            "after must be a list of slice keys",
            id="after_nested",
        ),
        pytest.param(
            plan(slices=[{"key": 7, "title": "A", "tasks": [1]}]),
            "slice 1 key must be a string",
            id="key_not_a_string",
        ),
        pytest.param(
            plan(slices=[{"key": "a", "title": 7, "tasks": [1]}]),
            "slice 1 ('a') title must be a string",
            id="title_not_a_string",
        ),
        pytest.param(
            plan(feature_branch="kraft-lite/state"),
            "feature_branch and state_branch are both 'kraft-lite/state'",
            id="feature_branch_is_the_state_branch",
        ),
        pytest.param(
            plan(mode="main", feature_branch="feature/x"),
            "main mode has no feature branch",
            id="main_mode_with_a_feature_branch",
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


@pytest.mark.parametrize("mode", [None, *kl.MODES])
def test_a_plan_takes_its_own_mode_or_feature(mode):
    records = kl.materialize_slices(plan(mode=mode), CHAIN_ID)
    assert kl.label_value(kl.run_record(records, CHAIN_ID), kl.MODE_LABEL) == (mode or "feature")


def test_main_mode_allows_no_feature_branch():
    records = kl.materialize_slices(plan(mode="main"), CHAIN_ID)
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


#: b comes after a; d comes after a and c, so no one slice's branch holds both.
JOIN = [
    {"key": "a", "title": "Alpha", "tasks": [1]},
    {"key": "b", "title": "Beta", "tasks": [2], "after": ["a"]},
    {"key": "c", "title": "Gamma", "tasks": [3]},
    {"key": "d", "title": "Delta", "tasks": [4], "after": ["a", "c"]},
]


@pytest.mark.parametrize(
    "mode, statuses, key, ready",
    [
        pytest.param("main", {"a": "ready_to_merge"}, "b", True, id="main-after-one-ready"),
        # In feature mode the same status is not enough: the slice has to be merged.
        pytest.param("feature", {"a": "ready_to_merge"}, "b", False, id="feature-after-one-ready"),
        # Two unmerged branches, and the slice can be cut from only one: it waits
        # for the user's merges.
        pytest.param(
            "main",
            {"a": "ready_to_merge", "c": "ready_to_merge"},
            "d",
            False,
            id="main-after-two-ready",
        ),
        # One left unmerged: cut from that one's branch.
        pytest.param(
            "main",
            {"a": "merged", "c": "ready_to_merge"},
            "d",
            True,
            id="main-one-merged-one-ready",
        ),
        pytest.param("main", {"a": "merged", "c": "merged"}, "d", True, id="main-after-two-merged"),
        pytest.param(
            "feature", {"a": "merged", "c": "merged"}, "d", True, id="feature-after-two-merged"
        ),
    ],
)
def test_in_main_mode_ready_to_merge_counts_as_done(mode, statuses, key, ready):
    records = kl.materialize_slices(plan(mode=mode, slices=JOIN), CHAIN_ID)
    for done, status in statuses.items():
        records = with_status(records, done, status)

    keys = [kl.label_value(r, kl.SLICE_KEY_LABEL) for r in kl.ready_slices(records, CHAIN_ID)]
    assert (key in keys) is ready


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


@pytest.mark.parametrize("backend", ["jsonl", "bd"])
def test_slice_records_are_invisible_to_the_chain_machinery(backend, fake_bd, tmp_path):
    chain = json.loads((PLUGIN / "chains" / "default.json").read_text())
    store = (
        kl.JsonlStore(tmp_path / "chain.jsonl") if backend == "jsonl" else kl.BdStore(run=fake_bd)
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


# --- the verbs, through the real CLI ---------------------------------------------


def keys(entries):
    return [e["key"] for e in entries]


def test_approve_with_slices_at_chain_finalized_creates_them(walk, kl_cli, tmp_path):
    walk.to_chain_review(tmp_path)
    kl_cli(tmp_path, "gate", "--name", "chain_finalized")
    state = kl_cli(tmp_path, "approve", "--slices", str(walk.write_plan(tmp_path, plan())))

    assert state["node"] == "implementation" and state["status"] == "open"
    out = kl_cli(tmp_path, "slices")
    assert out["mode"] == "feature"
    assert out["feature_branch"] == "feature/x" and out["state_branch"] == "kraft-lite/state"
    assert keys(out["slices"]) == ["a", "b", "c"]
    assert out["ready"] == ["a", "c"]
    beta = out["slices"][1]
    assert beta | {"id": None} == {
        "id": None,
        "key": "b",
        "title": "Beta",
        "tasks": [3],
        "after": ["a"],
        "status": "waiting",
        "session": None,
        "branch": None,
        "fork": None,
        "done": False,
    }
    # The chain walks on exactly as it does without slices.
    assert kl_cli(tmp_path, "chains")[0]["status"] != "done"


@pytest.mark.parametrize(
    "setup, message",
    [
        pytest.param("at_spec_gate", "only accepted at chain_finalized", id="at_spec_gate"),
        pytest.param("already_sliced", "already sliced", id="already_sliced"),
        # Sliced wins over "wrong gate": the first check is the one a person can act on.
        pytest.param("sliced_and_open", "already sliced", id="sliced_and_not_blocked"),
        pytest.param("sliced_and_other_gate", "already sliced", id="sliced_and_other_gate"),
    ],
)
def test_slices_are_only_accepted_at_chain_finalized(walk, setup, message, kl_cli, tmp_path):
    if setup == "at_spec_gate":
        walk.start(tmp_path)
        kl_cli(tmp_path, "gate", "--name", "spec_approval")
    else:
        walk.slice(tmp_path, plan())
        if setup == "already_sliced":
            kl_cli(tmp_path, "reject", "--note", "x", "--from-node", "chain_review")
            kl_cli(tmp_path, "gate", "--name", "chain_finalized")
        elif setup == "sliced_and_other_gate":
            kl_cli(tmp_path, "gate", "--name", "something_else")
    before = (kl_cli(tmp_path, "slices"), kl_cli(tmp_path, "state"))

    refused = kl_cli(
        tmp_path, "approve", "--slices", str(walk.write_plan(tmp_path, plan())), ok=False
    )

    assert message in refused.stderr
    assert (kl_cli(tmp_path, "slices"), kl_cli(tmp_path, "state")) == before


def test_a_rejected_slicing_leaves_nothing_behind(walk, kl_cli, tmp_path):
    walk.to_chain_review(tmp_path)
    kl_cli(tmp_path, "gate", "--name", "chain_finalized")
    kl_cli(tmp_path, "reject", "--note", "regroup")

    out = kl_cli(tmp_path, "slices")
    assert out["mode"] is None and out["slices"] == []


@pytest.mark.parametrize(
    "write, named",
    [
        pytest.param(lambda p: None, "cannot read", id="no_such_file"),
        pytest.param(lambda p: p.write_text("{nope"), "cannot read", id="not_json"),
        pytest.param(lambda p: p.write_text("[]"), "not a JSON object", id="not_an_object"),
        pytest.param(
            lambda p: p.write_text(json.dumps(plan(slices=[{"key": "a", "tasks": [1]}]))),
            "slice 1 has no title",
            id="broken_graph",
        ),
    ],
)
def test_a_broken_plan_file_is_refused_and_the_gate_stays_shut(
    walk, write, named, kl_cli, tmp_path
):
    walk.to_chain_review(tmp_path)
    kl_cli(tmp_path, "gate", "--name", "chain_finalized")
    path = tmp_path / "plan.json"
    write(path)

    refused = kl_cli(tmp_path, "approve", "--slices", str(path), ok=False)

    assert named in refused.stderr and "Traceback" not in refused.stderr
    assert kl_cli(tmp_path, "state")["status"] == "blocked"
    assert kl_cli(tmp_path, "slices")["slices"] == []


def test_slice_set_moves_a_slice_and_merged_closes_it(walk, kl_cli, tmp_path):
    walk.slice(tmp_path, plan())
    stored = lambda: {  # noqa: E731
        r["id"]: r for r in kl.read_jsonl(tmp_path / ".kraft-lite" / "chain.jsonl")
    }

    moved = kl_cli(
        tmp_path,
        "slice-set",
        "--slice",
        "a",
        "--status",
        "running",
        "--session",
        "sess-1",
        "--branch",
        "feature/x-a",
        "--fork",
        "abc123",
    )
    assert moved["key"] == "a" and moved["status"] == "running" and moved["done"] is False
    assert (moved["session"], moved["branch"], moved["fork"]) == ("sess-1", "feature/x-a", "abc123")
    assert stored()[moved["id"]]["status"] == "open"
    assert kl_cli(tmp_path, "slices")["ready"] == ["c"]

    merged = kl_cli(tmp_path, "slice-set", "--slice", "a", "--status", "merged")
    assert merged["done"] is True and merged["session"] == "sess-1"  # unset flags keep what was set
    assert stored()[merged["id"]]["status"] == "closed"
    assert kl_cli(tmp_path, "slices")["ready"] == ["b", "c"]

    reopened = kl_cli(tmp_path, "slice-set", "--slice", "a", "--status", "in_review")
    assert stored()[reopened["id"]]["status"] == "open"


@pytest.mark.parametrize("mode", kl.MODES)
def test_the_run_record_closes_when_every_slice_is_merged(walk, mode, kl_cli, tmp_path):
    """Under bd an open record is open work, and a finished run is not."""
    chain_id = walk.slice(tmp_path, plan(mode=mode))

    def run_status():
        stored = kl.read_jsonl(tmp_path / ".kraft-lite" / "chain.jsonl")
        return kl.run_record(stored, chain_id)["status"]

    for key in ("a", "b"):
        kl_cli(tmp_path, "slice-set", "--slice", key, "--status", "merged")
    assert run_status() == "open", "a slice is still unmerged"
    kl_cli(tmp_path, "slice-set", "--slice", "c", "--status", "ready_to_merge")
    assert run_status() == "open", "ready to merge is not merged, in main mode too"

    kl_cli(tmp_path, "slice-set", "--slice", "c", "--status", "merged")
    assert run_status() == "closed"

    # A call overturned at the wrap-up puts a slice back to work, and the run with it.
    kl_cli(tmp_path, "slice-set", "--slice", "a", "--status", "running")
    assert run_status() == "open"


@pytest.mark.parametrize(
    "flags, named",
    [
        pytest.param(["--slice", "ghost"], "a, b, c", id="unknown_key"),
        pytest.param(
            ["--slice", "a", "--status", "done"], "waiting, starting", id="unknown_status"
        ),
    ],
)
def test_slice_set_refuses_an_unknown_key_or_status(walk, flags, named, kl_cli, tmp_path):
    walk.slice(tmp_path, plan())
    before = kl_cli(tmp_path, "slices")

    refused = kl_cli(tmp_path, "slice-set", *flags, ok=False)

    assert named in refused.stderr
    assert kl_cli(tmp_path, "slices") == before


def test_slice_verbs_still_answer_after_the_chain_is_done(walk, kl_cli, tmp_path):
    walk.slice(tmp_path, plan())
    while kl_cli(tmp_path, "state")["status"] != "done":
        kl_cli(tmp_path, "close")

    assert keys(kl_cli(tmp_path, "slices")["slices"]) == ["a", "b", "c"]
    assert kl_cli(tmp_path, "slice-set", "--slice", "a", "--status", "merged")["done"] is True


@pytest.mark.parametrize(
    "verb",
    [
        ["close"],
        ["approve"],
        ["gate", "--name", "x"],
        ["reject", "--note", "x"],
        ["attempt"],
        ["approve", "--slices", "plan.json"],
    ],
    ids=["close", "approve", "gate", "reject", "attempt", "approve_slices"],
)
def test_a_finished_chain_still_refuses_the_verbs_that_need_an_open_node(
    walk, verb, kl_cli, tmp_path
):
    walk.slice(tmp_path, plan())
    while kl_cli(tmp_path, "state")["status"] != "done":
        kl_cli(tmp_path, "close")

    refused = kl_cli(tmp_path, *verb, ok=False)

    assert "no open node" in refused.stderr


@pytest.mark.parametrize("verb", [["slices"], ["slice-set", "--slice", "a"]])
def test_slice_verbs_refuse_to_guess_between_two_unfinished_chains(walk, verb, kl_cli, tmp_path):
    walk.start(tmp_path)
    walk.start(tmp_path)

    refused = kl_cli(tmp_path, *verb, ok=False)

    assert "more than one unfinished chain" in refused.stderr


# --- what a node means in a sliced run, and the standing rules -------------------


#: The spec's table, typed out again rather than read from `kl.SLICED_ACTIONS`, so
#: the test fails when the code drifts from the spec rather than following it.
SLICED_TABLE = {
    "implementation": ("run_slices", "run_slices"),
    "verify": ("scoped_verify", "skip"),
    "open_mr": ("hooks", "skip"),
    "mr_checks": ("hooks", "skip"),
    "human_review": ("hooks", "await_user_merges"),
    "merge": ("hooks", "await_user_merges"),
    "post_merge_watch": ("hooks", "hooks"),
}


@pytest.mark.parametrize(
    "mode, node, action",
    [
        pytest.param(mode, node, actions[column], id=f"{mode}-{node}")
        for column, mode in enumerate(("feature", "main"))
        for node, actions in SLICED_TABLE.items()
    ]
    + [pytest.param("feature", "spec", "hooks", id="feature-spec")],
)
def test_state_says_what_each_node_means_in_a_sliced_run(
    walk, mode, node, action, kl_cli, tmp_path
):
    walk.slice(tmp_path, plan(mode=mode))
    if node == "spec":
        kl_cli(tmp_path, "reject", "--note", "again", "--from-node", "spec")
    while kl_cli(tmp_path, "state")["node"] != node:
        kl_cli(tmp_path, "close")

    assert kl_cli(tmp_path, "state")["sliced"] == {"mode": mode, "action": action}


def test_an_unsliced_chain_reports_sliced_null(walk, kl_cli, tmp_path):
    assert kl_cli(tmp_path, "state")["sliced"] is None
    walk.to_chain_review(tmp_path)
    assert kl_cli(tmp_path, "state")["sliced"] is None


def test_a_finished_or_unstarted_chain_still_reports_sliced(walk, kl_cli, tmp_path):
    assert kl_cli(tmp_path, "state")["status"] == "unstarted"
    walk.slice(tmp_path, plan(mode="main"))
    while kl_cli(tmp_path, "state")["status"] != "done":
        kl_cli(tmp_path, "close")

    assert kl_cli(tmp_path, "state")["sliced"] == {"mode": "main", "action": "hooks"}


def settings_file(cwd, content):
    path = cwd / ".kraft-lite" / "slices.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(content if isinstance(content, str) else json.dumps(content))


def test_settings_merge_over_the_defaults(kl_cli, tmp_path):
    defaults = kl_cli(tmp_path, "settings")
    assert defaults == {
        "mode": "feature",
        "rights": {
            "plan_question": "controller",
            "slice_review": "controller",
            "merge_slice": "controller",
            "merge_main": "user",
            "push_outside_branch": "user",
        },
    }

    settings_file(tmp_path, {"mode": "main"})
    assert kl_cli(tmp_path, "settings") == defaults | {"mode": "main"}

    settings_file(tmp_path, {"rights": {"merge_main": "controller"}})
    merged = kl_cli(tmp_path, "settings")
    assert merged["mode"] == "feature"
    assert merged["rights"] == defaults["rights"] | {"merge_main": "controller"}


@pytest.mark.parametrize(
    "content, named",
    [
        pytest.param({"colour": "red"}, "colour", id="unknown_key"),
        pytest.param({"mode": "sideways"}, "sideways", id="bad_mode"),
        pytest.param({"rights": {"merge_main": "nobody"}}, "merge_main", id="bad_right"),
        pytest.param({"rights": {"fly": "user"}}, "fly", id="unknown_right"),
        pytest.param({"rights": "controller"}, "rights", id="rights_not_an_object"),
        pytest.param("{nope", "slices.json", id="not_json"),
        pytest.param("[]", "not a JSON object", id="not_an_object"),
    ],
)
def test_bad_settings_are_refused(content, named, kl_cli, tmp_path):
    settings_file(tmp_path, content)

    refused = kl_cli(tmp_path, "settings", ok=False)

    assert named in refused.stderr and "Traceback" not in refused.stderr


@pytest.mark.parametrize("mode", kl.MODES)
def test_a_slicing_plan_without_a_mode_takes_the_settings_mode(walk, mode, kl_cli, tmp_path):
    settings_file(tmp_path, {"mode": mode})
    walk.to_chain_review(tmp_path)
    kl_cli(tmp_path, "gate", "--name", "chain_finalized")
    unnamed = plan(feature_branch=None) if mode == "main" else plan()
    kl_cli(tmp_path, "approve", "--slices", str(walk.write_plan(tmp_path, unnamed)))

    assert kl_cli(tmp_path, "slices")["mode"] == mode
    assert kl_cli(tmp_path, "state")["sliced"]["mode"] == mode


@pytest.mark.parametrize(
    "content",
    [
        pytest.param({"mode": "main"}, id="settings_say_main"),
        # Not read at all: a plan that names its mode needs nothing from the file.
        pytest.param("{nope", id="settings_unreadable"),
    ],
)
def test_the_plans_own_mode_beats_the_settings_mode(walk, content, kl_cli, tmp_path):
    settings_file(tmp_path, content)
    walk.slice(tmp_path, plan(mode="feature"))

    assert kl_cli(tmp_path, "slices")["mode"] == "feature"
