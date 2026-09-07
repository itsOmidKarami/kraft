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
