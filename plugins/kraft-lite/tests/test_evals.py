"""The kraft-lite evals have no runner here: a model judges their prose. What a
test can pin is their shape, so a bad edit (broken JSON, a case that names a
skill that is not installed) is caught before a model is asked to run it."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import kl_bindings

PLUGIN = Path(__file__).resolve().parents[1]
EVALS = sorted((PLUGIN / "evals").glob("*.json"))
SKILLS = sorted(p.parent.name for p in (PLUGIN / "skills").glob("*/SKILL.md"))
KEYS = {"skills", "query", "setup", "expected_behavior"}
#: Every prompt Lite ships for a builtin or forge task, which a setup copies out of `state`.
DEFAULTS = {
    **{f"builtins.{k}": v["prompt"] for k, v in kl_bindings.BUILTIN_DEFAULTS.items()},
    **{f"forge.{k}": v["prompt"] for k, v in kl_bindings.FORGE_DEFAULTS.items()},
}


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def check_case(case: object, own: str, installed: set) -> list:
    """Every way `case` is not a well-formed eval case of skill `own`."""
    if not isinstance(case, dict):
        return ["the case is not an object"]
    problems = []
    if set(case) != KEYS:
        problems.append(f"keys are {sorted(case)}, not {sorted(KEYS)}")
    for key in ("query", "setup"):
        if not _text(case.get(key)):
            problems.append(f"{key} is not a non-blank string")
    expected = case.get("expected_behavior")
    if not (isinstance(expected, list) and expected and all(map(_text, expected))):
        problems.append("expected_behavior is not a non-empty list of non-blank strings")
    skills = case.get("skills")
    if not (isinstance(skills, list) and skills and all(_text(s) for s in skills)):
        problems.append("skills is not a non-empty list of non-blank strings")
        return problems
    for skill in skills:
        prefix, _, name = skill.partition(":")
        if prefix != "kraft-lite" or name not in installed:
            problems.append(f"{skill!r} is not an installed kraft-lite:<skill>")
    if skills[0] != f"kraft-lite:{own}":
        problems.append(f"the first skill is {skills[0]!r}, not the file's own kraft-lite:{own}")
    return problems


def stale_prompts(setup: str) -> list:
    """The defaults `setup` quotes from their first sentence but not whole: a
    copy of `state` that a later change to the prompt left behind."""
    return sorted(
        name
        for name, prompt in DEFAULTS.items()
        for found in re.finditer(re.escape(prompt.split(". ")[0]), setup)
        if not setup.startswith(prompt, found.start())
    )


def test_every_skill_has_an_evals_file():
    assert [p.stem for p in EVALS] == SKILLS


@pytest.mark.parametrize("path", EVALS, ids=[p.stem for p in EVALS])
def test_every_eval_case_is_well_formed(path):
    cases = json.loads(path.read_text())
    assert isinstance(cases, list) and cases, f"{path.name} is not a non-empty list"
    found = {
        index: problems
        for index, case in enumerate(cases)
        if (problems := check_case(case, path.stem, set(SKILLS)))
    }
    assert not found, f"{path.name}: {found}"


@pytest.mark.parametrize("path", EVALS, ids=[p.stem for p in EVALS])
def test_a_setup_quotes_each_default_prompt_as_lite_ships_it(path):
    found = {
        index: stale
        for index, case in enumerate(json.loads(path.read_text()))
        if (stale := stale_prompts(case["setup"]))
    }
    assert not found, f"{path.name}: copy the current prompt from kl_bindings into {found}"


def test_a_prompt_quoted_from_an_older_release_is_stale():
    first = kl_bindings.FORGE_DEFAULTS["mr.sync"]["prompt"].split(". ")[0]
    assert stale_prompts(f"its prompt is '{first}.'") == ["forge.mr.sync"]


_GOOD = {"skills": ["kraft-lite:gate"], "query": "q", "setup": "s", "expected_behavior": ["b"]}


@pytest.mark.parametrize(
    ("case", "problem"),
    [
        ({k: v for k, v in _GOOD.items() if k != "setup"}, "keys are"),
        ({**_GOOD, "skills": ["kraft-lite:nope"]}, "is not an installed"),
        ({**_GOOD, "skills": ["kraft-lite:next", "kraft-lite:gate"]}, "the first skill is"),
    ],
    ids=["missing-key", "unknown-skill", "own-skill-not-first"],
)
def test_a_malformed_case_is_refused(case, problem):
    problems = check_case(case, "gate", {"gate", "next"})
    assert any(problem in p for p in problems), problems
