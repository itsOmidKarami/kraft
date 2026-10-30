"""A shipped SKILL.md runs in someone else's repository. A fact about Kraft's own
repository -- one of its beads, its label taxonomy, its source paths, its
justfile recipes -- reads there as a rule of that repository, and an agent obeys
it (Kraft-35u4m.2: `mr-metadata` told every repo it had `release::` labels)."""

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


_SKILLS = sorted((ROOT / "src" / "kraft" / "skills").glob("*/SKILL.md"))


@pytest.mark.parametrize("path", _SKILLS, ids=lambda p: p.parent.name)
def test_a_worker_skill_never_calls_a_forge_cli(path):
    """A worker holds no forge login: it reads the forge through `kraft view`."""
    call = re.search(r"`(?:gh|glab) (?:pr|mr|run|ci|label|api|issue)\b", path.read_text())
    assert call is None, f"{path.parent.name} calls {call.group(0)!r}"
