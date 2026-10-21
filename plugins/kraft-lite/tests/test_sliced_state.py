"""What a node means in a sliced run, which `state` reports as `sliced`; when the
nodes that wait for the slices may close; and the standing rules in
`.kraft-lite/slices.json` that set a run's mode."""

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
    + [pytest.param("feature", "spec", "hooks", id="feature-spec")],
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

    assert kl_cli(tmp_path, "state")["sliced"] == {"mode": mode, "action": action}


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
