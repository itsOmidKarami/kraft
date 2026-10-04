"""A shipped SKILL.md runs in someone else's repository. A fact about Kraft's own
repository -- one of its beads, its label taxonomy, its source paths, its
justfile recipes -- reads there as a rule of that repository, and an agent obeys
it (Kraft-35u4m.2: `mr-metadata` told every repo it had `release::` labels)."""

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SHIPPED = sorted(
    [
        *(ROOT / "src" / "kraft" / "skills").glob("*/SKILL.md"),
        *ROOT.glob("plugins/*/skills/*/SKILL.md"),
    ]
)

#: This repo's justfile recipes, except `test`: `kraft repo connect` itself
#: probes `just test` for any repository whose justfile has a `test` recipe.
_RECIPES = set(
    re.findall(r"^@?([a-z][\w-]*)(?:\s[^:=\n]*)?:(?!=)", (ROOT / "justfile").read_text(), re.M)
) - {"test"}

LEAKS = {
    "a bead of this repo": r"\bKraft-[a-z0-9]{3,}\b",
    "this repo's release labels": r"release::",
    "a Kraft ruling": r"\bRuling \d+",
    "this repo's own paths": r"\b(?:src/kraft|docs/(?:superpowers|intent|testing)|dev/check_)",
    "this repo's justfile recipes": rf"\bjust (?:{'|'.join(map(re.escape, sorted(_RECIPES)))})\b",
}


def test_the_shipped_skills_are_found():
    assert len(SHIPPED) > 15 and _RECIPES


@pytest.mark.parametrize("path", SHIPPED, ids=lambda p: str(p.relative_to(ROOT)))
def test_a_shipped_skill_names_nothing_of_this_repo(path):
    text = path.read_text()
    found = {what: m.group(0) for what, rx in LEAKS.items() if (m := re.search(rx, text))}
    assert not found, f"{path.relative_to(ROOT)} names {found}"


#: Three scenarios a skill: the ordinary case, the mistake it warns about, and
#: the case that is a sibling's or a stop. Data for an evaluation run, which
#: nothing in the suite performs.
EVALS = ROOT / "tests" / "fixtures" / "skill_evals"


@pytest.mark.parametrize("path", SHIPPED, ids=lambda p: str(p.relative_to(ROOT)))
def test_a_shipped_skill_has_its_three_evaluations(path):
    name = path.parent.name
    plugin = path.parts[-4] if "plugins" in path.parts else None
    scenarios = json.loads((EVALS / (plugin or "worker") / f"{name}.json").read_text())
    assert sorted(s["kind"] for s in scenarios) == ["boundary", "core", "trap"]
    for s in scenarios:
        assert s["skills"] == [f"{plugin or 'kraft'}:{name}"], s["name"]
        assert s["query"].strip() and len(s["expected_behavior"]) >= 3, s["name"]
