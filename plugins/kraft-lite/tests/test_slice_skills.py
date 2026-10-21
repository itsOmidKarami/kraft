"""The slice skills on Kraft's V1 chains: the controller slices at the chain's
slice gate, whatever the chain calls it; a child starts from the chain the
controller froze and exported, never from one of its own; and nothing of the
legacy chains is left for either to go looking for."""

from __future__ import annotations

import pytest

SLICE_SKILLS = ["run-slices", "build-slice"]


@pytest.mark.parametrize("name", SLICE_SKILLS)
@pytest.mark.parametrize(
    "legacy",
    [
        "slice.json",
        "chain_review",
        "registry",
        "gate --name",
        "`mr_checks`",
        "`open_mr`",
        "`verify`",
        "`human_review`",
        "post_merge_watch",
    ],
)
def test_the_slice_skills_name_nothing_of_the_legacy_chains(texts, name, legacy):
    """A legacy node, gate verb or file sends the controller or a child after
    something no V1 chain has."""
    assert legacy not in texts[name], f"{name} names {legacy!r}"


@pytest.mark.parametrize(
    "name, needle",
    [
        (
            "run-slices",
            "the last gate before the first build node, which `state` marks `gate.slice_gate` "
            "true (`chain_revision_approval` on the default chain, `plan_approval` on one that "
            "builds right after its plan gate). `kraft-lite:next` invokes this skill there while "
            "`sliced` is null.",
        ),
        (
            "run-slices",
            "Read `KL slices` first: its `shape` names this chain's `slice_gate`, or says why it "
            "is `unsliceable`.",
        ),
        (
            "run-slices",
            "At any other node: tell the user slicing comes at `shape.slice_gate`, and draw "
            "nothing now. In both, go on with `kraft-lite:next`",
        ),
        (
            "run-slices",
            "Then the state branch: its `kraft-lite-state/chain.json` is what the child starts "
            "its child chain from.",
        ),
        (
            "run-slices",
            "An import refused as exported by an older Kraft Lite is a run from before V1 chains: "
            "tell the user it must be finished on the previous Kraft Lite version, and write "
            "nothing.",
        ),
        (
            "build-slice",
            "git show FETCH_HEAD:kraft-lite-state/chain.json > <run>/chain.json python3 "
            '"$CLAUDE_PLUGIN_ROOT/kl.py" start --slice-of <run>/chain.json --title "<slice title>"',
        ),
        ("build-slice", "never start with `--chain` instead"),
        ("build-slice", "a binding in `.kraft-lite/bindings.yaml` that `start` or `state` refuses"),
        (
            "run-slices",
            "A binding in `.kraft-lite/bindings.yaml` that a child's `start` or `state` refuses "
            "is configuration",
        ),
        ("build-slice", "The first build node's tasks run under the slice layer"),
        ("build-slice", "done | <slice key> | <your child chain's last node>"),
        ("build-slice", "status | <slice key> | <node>"),
    ],
    ids=[
        "controller-slices-at-the-slice-gate",
        "controller-reads-the-shape-first",
        "controller-draws-only-at-the-slice-gate",
        "controller-names-the-state-branch-to-start-from",
        "controller-refuses-a-pre-v1-export",
        "child-starts-from-the-exported-chain",
        "child-never-starts-a-chain-of-its-own",
        "child-configuration-is-a-refused-binding",
        "controller-configuration-is-a-refused-binding",
        "child-build-node-under-the-slice-layer",
        "child-done-names-its-last-node",
        "child-ci-status-names-its-node",
    ],
)
def test_the_slice_skills_walk_the_frozen_chain(texts, name, needle):
    """Where the controller slices, what a child starts from, and what each
    names in place of a legacy hook. Whitespace is flattened."""
    assert needle in " ".join(texts[name].split()), f"{name} lacks {needle!r}"
