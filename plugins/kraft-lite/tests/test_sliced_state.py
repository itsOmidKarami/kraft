"""What a node means in a sliced run, which `state` reports as `sliced`; when the
nodes that wait for the slices may close; what a rewind, or a slice sent back to
work, does to the slices; and the standing rules in `.kraft-lite/slices.json`
that set a run's mode."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN))

import kl  # noqa: E402

#: The spec's table, typed out again rather than read from `kl.SLICED_ACTIONS`, so
#: the test fails when the code drifts from the spec rather than following it.
SLICED_TABLE = {
    "implementation": ("run_slices", "run_slices"),
    "verify": ("scoped_verify", "skip"),
    "open_mr": ("hooks", "skip"),
    "mr_checks": ("hooks", "skip"),
    "human_review": ("hooks", "await_user_merges"),
    "merge": ("hooks", "await_user_merges"),
    # Main mode has no single merge to watch: each slice merged on its own.
    "post_merge_watch": ("hooks", "skip"),
}


@pytest.mark.parametrize(
    "mode, node, action",
    [
        pytest.param(mode, node, actions[column], id=f"{mode}-{node}")
        for column, mode in enumerate(("feature", "main"))
        for node, actions in SLICED_TABLE.items()
    ]
    # A rewind to a node before the slices were cut retires them.
    + [pytest.param("feature", "spec", None, id="feature-spec")],
)
def test_state_says_what_each_node_means_in_a_sliced_run(
    walk, mode, node, action, kl_cli, tmp_path
):
    walk.slice(tmp_path, walk.plan(mode=mode))
    if node == "spec":
        kl_cli(tmp_path, "reject", "--note", "again", "--from-node", "spec")
    walk.merge_slices(tmp_path)
    while kl_cli(tmp_path, "state")["node"] != node:
        kl_cli(tmp_path, "close")

    sliced = {"mode": mode, "action": action} if action else None
    assert kl_cli(tmp_path, "state")["sliced"] == sliced


def test_an_unsliced_chain_reports_sliced_null(walk, kl_cli, tmp_path):
    assert kl_cli(tmp_path, "state")["sliced"] is None
    walk.to_chain_review(tmp_path)
    assert kl_cli(tmp_path, "state")["sliced"] is None


def test_a_finished_or_unstarted_chain_still_reports_sliced(walk, kl_cli, tmp_path):
    assert kl_cli(tmp_path, "state")["status"] == "unstarted"
    walk.slice(tmp_path, walk.plan(mode="main"))
    walk.merge_slices(tmp_path)
    while kl_cli(tmp_path, "state")["status"] != "done":
        kl_cli(tmp_path, "close")

    assert kl_cli(tmp_path, "state")["sliced"] == {"mode": "main", "action": "hooks"}


#: Three slices, b after a, so a test can hold one back while the others finish.
ABC = [
    {"key": "a", "title": "Alpha", "tasks": [1, 2]},
    {"key": "b", "title": "Beta", "tasks": [3], "after": ["a"]},
    {"key": "c", "title": "Gamma", "tasks": [4, 5]},
]


@pytest.mark.parametrize(
    "mode, moves, not_done",
    [
        pytest.param("feature", [], "a (waiting), b (waiting), c (waiting)", id="none_started"),
        pytest.param(
            "feature",
            [("a", "running"), ("a", "merged"), ("c", "running"), ("c", "ready_to_merge")],
            "b (waiting), c (ready_to_merge)",
            id="feature_ready_to_merge_is_not_done",
        ),
        pytest.param(
            "main",
            [("a", "running"), ("a", "merged"), ("c", "running"), ("c", "ready_to_merge")],
            "b (waiting)",
            id="main_one_not_done",
        ),
        pytest.param(
            "feature", [(k, s) for k in "abc" for s in ("running", "merged")], None, id="all_merged"
        ),
        pytest.param(
            "main",
            [("a", "running"), ("a", "merged"), ("b", "running"), ("b", "ready_to_merge")]
            + [("c", "running"), ("c", "ready_to_merge")],
            None,
            id="main_all_ready_to_merge",
        ),
        # Only the node that runs the slices waits for them.
        pytest.param("feature", "spec", None, id="another_node"),
    ],
)
# `approve` closes the node as `close` does, at a gate opened on it by hand.
@pytest.mark.parametrize("verb", ["close", "approve"])
def test_implementation_closes_only_once_every_slice_is_done(
    walk, mode, moves, not_done, verb, kl_cli, tmp_path
):
    def finish():
        if verb == "approve":
            kl_cli(tmp_path, "gate", "--name", "by_hand")
        return kl_cli(tmp_path, verb, ok=not_done is None)

    walk.slice(tmp_path, walk.plan(mode=mode, slices=ABC))
    if moves == "spec":
        kl_cli(tmp_path, "reject", "--note", "again", "--from-node", "spec")
        assert finish()["node"] == "plan"
        return
    for key, status in moves:
        kl_cli(tmp_path, "slice-set", "--slice", key, "--status", status)

    if not_done is None:
        assert finish()["node"] == "verify"
        return
    refused = finish()
    assert f"not done: {not_done}" in refused.stderr and "Traceback" not in refused.stderr
    assert kl_cli(tmp_path, "state")["node"] == "implementation"


#: Every slice of `plan()` ready to merge, b started early from a's branch.
ALL_READY = [
    ("a", "running"),
    ("a", "ready_to_merge"),
    ("b", "starting"),
    ("b", "running"),
    ("b", "ready_to_merge"),
    ("c", "running"),
    ("c", "ready_to_merge"),
]


@pytest.mark.parametrize(
    "node, verb, unmerged",
    [
        pytest.param("human_review", "close", "abc", id="human_review_none_merged"),
        pytest.param("human_review", "close", "bc", id="human_review_two_unmerged"),
        pytest.param("human_review", "approve", "c", id="human_review_approve"),
        pytest.param("merge", "close", "c", id="merge"),
        pytest.param("merge", "approve", "c", id="merge_approve"),
        pytest.param("human_review", "close", "", id="human_review_all_merged"),
        pytest.param("merge", "approve", "", id="merge_all_merged"),
    ],
)
def test_main_modes_await_nodes_close_only_once_the_user_merged_every_slice(
    walk, node, verb, unmerged, kl_cli, tmp_path
):
    """`implementation` closes on ready to merge in main mode; the nodes after it
    wait for the user's merges, or the run finishes before any of them."""

    def move(key, status):
        kl_cli(tmp_path, "slice-set", "--slice", key, "--status", status)

    walk.slice(tmp_path, walk.plan(mode="main", slices=ABC))
    for key, status in ALL_READY:
        move(key, status)
    if node == "merge":
        for key in "abc":
            move(key, "merged")
    while kl_cli(tmp_path, "state")["node"] != node:
        kl_cli(tmp_path, "close")
    for key in "abc":
        move(key, "ready_to_merge" if key in unmerged else "merged")
    if verb == "approve":
        kl_cli(tmp_path, "gate", "--name", "by_hand")

    if not unmerged:
        assert kl_cli(tmp_path, verb)["node"] != node
        return
    refused = kl_cli(tmp_path, verb, ok=False)
    named = ", ".join(f"{key} (ready_to_merge)" for key in unmerged)
    assert f"{node} closes once every slice is merged; not merged: {named}" in refused.stderr
    assert kl_cli(tmp_path, "state")["node"] == node


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
    unnamed = walk.plan(feature_branch=None) if mode == "main" else walk.plan()
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
    walk.slice(tmp_path, walk.plan(mode="feature"))

    assert kl_cli(tmp_path, "slices")["mode"] == "feature"


def stored_slicing(cwd: Path) -> list[dict]:
    """Every run and slice record in the store, retired ones too."""
    records = kl.read_jsonl(cwd / ".kraft-lite" / "chain.jsonl")
    return [
        r
        for r in records
        if kl.label_value(r, kl.RUN_LABEL) or kl.label_value(r, kl.SLICE_OF_LABEL)
    ]


def to_human_review_gate(walk, kl_cli, cwd: Path, mode: str = "feature") -> None:
    """A sliced run with every slice merged, at the gate after `human_review`."""
    walk.slice(cwd, walk.plan(mode=mode, slices=ABC))
    walk.merge_slices(cwd)
    while kl_cli(cwd, "state")["node"] != "human_review":
        kl_cli(cwd, "close")
    kl_cli(cwd, "gate", "--name", "human_review_approval")


@pytest.mark.parametrize("node", ["spec", "plan", "chain_review"])
@pytest.mark.parametrize("mode", kl.MODES)
def test_a_rewind_to_before_implementation_retires_the_slices(walk, node, mode, kl_cli, tmp_path):
    """The plan or its slicing is being redone, so the slices cut from the old
    one stand for nothing: they are retired, and the chain is sliced again at
    the next `chain_finalized`."""
    to_human_review_gate(walk, kl_cli, tmp_path, mode)
    old = {r["id"] for r in stored_slicing(tmp_path)}

    kl_cli(tmp_path, "reject", "--note", "redo", "--from-node", node)

    assert kl_cli(tmp_path, "state")["sliced"] is None
    view = kl_cli(tmp_path, "slices")
    assert (view["mode"], view["slices"], view["ready"], view["early"]) == (None, [], [], [])
    retired = stored_slicing(tmp_path)
    assert {r["id"] for r in retired} == old
    assert all(r["status"] == "closed" for r in retired)
    assert all(kl.label_value(r, kl.RETIRED_LABEL) == node for r in retired)
    # Sliced again, from a new proposal, at the next chain_finalized.
    while kl_cli(tmp_path, "state")["node"] != "chain_review":
        kl_cli(tmp_path, "gate", "--name", "again")
        kl_cli(tmp_path, "approve")
    kl_cli(tmp_path, "gate", "--name", "chain_finalized")
    again = walk.plan(mode=mode, slices=[{"key": "z", "title": "Zeta", "tasks": [1]}])
    kl_cli(tmp_path, "approve", "--slices", str(walk.write_plan(tmp_path, again)))
    assert [e["key"] for e in kl_cli(tmp_path, "slices")["slices"]] == ["z"]
    assert kl_cli(tmp_path, "state")["sliced"]["mode"] == mode


@pytest.mark.parametrize(
    "mode, reopen, node, refused",
    [
        # Every slice done: implementation would close at once, with nothing redone.
        pytest.param("feature", [], "implementation", True, id="feature_all_merged"),
        pytest.param("main", [], "implementation", True, id="main_all_merged"),
        # A slice reopened first: implementation waits for it again.
        pytest.param("feature", ["a"], "implementation", False, id="one_reopened"),
        pytest.param("main", ["a", "c"], "implementation", False, id="main_two_reopened"),
        # Past implementation, the slices stand as they are.
        pytest.param("feature", [], "verify", False, id="past_implementation"),
    ],
)
def test_a_rewind_to_implementation_waits_for_a_reopened_slice(
    walk, mode, reopen, node, refused, kl_cli, tmp_path
):
    to_human_review_gate(walk, kl_cli, tmp_path, mode)
    for key in reopen:
        kl_cli(tmp_path, "slice-set", "--slice", key, "--status", "running")
    before = (kl_cli(tmp_path, "state"), kl_cli(tmp_path, "slices"))

    rewound = kl_cli(tmp_path, "reject", "--note", "redo", "--from-node", node, ok=not refused)

    if refused:
        assert (
            "every slice is done, so implementation would close at once with nothing redone. "
            "Reopen the slices to redo first (slice-set --slice KEY --status running), or "
            "rewind further back to slice the chain again" in rewound.stderr
        )
        assert (kl_cli(tmp_path, "state"), kl_cli(tmp_path, "slices")) == before
        return
    assert rewound["node"] == node and rewound["sliced"]["mode"] == mode
    assert kl_cli(tmp_path, "slices")["slices"] == before[1]["slices"]
    if reopen:
        closing = kl_cli(tmp_path, "close", ok=False)
        assert "not done: " + ", ".join(f"{k} (running)" for k in reopen) in closing.stderr


#: b cut early from a's branch while a is in review.
CUT_B = [("a", "running"), ("a", "in_review"), ("b", "starting", "--fork", "f1")]


@pytest.mark.parametrize(
    "moves, cut_from, base_moved",
    [
        pytest.param(CUT_B, "a", [], id="cut_early"),
        pytest.param([("a", "running"), ("a", "merged"), ("b", "running")], None, [], id="ready"),
        # a's review found work: b's base is no longer a's tip.
        pytest.param([*CUT_B, ("a", "running")], "a", ["b"], id="a_back_to_running"),
        pytest.param([*CUT_B, ("a", "waiting")], "a", ["b"], id="a_back_to_waiting"),
        pytest.param(
            [*CUT_B, ("a", "ready_to_merge"), ("a", "starting")], "a", ["b"], id="a_from_ready"
        ),
        # Still flagged when a is back in review: b has not rebased yet.
        pytest.param([*CUT_B, ("a", "running"), ("a", "in_review")], "a", ["b"], id="a_back"),
        # A new fork is the rebase: onto a's new tip while a is unmerged...
        pytest.param(
            [*CUT_B, ("a", "running"), ("a", "in_review"), ("b", None, "--fork", "f2")],
            "a",
            [],
            id="rebased_onto_a",
        ),
        # ... and onto the target once a is merged.
        pytest.param(
            [*CUT_B, ("a", "merged"), ("b", None, "--fork", "f3")], None, [], id="rebased_on_merge"
        ),
        # Only a started, unmerged slice has a branch left to rebase.
        pytest.param([*CUT_B, ("a", "running"), ("b", "waiting")], "a", [], id="b_back_to_waiting"),
        pytest.param(
            [*CUT_B, ("a", "running"), ("a", "merged"), ("b", "merged")],
            "a",
            [],
            id="b_merged_unrebased",
        ),
        # Started again, a slice is cut afresh: from a, or from the target.
        pytest.param(
            [*CUT_B, ("a", "running"), ("b", "waiting"), ("a", "in_review"), ("b", "starting")],
            "a",
            [],
            id="b_cut_again_early",
        ),
        pytest.param(
            [*CUT_B, ("b", "waiting"), ("a", "merged"), ("b", "running")],
            None,
            [],
            id="b_cut_again_from_target",
        ),
        # a merged first: b rebases onto the target, which a reopened does not move.
        pytest.param([*CUT_B, ("a", "merged"), ("a", "running")], "a", [], id="a_reopened"),
        # Review to ready to merge is no new work.
        pytest.param([*CUT_B, ("a", "ready_to_merge")], "a", [], id="a_ready_to_merge"),
        # A slice not cut from a, or not started, has no base on a's branch.
        pytest.param(
            [("a", "running"), ("a", "in_review"), ("a", "running")], None, [], id="b_waiting"
        ),
        pytest.param(
            [("a", "running"), ("a", "merged"), ("b", "running"), ("a", "running")],
            None,
            [],
            id="a_reopened_after_b_cut_from_target",
        ),
    ],
)
def test_a_slice_sent_back_to_work_flags_the_slices_cut_early_from_it(
    walk, moves, cut_from, base_moved, kl_cli, tmp_path
):
    walk.slice(tmp_path, walk.plan(slices=ABC))
    for key, status, *flags in moves:
        kl_cli(
            tmp_path, "slice-set", "--slice", key, *(["--status", status] if status else []), *flags
        )

    view = kl_cli(tmp_path, "slices")
    assert view["base_moved"] == base_moved
    assert {e["key"]: e["cut_from"] for e in view["slices"]}["b"] == cut_from
