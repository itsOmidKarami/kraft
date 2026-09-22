"""The `## Intent tree` block a repo's `intent_dir` adds to every agent
launch (intent-process design §4)."""

import pytest
from support.harness import entry_of

from kraft import skill
from kraft.adapters import agent
from kraft.worker import steering


def _system_prompt(cmd):
    return cmd[cmd.index("--append-system-prompt") + 1]


def test_the_intent_block_sits_after_the_method_and_before_steering(run):
    prompt = _system_prompt(
        run(
            repo_entry=entry_of({"intent_dir": "docs/intent"}),
            method_text="Write it in one page.",
            steering_texts=("Use tabs.",),
        )["cmd"]
    )
    in_order = (
        skill.HEADING + "Write it in one page.",
        agent.INTENT_HEADING + "This repository states its intended behaviour in `docs/intent/`",
        "`docs/intent/README.md`",
        steering.Steering.HEADING + "Use tabs.",
    )
    firsts = [prompt.index(p) for p in in_order]
    assert firsts == sorted(firsts)


@pytest.mark.parametrize(
    "repo_entry",
    [None, {}, {"intent_dir": None}],
    ids=["no-entry", "empty-entry", "null-intent-dir"],
)
def test_no_intent_dir_no_intent_block(run, repo_entry):
    entry = entry_of(repo_entry) if repo_entry is not None else None
    assert agent.INTENT_HEADING not in _system_prompt(run(repo_entry=entry)["cmd"])
