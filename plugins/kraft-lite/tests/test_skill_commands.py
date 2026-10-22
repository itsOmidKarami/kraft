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
def test_the_v1_skills_cite_no_hook(texts, name):
    """V1 chains have tasks, not hooks: a hook name sends the walk looking for a
    registry entry that no longer exists."""
    assert not re.findall(r"`on\.[\w.]+`", texts[name])


@pytest.mark.parametrize("name", V1_SKILLS)
def test_the_v1_skills_spell_artifact_as_state_does(texts, name):
    """One term per concept: `state` says `gate.artifact`."""
    assert "artefact" not in texts[name]


@pytest.mark.parametrize("name", V1_SKILLS)
@pytest.mark.parametrize(
    "other, term",
    [
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
    assert "follow `kraft-lite:init`'s last section, `A pre-V1 directory: migrate`" in body
    headings = re.findall(r"^## (.*)$", texts["init"], re.M)
    assert headings[-1] == "A pre-V1 directory: migrate"


def test_the_next_skill_says_when_a_bound_skill_is_missing(texts):
    """`state` falls a bound skill that is not installed back to its prompt and
    marks the source; the human should hear that the skill did not run."""
    assert "(skill missing)" in texts["next"]


def _section(body: str, number: int) -> str:
    """One numbered `## N.` section of a skill, whitespace flattened; 0 is the whole skill."""
    if not number:
        return " ".join(body.split())
    return " ".join(body.split(f"## {number}.")[1].split("\n## ")[0].split())


@pytest.mark.parametrize(
    "name, section, needle",
    [
        pytest.param(
            "next",
            1,
            "So a file a task writes there is recorded by its absolute path in the worktree.",
            id="next-produced-in-the-feature-worktree",
        ),
        pytest.param(
            "next",
            2,
            "`<file>` being the absolute path where the task wrote it",
            id="next-produced-takes-an-absolute-path",
        ),
        pytest.param(
            "next",
            2,
            "It refuses past `on_base_changed.cap` restarts: with `restarts` already at `cap`, "
            "stop and ask the user instead",
            id="next-restart-cap-from-state",
        ),
        pytest.param(
            "next",
            2,
            "The merge task can report that the forge's merge train or merge queue dropped "
            "the PR instead of merging it",
            id="next-merge-rejected-is-reported-by-the-merge-task",
        ),
        pytest.param(
            "next",
            2,
            "No step failed, so spend no `attempt` and skip section 3. First, before any repair "
            "runs or anything is pushed: with `on_merge_rejected` null, or `rejections` already "
            "at its `cap`, stop and tell the user why the PR was dropped, as for `over_cap`.",
            id="next-merge-rejection-cap-before-the-repair",
        ),
        pytest.param(
            "next",
            2,
            "Stop the same way when the forge gave no reason a repair can act on: the PR was "
            "taken off by hand, or for a reason it does not say.",
            id="next-merge-rejected-without-a-reason-stops",
        ),
        pytest.param(
            "next",
            2,
            "Otherwise run `on_merge_rejected.repair.steps` as above, with the drop's reason and "
            "each failed job as their input. If they committed nothing, stop and tell the user: "
            "the forge would drop the same head again. Else push what they committed to the "
            "PR's branch, then `KL restart --merge-rejected`: it reopens the walk from "
            "`on_merge_rejected.restart_from` through this node, gates included, so the PR's "
            "checks and the final gate are answered again before the merge runs.",
            id="next-merge-rejected-repairs-then-restarts",
        ),
        pytest.param(
            "next",
            1,
            "`git diff --name-only origin/<base branch>...HEAD` lists there",
            id="next-scoped-verify-diffs-against-origin",
        ),
        pytest.param(
            "next",
            1,
            "a `prompt` task runs on their test paths",
            id="next-scoped-verify-prompt-task",
        ),
        pytest.param(
            "next",
            3,
            "spend no attempt and no `on_failure`",
            id="next-unrepairable-error-spends-no-on-failure",
        ),
        pytest.param(
            "start",
            0,
            "follow `kraft-lite:init`'s last section, `A pre-V1 directory: migrate`, first",
            id="start-migrate-follows-init",
        ),
        pytest.param(
            "start", 0, "a chain file path (`.yaml`, or with a `/`)", id="start-chain-file-path"
        ),
        pytest.param(
            "start",
            0,
            "and the chain it froze with where that came from",
            id="start-reports-the-chain-it-froze",
        ),
        pytest.param(
            "status",
            0,
            "whether it is `open` (running) or `blocked`",
            id="status-node-status-words",
        ),
        pytest.param(
            "status",
            0,
            "Render it as the table in `kraft-lite:next`'s section 5 (read it, in "
            "`../next/SKILL.md` beside this skill; do not invoke `next` from here).",
            id="status-summary-as-next-shapes-it",
        ),
        pytest.param(
            "status",
            0,
            "There is no token count or cost: Lite never sees them, so estimate none.",
            id="status-no-cost-to-estimate",
        ),
        pytest.param(
            "gate",
            1,
            "and `--spec`/`--plan` set to the spec and plan the earlier gates approved, so the "
            "new chain starts at the chain revision",
            id="gate-restart-keeps-the-approved-spec-and-plan",
        ),
        pytest.param(
            "gate",
            1,
            "For a `repo:` chain, put the copy in `.kraft/chains/` with a new `id`, so its "
            "`extends` still resolve, and pass `--chain repo:<new id>`.",
            id="gate-restart-repo-chain-stays-in-the-repo-library",
        ),
        pytest.param(
            "gate",
            1,
            "`--chain <that file>` (or `repo:<new id>`), and `--spec`/`--plan`",
            id="gate-restart-names-both-chain-forms",
        ),
        pytest.param(
            "init",
            0,
            "its `summary`, `export` and other reports need the previous Kraft Lite, and "
            "`kl.py` refuses every chain verb but `state` for it",
            id="init-finished-legacy-run-only-state",
        ),
        pytest.param(
            "init",
            0,
            "which closes the run's open records, labelled `kraft-abandoned`, and keeps them",
            id="init-migrate-abandon",
        ),
        pytest.param(
            "init",
            0,
            "Delete `bindings.yaml.mine` once merged.",
            id="init-migrate-deletes-the-moved-aside-copy",
        ),
        pytest.param(
            "init",
            0,
            "With both files present and no verb refusing",
            id="init-migrate-both-files-no-verb-refusing",
        ),
        pytest.param(
            "init",
            0,
            "`{}` when there was no registry, so no file was written",
            id="init-migrate-without-a-registry",
        ),
        pytest.param(
            "init",
            0,
            "move `bindings.yaml` aside (`.kraft-lite/bindings.yaml.mine`), run `migrate`",
            id="init-migrate-with-both-files",
        ),
        pytest.param(
            "init",
            0,
            "It is required - `start` refuses the file without it",
            id="init-prompt-is-required",
        ),
        pytest.param(
            "init",
            0,
            "`.kraft-lite/registry.yaml.migrated` goes there too, or is deleted",
            id="init-ignores-the-migrated-registry",
        ),
        pytest.param(
            "build-slice",
            1,
            "Run `mkdir -p <run>/<slice key>` (it makes `<run>` too), then write "
            "`<run>/spec.md` and `plan.md`",
            id="child-run-folder-made-first",
        ),
        pytest.param(
            "build-slice",
            3,
            "Run no further repair, no further `attempt`, no `close`",
            id="child-over-cap-no-further-repair",
        ),
        pytest.param(
            "build-slice",
            3,
            "never your child chain's",
            id="child-messages-name-the-controllers-chain",
        ),
    ],
)
def test_a_skill_carries_the_final_fix_wave(texts, name, section, needle):
    """The final review's fixes and round two's, by skill and section; 0 is the whole skill."""
    assert needle in _section(texts[name], section), f"{name} section {section} lacks {needle!r}"


def test_the_next_skill_stops_on_an_unrepairable_error_before_on_failure(texts):
    """Kraft spends no repair on a builtin or forge task that failed with an
    error no repair changes (its `test_a_reviewer_error_stops_for_a_human_and_spends_no_repair`):
    the stop comes before `on_failure`, which would otherwise run once first."""
    failing = _section(texts["next"], 3)
    assert failing.index("Every failure is a task with `task_kind` `builtin` or `forge`") < (
        failing.index("The node failed, `on_failure` is set")
    )


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
