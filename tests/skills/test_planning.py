"""The two shipped methods are data, so what can be tested is what they must
not do: re-state the contract (which would drift from `_ARTIFACT`), or assume
an interactive human (a hook agent runs headless)."""

import pytest

from kraft import skill

#: Per skill, because the plan method legitimately names its *input* directory
#: (`.engineering/specs/`, where the approved spec is) while nothing may name
#: its own *output* path — that is `_ARTIFACT`'s to state.
FORBIDDEN = {
    "spec": (".engineering/", "front matter", "git add", "git commit"),
    "plan": (".engineering/plans", "front matter", "git add", "git commit"),
}


@pytest.mark.parametrize("name", ["spec", "plan"])
def test_the_method_loads_and_has_front_matter(name):
    text = skill.read(None, name)
    assert text.startswith("---\n")
    assert f"name: {name}" in text


@pytest.mark.parametrize("name", ["spec", "plan"])
def test_the_method_does_not_restate_the_injected_contract(name):
    text = skill.read(None, name).lower()
    for phrase in FORBIDDEN[name]:
        assert phrase.lower() not in text, f"{name}: the contract block owns {phrase!r}"


@pytest.mark.parametrize("name", ["spec", "plan"])
def test_the_method_routes_questions_through_needs_context(name):
    assert "needs_context" in skill.read(None, name)


def _context(artifact: str) -> str:
    """The contract a task producing `artifact` launches with, under a method
    written for an interactive session -- the kind an operator names in a
    task's `skill:` instead of Kraft's own (Kraft-35u4m.3, .4)."""
    from kraft.adapters import agent

    return " ".join(
        agent.build_context(
            usage_source="envelope",
            context_channel="system_prompt",
            title="t",
            task_instruction="do it",
            repo_path="/r",
            work_item_id="w1",
            node_id="planning",
            hook_point="planning.main.plan_author",
            session_id="s1",
            artifact=artifact,
            method_text="INTERACTIVE-METHOD: run the full test suite, then ask the user.",
        ).split()
    )


def test_a_plan_under_any_method_is_told_verification_runs_the_suite():
    """Kraft-35u4m.3: the plan skill is whichever the task names, and a plugin's
    plan method adds a full-suite step. The contract, which no method can
    remove, says the chain's verification runs the suite, before the method."""
    ctx = _context("plan")
    note = ctx.index("do not add a step that runs the whole suite")
    assert "a verification node after it runs the repository's test suite" in ctx
    assert note < ctx.index("INTERACTIVE-METHOD")
    assert "This plan is carried out by Kraft" not in _context("work_brief")


def test_a_spec_under_any_method_is_told_the_chain_implements_it_headless():
    """Kraft-35u4m.4: the spec skill is whichever the task names, and a plugin's
    brainstorming method asks a human one question at a time and then hands
    off. The contract says the chain carries the spec on, and verifies it."""
    ctx = _context("spec")
    note = ctx.index("This spec is carried out by Kraft, not in this session")
    assert "a verification node runs the repository's test suite" in ctx
    assert "ask every question at once with needs_context" in ctx
    assert note < ctx.index("INTERACTIVE-METHOD")
    assert "carried out by Kraft" not in _context("work_brief")
