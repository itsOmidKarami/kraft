"""The skills against kl.py itself: every command they or the run-slices
references give parses, and they name what `state` names. The skills are the
executor, so a command its parser refuses is one the agent runs into an error,
or improvises around."""

from __future__ import annotations

import re
import shlex
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN))

import kl  # noqa: E402

#: Every skill: each walks Kraft's V1 chains.
V1_SKILLS = ["init", "start", "next", "gate", "status", "run-slices", "build-slice"]
#: Every file a skill's commands live in: the skills, and run-slices' harness references.
COMMAND_FILES = {
    **{name: PLUGIN / "skills" / name / "SKILL.md" for name in V1_SKILLS},
    **{
        f"references/{harness}": PLUGIN / "skills" / "run-slices" / "references" / f"{harness}.md"
        for harness in ("claude-code", "codex", "cursor")
    },
}


@pytest.mark.parametrize("name", V1_SKILLS)
def test_the_v1_skills_cite_no_hook(texts, name):
    """V1 chains have tasks, not hooks: a hook name sends the walk looking for a
    registry entry that no longer exists."""
    assert not re.findall(r"`on\.[\w.]+`", texts[name])


@pytest.mark.parametrize("name", V1_SKILLS)
def test_the_v1_skills_spell_artifact_as_state_does(texts, name):
    """One term per concept: `state` says `gate.artifact`."""
    assert "artefact" not in texts[name]


def _kl_commands(text: str) -> list[str]:
    """Every `kl.py` command a skill gives: after the helper's path, to the end
    of the line or the code span, and every `KL <verb> ...` code span."""
    spelled = re.findall(r'kl\.py" ([^`\n]+)', text)
    short = [" ".join(span.split()) for span in re.findall(r"`KL ([^`]+)`", text)]
    return spelled + short


@pytest.mark.parametrize("name", list(COMMAND_FILES))
def test_every_command_a_v1_skill_gives_parses(name, capsys):
    """A command kl.py's own parser refuses is one the agent runs into an error,
    or improvises around. Placeholders become a dummy value, and a verb that
    reads one chain gets the `--chain-id` the skills say every `KL` command carries.
    A reference names harness tools and may give no kl.py command; a skill gives some."""
    parser = kl.build_parser()
    commands = _kl_commands(COMMAND_FILES[name].read_text())
    assert commands or name not in V1_SKILLS, f"{name} gives no kl.py command"
    for command in commands:
        args = shlex.split(re.sub(r"<[^>]+>", "x", command))
        if "chain" in kl.VERBS.get(args[0], ()) and "--chain-id" not in args:
            args += ["--chain-id", "x"]
        try:
            parser.parse_args(args)
        except SystemExit:
            pytest.fail(f"{name}: kl.py refuses {command!r}: {capsys.readouterr().err}")


def test_init_writes_bindings_from_detect_and_the_ignores(texts):
    body = texts["init"]
    for needle in (
        'kl.py" detect',
        "`proposed`",
        ".kraft-lite/bindings.yaml",
        'kl.py" migrate',
        ".kraft-lite/decisions/",
        ".kraft-lite/messages/",
        ".kraft-lite/proposals/",
    ):
        assert needle in body, needle
    # the slice layer's defaults are built in: no second detect over a slice chain
    assert "slice.json" not in body


def test_the_start_skill_needs_no_bindings(texts):
    """Every task has a default, so a repo with no bindings file starts; only a
    pre-V1 registry stands in the way, and `migrate` is what moves it."""
    body = texts["start"]
    assert "kraft-lite:init" not in body
    assert 'kl.py" migrate' in body


def test_the_next_skill_says_when_a_bound_skill_is_missing(texts):
    """`state` falls a bound skill that is not installed back to its prompt and
    marks the source; the human should hear that the skill did not run."""
    assert "(skill missing)" in texts["next"]
