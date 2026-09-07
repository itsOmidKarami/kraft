from pathlib import Path

import pytest

from kraft import skill


def _overlay(tmp_path: Path, name: str, body: str) -> Path:
    (tmp_path / name).mkdir(parents=True)
    (tmp_path / name / "SKILL.md").write_text(body)
    return tmp_path


def test_a_bundled_skill_resolves_without_an_overlay(tmp_path):
    assert skill.read(tmp_path, "chain-review").startswith("---")


def test_an_overlay_wins_over_the_bundled_copy(tmp_path):
    _overlay(tmp_path, "chain-review", "operator's own method")
    assert skill.read(tmp_path, "chain-review") == "operator's own method"


def test_an_unknown_skill_raises(tmp_path):
    with pytest.raises(skill.SkillError, match="nope"):
        skill.validate(tmp_path, "nope", where="registry.yaml")


def test_a_plugin_reference_is_passed_through_without_a_lookup(tmp_path):
    # No file anywhere named `superpowers:brainstorming`; validation must not
    # look for one, and read must return the instruction, not a body.
    skill.validate(tmp_path, "superpowers:brainstorming", where="registry.yaml")
    assert "superpowers:brainstorming" in skill.read(tmp_path, "superpowers:brainstorming")


@pytest.mark.parametrize("name", ["../etc/passwd", "a/b", "a\\b", ".", "", ".hidden"])
def test_a_name_that_could_escape_the_skills_directory_is_rejected(tmp_path, name):
    with pytest.raises(skill.SkillError):
        skill.validate(tmp_path, name, where="registry.yaml")


def test_a_non_string_is_rejected(tmp_path):
    with pytest.raises(skill.SkillError):
        skill.validate(tmp_path, ["spec"], where="registry.yaml")
