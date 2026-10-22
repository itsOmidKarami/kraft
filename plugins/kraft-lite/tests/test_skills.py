"""The skills are the executor, so their prose is load-bearing. These check the
things that go silently wrong: a legacy hook named, a gate invented, a binding
kind the walk does not dispatch, a missing frontmatter name."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

import kl_bindings
import kl_chains
import kl_shape

from .test_skill_wording import section

PLUGIN = Path(__file__).resolve().parents[1]

SKILLS = ["init", "start", "next", "gate", "status", "run-slices", "build-slice"]
#: Every gate node the bundled library's chains have, and `chain_finalized`, the
#: gate field a skill reads by name.
V1_GATES = {
    node["id"]
    for ref in ("default", "quick-task")
    for node in kl_chains.resolve_bundled(ref)["nodes"]
    if node["kind"] == "gate"
} | {"chain_finalized"}
#: The chain the README counts nodes and gates of: the bundled library's `default`.
CHAIN = kl_chains.resolve_bundled("default")
EVERY_SKILL = sorted(path.parent.name for path in (PLUGIN / "skills").glob("*/SKILL.md"))
HARNESS_REFERENCES = ["claude-code", "codex", "cursor", "gemini", "antigravity", "amp", "opencode"]


@pytest.mark.parametrize("name", SKILLS)
def test_each_skill_has_frontmatter_naming_itself(texts, name):
    text = texts[name]
    assert text.startswith("---\n")
    head = text.split("---", 2)[1]
    assert f"name: {name}" in head
    assert "description:" in head


def test_the_manifest_namespaces_the_skills():
    manifest = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text())
    assert manifest["name"] == "kraft-lite", "a plugin named kraft would collide with Kraft's own"
    assert manifest["skills"] == ["./skills"]


@pytest.mark.parametrize("name", SKILLS)
def test_no_skill_invents_a_gate(texts, name):
    cited = set(re.findall(r"`(\w*(?:approval|finalized))`", texts[name]))
    assert cited <= V1_GATES, f"unknown gates: {sorted(cited - V1_GATES)}"


def test_the_next_skill_dispatches_every_binding_kind(texts):
    """`state` hands each task over already dispatched; a kind the skill does not
    name is one it improvises."""
    for kind in kl_bindings.KINDS:
        assert f"- `{kind}` -" in section(texts["next"], 2), kind


def test_the_next_skill_states_the_cap_behaviour(texts):
    text = texts["next"]
    assert "over_cap" in text
    assert "escalat" in text.lower()


def test_no_skill_tells_the_agent_to_poll_or_sleep(texts):
    """An attended session that sleeps burns the human's attention on a wait."""
    for name, text in texts.items():
        assert not re.search(r"\b(sleep|poll until|wait until|loop until)\b", text, re.I), name


@pytest.mark.parametrize("name", SKILLS)
def test_every_skill_invokes_the_helper_by_plugin_root(texts, name):
    """`kl.py` is not on PATH; the skills must call it where it lives."""
    assert '"$CLAUDE_PLUGIN_ROOT/kl.py"' in texts[name]


def test_the_readme_describes_the_chain_it_actually_ships():
    """The README counts nodes and gates. The chain is generated, so those
    numbers drift the moment a node is added."""
    readme = (PLUGIN / "README.md").read_text()
    words = {
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
        "ten": 10,
        "eleven": 11,
        "twelve": 12,
        "thirteen": 13,
        "fourteen": 14,
        "fifteen": 15,
        "sixteen": 16,
        "seventeen": 17,
        "eighteen": 18,
        "nineteen": 19,
        "twenty": 20,
    }
    nodes = re.search(r"`default` chain:\s+(\w+)\s+nodes", readme)
    gates = re.search(r"(\w+)\s+of\s+them\s+gates", readme)
    assert nodes and gates, "the README no longer states the counts this test guards"
    assert words[nodes.group(1)] == len(CHAIN["nodes"])
    assert words[gates.group(1)] == sum(node["kind"] == "gate" for node in CHAIN["nodes"])


def test_the_skills_distinguish_unstarted_from_done(texts):
    """`state` reports `unstarted` for a repo with no chain. A skill that only
    knows `done` tells the human a chain that never existed has finished."""
    assert "unstarted" in texts["next"]
    assert "unstarted" in texts["status"]


@pytest.mark.parametrize("name", ["next", "gate", "status"])
def test_every_stateful_skill_passes_the_chain_id(texts, name):
    """A cleared context has only the skill prose and the disk. If the skills do
    not carry the id, two chains in one directory stall on the ambiguity error."""
    assert "--chain-id" in texts[name], f"the {name} skill must pass the chain id"


def test_the_start_skill_documents_the_chain_argument(texts):
    """kl.py has accepted --chain since the first release; the skill never said
    so, which is why /kraft-lite:start could only ever run the default chain."""
    body = texts["start"]
    assert "--chain " in body or "--chain <" in body
    assert "--chain-id" in body, "and it must hand the minted id onward"


def test_the_status_skill_uses_the_chains_verb(texts):
    """It is told to report every chain in the directory. Before the verb existed
    the only way to enumerate them was to trigger the ambiguity error."""
    assert 'kl.py" chains' in texts["status"], "it must invoke the verb, not describe it"


def test_the_gate_skill_offers_a_rewind(texts):
    """The last gate rejects work an earlier node produced; without --from-node it
    is approve-or-stall."""
    assert "--from-node" in texts["gate"]


def test_the_next_skill_uses_a_background_wait_when_the_harness_has_one(texts):
    """Runs-once-and-reports made the human re-invoke this skill until CI went
    green. Blocking is still banned; delegating the wait is not."""
    body = texts["next"]
    assert "background" in body
    assert "wake" in body or "notif" in body
    assert "Do not idle" in body, "blocking synchronously is still banned"
    flat = " ".join(body.split())
    assert "If your starting instructions name a controller, wait for CI as" in flat


def test_a_skill_says_the_bindings_can_be_edited_mid_run(texts):
    """Chains freeze at start and cannot be reordered; bindings are re-read at
    every `state`, so rebinding is the only mid-run correction."""
    body = " ".join(texts["next"].split())
    assert "cannot be reordered" in body or "cannot be changed" in body
    assert "bindings.yaml" in body


def test_kl_parses_on_the_oldest_supported_python():
    """kl.py ships into repos with whatever python3 they have, and the plugin's
    own CI matrix pins the floor at 3.10. The Kraft monorepo requires >=3.12, so
    nothing but this test stops a 3.11-or-later construct (PEP 758's
    unparenthesized `except A, B:` bit once) reaching a file the floor cannot
    parse."""
    source = (PLUGIN / "kl.py").read_text()
    ast.parse(source, feature_version=(3, 10))


@pytest.mark.parametrize("name", EVERY_SKILL)
def test_core_skills_name_no_harness_tool(name):
    """Skills name abilities; a harness's tool names live only in the
    run-slices references, so a skill written against one harness's tools does
    not strand a controller running on another."""
    text = (PLUGIN / "skills" / name / "SKILL.md").read_text()
    found = re.findall(
        r"create_session|send_message|archive_session|unarchive_session|SendMessage"
        r"|send_later|ScheduleWakeup|list_events|get_session|CronCreate|ListAgents"
        r"|list_sessions|ToolSearch",
        text,
    )
    assert not found, f"{name} names harness tools: {sorted(set(found))}"


@pytest.mark.parametrize("name", ["run-slices", "build-slice"])
def test_the_slice_skills_name_no_repo_rule_and_no_superpowers_skill(texts, name):
    """The slice skills ship into any repo. A repo's rules come from its own
    CLAUDE.md and CONTRIBUTING.md, and installed skills through the registry."""
    for banned in ("superpowers:", "kraft/", "screenshot", "just test", "just lint"):
        assert banned not in texts[name], f"{name} says {banned!r}"


def test_the_build_slice_skill_asks_the_controller_not_the_human(texts):
    """A child has no human of its own: wherever the walk would stop for one it asks
    the controller, which keeps the decisions log. A skill that tells it to ask the
    user lets answers bypass the log."""
    body = texts["build-slice"]
    for needle in ("controller", "start --slice-of", "rebase --onto"):
        assert needle in body, needle
    for banned in ("ask the human", "ask the user"):
        assert banned not in body.lower(), banned


@pytest.mark.parametrize(
    "needle",
    [
        ".kraft-lite/messages/<chain-id>/",
        "<chain-id> <key>: <slice title>",
        "part i of n",
        "You may push to <branch>.",
        "For the user: configuration",
        "Cut from: target at <sha>",
        "Cut from: <slice key> at <sha>",
        "--path-format=absolute --git-common-dir)/kraft-lite/<chain-id>",
        "new tip",
        "kraft-lite-state/chain.json",
        "`kraft.verify_changed_test_scopes`",
        "`kraft.mr_rebase`",
    ],
    ids=[
        "message-path",
        "session-title",
        "part-marker",
        "push-words",
        "config",
        "cut-from-target",
        "cut-from-slice",
        "child-folder",
        "new-tip",
        "exported-chain",
        "scoped-test-task",
        "rebase-task",
    ],
)
def test_the_child_and_controller_skills_name_the_same_thing(texts, needle):
    """What one skill sends the other must be spelled the same in both, or a child
    writes where the controller does not read."""
    for name in ("run-slices", "build-slice"):
        assert needle in texts[name], f"{name} lacks {needle!r}"


def test_the_child_rebase_reads_the_commit_recorded_at_setup(texts):
    """The fork commit is where the branch was cut: recorded before the first
    commit and read back at the rebase. A merge-base against another slice's branch
    breaks once that branch is squash-merged, force-pushed or deleted."""
    body = texts["build-slice"]
    assert "<slice key>/fork" in body.split("## 5.")[1].split("## 6.")[0]
    assert "<slice key>/fork" in body.split("## 1.")[1].split("## 2.")[0]
    guard = "[ -f <run>/<slice key>/fork ] ||"
    assert guard in body, "a resumed child must not overwrite its fork commit"
    assert "<common>" not in body, "the shared common dir would collide between worktree slices"
    assert "merge-base" not in body.replace("merge-base --is-ancestor", ""), "only as a check"
    assert "--path-format=absolute" in body
    assert body.count("git-common-dir") == body.count("--path-format=absolute --git-common-dir")


def test_the_run_slices_skill_cites_every_verb_it_needs(texts):
    """Every state write is an exact command; a verb the skill only describes
    is one the controller improvises."""
    for verb in (
        "approve --slices",
        "slice-set",
        "--status running --session",
        "--status in_review",
        "decide --",
        "--held",
        "--unreported",
        "export --to",
        "import --from",
    ):
        assert verb in texts["run-slices"], verb


@pytest.mark.parametrize("harness", HARNESS_REFERENCES)
def test_every_harness_reference_covers_the_four_abilities(harness):
    """The skill says which ability it needs; the reference is where each
    harness says how, or says it cannot and names the fallback."""
    path = PLUGIN / "skills" / "run-slices" / "references" / f"{harness}.md"
    headings = set(re.findall(r"^#+\s+(.+?)\s*$", path.read_text(), re.M))
    for ability in (
        "Start a child",
        "Message a child",
        "Be messaged",
        "Archive a child",
        "Check in later",
    ):
        assert ability in headings, f"{harness}.md has no {ability!r} heading"


def test_the_run_slices_skill_spells_the_helper_in_copyable_blocks(texts):
    """A fenced block is what gets pasted. Shorthand in one runs nothing, so
    every command there names the helper the way it is invoked."""
    blocks = re.findall(r"```\n(.*?)```", texts["run-slices"], re.S)
    lines = [line for block in blocks for line in block.splitlines()]
    assert any('"$CLAUDE_PLUGIN_ROOT/kl.py"' in line for line in lines)
    assert not [line for line in lines if re.match(r"\s*KL\b", line)]


SLICED_ACTION_NAMES = sorted(kl_shape.ACTIONS)


@pytest.mark.parametrize("action", SLICED_ACTION_NAMES)
def test_the_next_skill_acts_on_every_sliced_action(texts, action):
    """`state.sliced.action` says what a node means in a sliced run. An action the
    skill does not name is one it dispatches as plain hooks: an implementation node
    run inline instead of by the slices, a main-mode node opening a gate the user's
    merges already answered."""
    assert f"- `{action}` -" in texts["next"], f"next does not act on sliced.action {action!r}"


def test_the_next_skill_hands_each_sliced_action_to_its_owner(texts):
    body = texts["next"]
    assert "kraft-lite:run-slices" in body
    assert "slice-set" in body and "--status merged" in body
    assert "git diff --name-only" in body, "the scoped verify has to name what it scopes to"
    flat = " ".join(body.split())
    assert "Never open" in flat, "await_user_merges must forbid opening the gate"
    assert "`sliced` not null, `status` neither `done` nor `blocked`" in flat
    assert "then export state, as `kraft-lite:run-slices` describes under State branch" in flat
    assert "`sliced`." in flat.split("You get")[1].split("- `status: unstarted`")[0]
    assert "a quick check of the seams between slices before the feature PR" in flat
    # a done sliced chain reports action `run`: status is read before the action
    assert body.index("`status: done`") < body.index("`sliced.action`")


def test_the_status_skill_reports_slices_and_held_questions(texts):
    body = texts["status"]
    for verb in ('kl.py" slices', "decisions --held", "decisions --unreported --mark"):
        assert verb in body, verb
    assert "`ready`" in body
    # held questions lead the report, and `--mark` is refused together with `--held`
    assert body.index("held questions first") < body.index("`ready`") < body.index("last report")
    assert "`after` slices not yet merged" in " ".join(body.split())
    assert "--held --unreported" not in body and "--unreported --held" not in body
    assert "two commands" in body or "separate" in body
    for part in (
        "Held for you",
        "Slices:",
        "Next:",
        "Decided since the last report:",
        "Needs from you:",
    ):
        assert part in body, f"the sliced report template lacks {part!r}"


def test_the_gate_skill_records_slices_at_the_slice_gate(texts):
    body = texts["gate"]
    assert "approve --slices .kraft-lite/proposals/<chain-id>.json" in body
    assert "`gate.slice_gate`" in body
    assert "`sliced`" in body, "a sliced run's rewind decides the slices' fate"
    flat = " ".join(body.split())
    assert "rm .kraft-lite/proposals/<chain-id>.json" in flat, "a rejected proposal is deleted"
    assert (
        "`sliced` null and `.kraft-lite/proposals/<chain-id>.json` exists: the proposal "
        "is this gate's artifact" in flat
    )
    assert "leftover" not in flat, "after a /clear nothing says what was presented"
    assert "cannot be sliced again" not in flat, "a rewind to the slice gate retires the slices"


def test_the_child_rebases_by_what_its_branch_was_cut_from(texts):
    """A slice cut from another slice's branch carries that slice's commits until
    it merges. Rebasing it onto the target before then drops them, so its
    `kraft.mr_rebase` does nothing, and a successful rebase makes it target-cut."""
    rebase = section(texts["build-slice"], 5)
    for needle in (
        "`<run>/<slice key>/base` says when you rebase",
        "| a slice key | the controller says that slice merged | `origin/<target branch>` | "
        "nothing to do",
        "echo target > <run>/<slice key>/base",
        "Cut from: target at <the new fork>",
    ):
        assert needle in rebase, needle
    # the fork is checked before it is used
    assert rebase.index("merge-base --is-ancestor") < rebase.index("git rebase --onto")
    assert "The fork is not `HEAD` itself while your branch has commits of your own" in rebase


def test_the_claude_code_reference_starts_a_child_from_its_fork():
    body = " ".join((PLUGIN / "skills/run-slices/references/claude-code.md").read_text().split())
    assert "`source_revision`: the commit `--fork` records" in body
    assert "a checkout on the branch or commit the slice is cut from" in body
    assert "Child: the same tool schedules its own wake-up" in body
