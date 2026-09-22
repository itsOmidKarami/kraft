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


def test_a_kraft_qualified_name_is_kraft_s_own_bundled_method(tmp_path):
    """Kraft-vhcop: the V1 schema qualifies a skill by plugin (`kraft:spec`), and
    Kraft is the `kraft` plugin -- so a `kraft:` name is Kraft's own shipped
    method, read and injected, never handed to the agent as a plugin reference
    no plugin carries."""
    bundled = (skill.BUNDLED / "spec" / "SKILL.md").read_text()
    skill.validate(tmp_path, "kraft:spec", where="library.yaml")
    assert skill.read(tmp_path, "kraft:spec") == bundled


def test_a_kraft_qualified_name_honours_the_operator_overlay(tmp_path):
    _overlay(tmp_path, "spec", "operator's own spec method")
    assert skill.read(tmp_path, "kraft:spec") == "operator's own spec method"


@pytest.mark.parametrize("name", ["kraft:no-such-method", "kraft:", "kraft:../spec"])
def test_a_kraft_qualified_name_that_resolves_to_nothing_raises(tmp_path, name):
    with pytest.raises(skill.SkillError):
        skill.validate(tmp_path, name, where="library.yaml")


def test_a_library_seeded_before_a_rename_still_resolves_the_old_name(tmp_path):
    """Kraft-35u4m.1: `library.yaml` is seeded once and never overwritten, so
    a home seeded before `mr-checks-repair` became `mr-metadata-repair` still
    names the old one -- and must not stop resolving on upgrade."""
    renamed = (skill.BUNDLED / "mr-metadata-repair" / "SKILL.md").read_text()
    skill.validate(tmp_path, "kraft:mr-checks-repair", where="library.yaml")
    assert skill.read(tmp_path, "kraft:mr-checks-repair") == renamed
