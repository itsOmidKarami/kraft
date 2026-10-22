"""The skills against kl.py itself: every command they or the run-slices
references give parses, and they name what `state` names. The skills are the
executor, so a command its parser refuses is one the agent runs into an error,
or improvises around."""

from __future__ import annotations

import re
import shlex
from pathlib import Path

import pytest

import kl

from .test_skill_wording import section

PLUGIN = Path(__file__).resolve().parents[1]

#: Every skill: each walks Kraft's V1 chains.
V1_SKILLS = ["init", "start", "next", "gate", "status", "run-slices", "build-slice"]
#: The two that slice a chain and run its slices.
SLICE_SKILLS = ["run-slices", "build-slice"]
#: What no skill names: a hook, and the legacy chains' own nodes, gate verb and files.
LEGACY = [
    r"`on\.[\w.]+`",
    *map(
        re.escape,
        [
            "slice.json",
            "chain_review",
            "gate --name",
            "`mr_checks`",
            "`open_mr`",
            "`verify`",
            "`human_review`",
            "post_merge_watch",
        ],
    ),
]
#: Every file a skill's commands live in: the skills, and every file in run-slices'
#: `references/` (the harness guides and discover.md).
COMMAND_FILES = {
    **{name: PLUGIN / "skills" / name / "SKILL.md" for name in V1_SKILLS},
    **{
        f"references/{path.stem}": path
        for path in sorted((PLUGIN / "skills" / "run-slices" / "references").glob("*.md"))
    },
}


@pytest.mark.parametrize("name", V1_SKILLS)
def test_the_v1_skills_name_nothing_of_the_legacy_chains(texts, name):
    """V1 chains have tasks, not hooks, and no legacy node, gate verb or file: one
    names a registry entry or a step no V1 chain has, and the walk goes looking for
    it. `registry` is the slice skills' alone, since init and start tell the user
    how to migrate one."""
    terms = LEGACY + ([re.escape("registry")] if name in SLICE_SKILLS else [])
    found = [term for term in terms if re.search(term, texts[name])]
    assert not found, f"{name} names {found}"


@pytest.mark.parametrize("name", V1_SKILLS)
@pytest.mark.parametrize(
    "other, term",
    [
        pytest.param(r"artefact", "artifact", id="artifact"),
        pytest.param(r"\bhuman\b(?!_)|\bthe person\b", "the user", id="user"),
        pytest.param(r"feature chain", "the controller's chain", id="controllers-chain"),
        pytest.param(r"slice chain", "child chain", id="child-chain"),
        pytest.param(r"merge request", "PR, or the MR node", id="pr"),
        pytest.param(r"fix round|fourth round|every round's", "attempt", id="attempt"),
    ],
)
def test_the_v1_skills_use_one_term_per_concept(texts, name, other, term):
    """The seven skills hand work to each other: a person one skill calls the
    human and another the user is two people to an agent reading both."""
    assert not re.search(other, texts[name]), f"{name}: say {term!r}"


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
    pre-V1 registry or run stands in the way, and init's last section, the one
    place that says what `migrate` prints and how it refuses, moves it. Last, so
    a new repo's setup does not read pre-V1 material first."""
    body = " ".join(texts["start"].split())
    assert body.count("kraft-lite:init") == 1
    assert "follow `kraft-lite:init`'s last section, `A pre-V1 directory: migrate`, first" in body
    headings = re.findall(r"^## (.*)$", texts["init"], re.M)
    assert headings[-1] == "A pre-V1 directory: migrate"


def test_the_next_skill_stops_on_an_unrepairable_error_before_on_failure(texts):
    """Kraft spends no repair on a builtin or forge task that failed with an
    error no repair changes (its `test_a_reviewer_error_stops_for_a_human_and_spends_no_repair`):
    the stop comes before `on_failure`, which would otherwise run once first."""
    failing = section(texts["next"], 3)
    assert failing.index(
        "Every failure is a task with `task_kind` `builtin` or `forge` that ended in an error "
        "no repair can change"
    ) < (failing.index("The node failed, `on_failure` is set"))


@pytest.mark.parametrize("name", V1_SKILLS)
def test_every_skill_says_where_the_helper_is_before_its_first_command(texts, name):
    """Read top to bottom, the fallback for an unset plugin root comes before
    the first command that needs it."""
    body = texts[name].split("---", 2)[2]
    assert body.index("is unset") < body.index('kl.py"')


@pytest.mark.parametrize("name", ["next", "gate", "status"])
def test_a_walking_skills_description_says_what_then_when(texts, name):
    """One clause on what the skill does, then the triggers: a description that
    summarises the process is a shortcut the agent follows instead of the skill."""
    description = re.search(r'^description: "(.*)"$', texts[name], re.M).group(1)
    what, _, when = description.partition(" Use when ")
    assert when, description
    assert what.count(".") == 1 and ":" not in what and "(" not in what, what


def test_only_the_status_skill_triggers_on_what_a_run_took(texts):
    for name in V1_SKILLS:
        description = re.search(r'^description: "(.*)"$', texts[name], re.M).group(1)
        assert ("how long" in description) is (name == "status"), name
