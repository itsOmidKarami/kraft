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
            plan(slices=[{"key": "a", "title": "A", "tasks": [1, 2, 1]}]),
            "slice 1 ('a') lists task 1 twice",
            id="task_twice_in_one_slice",
        ),
        pytest.param(
            plan(
                slices=[
                    {"key": "a", "title": "A", "tasks": [1]},
                    {"key": "b", "title": "B", "tasks": [2], "after": ["a", "a"]},
                ]
            ),
            "slice 2 ('b') comes after 'a' twice",
            id="after_twice",
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
        # A key becomes a label value, a directory, a file name and a session title.
        *[
            pytest.param(
                plan(slices=[{"key": key, "title": "A", "tasks": [1]}]),
                f"slice 1 key {key!r} must be 1 to 40 of a-z, 0-9, '-' and '_'",
                id=f"key_{name}",
            )
            for name, key in (
                ("uppercase", "Alpha"),
                ("slash", "a/b"),
                ("dot", "a.b"),
                ("space", "a b"),
                ("leading_dash", "-a"),
                ("leading_underscore", "_a"),
                ("too_long", "a" * 41),
                ("trailing_newline", "a\n"),
                ("unicode", "café"),
            )
        ],
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
        # A branch name is a label value and an argument to git: one string, no spaces.
        *[
            pytest.param(
                plan(**{field: value}),
                f"the slice plan's {field} must be a branch name, a string with no "
                f"whitespace, not {value!r}",
                id=f"{field}_{name}",
            )
            for field in ("state_branch", "feature_branch")
            for name, value in (
                ("not_a_string", 7),
                ("a_list", ["x"]),
                ("with_a_space", "kraft lite/x"),
                ("with_a_newline", "kraft-lite/x\n"),
            )
        ],
    ],
)
def test_a_broken_slice_plan_is_refused(bad, named):
    with pytest.raises(SystemExit) as refused:
        kl.materialize_slices(copy.deepcopy(bad), CHAIN_ID)
    assert named in str(refused.value)


@pytest.mark.parametrize("key", ["a", "0", "slice-2_b", "a" * 40])
def test_a_slice_key_of_lowercase_letters_digits_dashes_and_underscores_is_taken(key):
    records = kl.materialize_slices(
        plan(slices=[{"key": key, "title": "A", "tasks": [1]}]), CHAIN_ID
    )
    assert list(by_key(records)) == [key]


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

    assert kl.slices_view(records, CHAIN_ID)["ready"] == ["a", "c"]
    records = with_status(records, "a", "merged")
    assert kl.slices_view(records, CHAIN_ID)["ready"] == ["b", "c"]


def test_a_slice_that_has_started_is_not_ready_again():
    records = with_status(kl.materialize_slices(plan(), CHAIN_ID), "a", "running")
    assert kl.slices_view(records, CHAIN_ID)["ready"] == ["c"]


#: b comes after a; d comes after a and c, so no one slice's branch holds both.
JOIN = [
    {"key": "a", "title": "Alpha", "tasks": [1]},
    {"key": "b", "title": "Beta", "tasks": [2], "after": ["a"]},
    {"key": "c", "title": "Gamma", "tasks": [3]},
    {"key": "d", "title": "Delta", "tasks": [4], "after": ["a", "c"]},
]


@pytest.mark.parametrize(
    "mode, statuses, key, listed",
    [
        # `ready` is every after slice merged, in both modes: main mode's
        # `ready_to_merge` is not merged until the user merges it.
        pytest.param("main", {"a": "merged"}, "b", "ready", id="main-after-one-merged"),
        pytest.param("feature", {"a": "merged"}, "b", "ready", id="feature-after-one-merged"),
        # One after slice in its final review or ready to merge: `early`, which the
        # controller may start from that slice's branch.
        pytest.param("main", {"a": "ready_to_merge"}, "b", "early", id="main-after-one-ready"),
        pytest.param(
            "feature", {"a": "ready_to_merge"}, "b", "early", id="feature-after-one-ready"
        ),
        pytest.param("feature", {"a": "in_review"}, "b", "early", id="feature-after-one-in-review"),
        pytest.param("main", {"a": "in_review"}, "b", "early", id="main-after-one-in-review"),
        pytest.param("feature", {"a": "running"}, "b", None, id="after-one-running"),
        pytest.param("feature", {}, "b", None, id="after-one-waiting"),
        # Already started: in neither list.
        pytest.param(
            "feature", {"a": "in_review", "b": "running"}, "b", None, id="started-is-not-early"
        ),
        # Two after slices: one branch cannot hold two slices' unmerged work, so it
        # waits until both are merged, with no early start.
        pytest.param(
            "main",
            {"a": "ready_to_merge", "c": "ready_to_merge"},
            "d",
            None,
            id="main-after-two-ready",
        ),
        pytest.param(
            "main",
            {"a": "merged", "c": "ready_to_merge"},
            "d",
            None,
            id="main-one-merged-one-ready",
        ),
        pytest.param(
            "feature",
            {"a": "merged", "c": "in_review"},
            "d",
            None,
            id="feature-one-merged-one-review",
        ),
        pytest.param("main", {"a": "merged", "c": "merged"}, "d", "ready", id="main-two-merged"),
        pytest.param(
            "feature", {"a": "merged", "c": "merged"}, "d", "ready", id="feature-two-merged"
        ),
        # No after slice at all: ready, never early.
        pytest.param("feature", {}, "a", "ready", id="no-after"),
    ],
)
def test_a_slice_starts_once_its_afters_merge_or_early_after_one(mode, statuses, key, listed):
    records = kl.materialize_slices(plan(mode=mode, slices=JOIN), CHAIN_ID)
    for done, status in statuses.items():
        records = with_status(records, done, status)

    view = kl.slices_view(records, CHAIN_ID)
    assert (key in view["ready"], key in view["early"]) == (listed == "ready", listed == "early")


@pytest.mark.parametrize("mode, done", [("main", True), ("feature", False)])
def test_ready_to_merge_still_counts_as_done_for_closing_implementation(mode, done):
    """Only the start rules wait for merges: main mode's `implementation` closes
    once every slice is ready to merge."""
    records = with_status(kl.materialize_slices(plan(mode=mode), CHAIN_ID), "a", "ready_to_merge")
    [alpha] = [s for s in kl.slices_view(records, CHAIN_ID)["slices"] if s["key"] == "a"]
    assert alpha["done"] is done


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
    assert out["early"] == []
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
        "cut_from": None,
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
        # Slicing is keyed to the default chain's node ids and its gate: a custom
        # chain without them is refused, not walked with no slice guard at all.
        pytest.param(
            "chain_without_nodes",
            "slicing needs the default chain's nodes; this run's chain 'custom' has no "
            "mr_checks, post_merge_watch",
            id="custom_chain_without_nodes",
        ),
        pytest.param(
            "chain_with_another_gate",
            "slicing needs chain_review's gate to be chain_finalized, as in the default "
            "chain; this run's chain 'custom' gates it with 'finalized'",
            id="custom_chain_with_another_gate",
        ),
    ],
)
def test_slices_are_only_accepted_at_chain_finalized(walk, setup, message, kl_cli, tmp_path):
    if setup == "at_spec_gate":
        walk.start(tmp_path)
        kl_cli(tmp_path, "gate", "--name", "spec_approval")
    elif setup.startswith("chain_"):
        chain = json.loads((PLUGIN / "chains" / "default.json").read_text())
        chain["id"] = "custom"
        if setup == "chain_without_nodes":
            dropped = ("mr_checks", "post_merge_watch")
            chain["nodes"] = [n for n in chain["nodes"] if n["id"] not in dropped]
        else:
            [review] = [n for n in chain["nodes"] if n["id"] == "chain_review"]
            review["gate_after"] = "finalized"
        (tmp_path / "custom.json").write_text(json.dumps(chain))
        kl_cli(tmp_path, "start", "--title", "t", "--chain", str(tmp_path / "custom.json"))
        for name in ("spec_approval", "plan_approval", "chain_finalized"):
            kl_cli(tmp_path, "gate", "--name", name)
            if name != "chain_finalized":
                kl_cli(tmp_path, "approve")
    else:
        walk.slice(tmp_path, plan())
        if setup == "already_sliced":
            # A gate of that name opened again, by hand, on the sliced run.
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
        kl_cli(tmp_path, "slice-set", "--slice", key, "--status", "running")
        kl_cli(tmp_path, "slice-set", "--slice", key, "--status", "merged")
    assert run_status() == "open", "a slice is still unmerged"
    kl_cli(tmp_path, "slice-set", "--slice", "c", "--status", "running")
    kl_cli(tmp_path, "slice-set", "--slice", "c", "--status", "ready_to_merge")
    assert run_status() == "open", "ready to merge is not merged, in main mode too"

    kl_cli(tmp_path, "slice-set", "--slice", "c", "--status", "merged")
    assert run_status() == "closed"

    # A call overturned at the wrap-up puts a slice back to work, and the run with it.
    kl_cli(tmp_path, "slice-set", "--slice", "a", "--status", "running")
    assert run_status() == "open"


#: b started early from a's branch, while a is in its review.
EARLY_B = [("a", "running"), ("a", "in_review"), ("b", "starting"), ("b", "running")]


@pytest.mark.parametrize(
    "moves, flags, named",
    [
        pytest.param([], ["--slice", "ghost"], "a, b, c", id="unknown_key"),
        pytest.param(
            [], ["--slice", "a", "--status", "done"], "waiting, starting", id="unknown_status"
        ),
        # b comes after a, which has not started: b is in neither `ready` nor `early`.
        pytest.param(
            [],
            ["--slice", "b", "--status", "starting"],
            "slice 'b' is not in `ready` or `early`: it comes after a (waiting)",
            id="start_before_its_after",
        ),
        pytest.param(
            [],
            ["--slice", "b", "--status", "running", "--session", "s-1"],
            "slice 'b' is not in `ready` or `early`",
            id="run_before_its_after",
        ),
        pytest.param(
            [],
            ["--slice", "a", "--status", "merged"],
            "slice 'a' was never started: it is waiting",
            id="merge_never_started",
        ),
        pytest.param(
            [],
            ["--slice", "c", "--status", "in_review"],
            "slice 'c' was never started: it is waiting",
            id="review_never_started",
        ),
        # An early-cut slice merges after the slice it was cut from, never before.
        pytest.param(
            EARLY_B,
            ["--slice", "b", "--status", "merged"],
            "slice 'b' merges after every slice it comes after; not merged: a (in_review)",
            id="merge_before_its_after",
        ),
        pytest.param(
            [*EARLY_B, ("a", "ready_to_merge")],
            ["--slice", "b", "--status", "merged"],
            "not merged: a (ready_to_merge)",
            id="merge_before_its_after_is_merged",
        ),
        # A merged slice reopened at the wrap-up holds back what comes after it.
        pytest.param(
            [("a", "running"), ("a", "merged"), ("b", "running"), ("a", "running")],
            ["--slice", "b", "--status", "merged"],
            "not merged: a (running)",
            id="merge_after_a_reopened_after",
        ),
    ],
)
def test_slice_set_refuses_an_unknown_key_or_status(walk, moves, flags, named, kl_cli, tmp_path):
    walk.slice(tmp_path, plan())
    for key, status in moves:
        kl_cli(tmp_path, "slice-set", "--slice", key, "--status", status)
    before = kl_cli(tmp_path, "slices")

    refused = kl_cli(tmp_path, "slice-set", *flags, ok=False)

    assert named in refused.stderr
    assert kl_cli(tmp_path, "slices") == before


@pytest.mark.parametrize(
    "moves, last",
    [
        # From `ready`, and from `early` (b's only after slice is in its review).
        pytest.param([("a", "starting")], ("a", "running"), id="ready_then_running"),
        pytest.param([("a", "running"), ("a", "in_review")], ("b", "starting"), id="early"),
        # Already started: no longer in `ready`, and still allowed to move on.
        pytest.param([("a", "starting")], ("a", "starting"), id="same_status_again"),
        pytest.param([], ("b", "waiting"), id="waiting_again"),
        # A call overturned at the wrap-up puts a merged slice back to work.
        pytest.param([("a", "running"), ("a", "merged")], ("a", "running"), id="reopen_merged"),
        # An early-cut slice merges once the slice it was cut from has.
        pytest.param([*EARLY_B, ("a", "merged")], ("b", "merged"), id="merge_after_its_after"),
        # Merged again changes nothing, so it is not refused when its after slice reopens.
        pytest.param(
            [("a", "running"), ("a", "merged"), ("b", "running"), ("b", "merged")]
            + [("a", "running")],
            ("b", "merged"),
            id="merged_again_after_a_reopen",
        ),
    ],
)
def test_slice_set_allows_a_start_from_ready_or_early_and_any_later_move(
    walk, moves, last, kl_cli, tmp_path
):
    walk.slice(tmp_path, plan())
    for key, status in moves:
        kl_cli(tmp_path, "slice-set", "--slice", key, "--status", status)

    key, status = last
    assert kl_cli(tmp_path, "slice-set", "--slice", key, "--status", status)["status"] == status


@pytest.mark.parametrize(
    "flags",
    [
        pytest.param(["--status", "running"], id="same_status"),
        pytest.param(["--session", "s-1", "--branch", "x-a", "--fork", "f1"], id="same_fields"),
        pytest.param([], id="no_flags"),
    ],
)
def test_slice_set_that_changes_nothing_writes_nothing(walk, flags, kl_cli, tmp_path):
    """Every write moves `kraft-seq:` on, and `import` ranks copies by it: a
    no-op counted as a write would beat a real one made in another checkout."""
    walk.slice(tmp_path, plan())
    set_a = ("slice-set", "--slice", "a", "--status", "running", "--session", "s-1")
    kl_cli(tmp_path, *set_a, "--branch", "x-a", "--fork", "f1")
    store = tmp_path / ".kraft-lite" / "chain.jsonl"
    written = store.read_text()

    again = kl_cli(tmp_path, "slice-set", "--slice", "a", *flags)

    assert store.read_text() == written
    assert (again["key"], again["status"], again["session"]) == ("a", "running", "s-1")


def test_slice_verbs_still_answer_after_the_chain_is_done(walk, kl_cli, tmp_path):
    walk.slice(tmp_path, plan())
    walk.merge_slices(tmp_path)
    while kl_cli(tmp_path, "state")["status"] != "done":
        kl_cli(tmp_path, "close")

    assert keys(kl_cli(tmp_path, "slices")["slices"]) == ["a", "b", "c"]
    kl_cli(tmp_path, "slice-set", "--slice", "a", "--status", "running")
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
    walk.merge_slices(tmp_path)
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
