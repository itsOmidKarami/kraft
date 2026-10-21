"""The skills are the executor, so their prose is load-bearing. These check the
things that go silently wrong: a legacy hook named, a gate invented, a binding
kind the walk does not dispatch, a missing frontmatter name."""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN))

import kl_bindings  # noqa: E402
import kl_chains  # noqa: E402
import kl_shape  # noqa: E402

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
HARNESS_REFERENCES = ["claude-code", "codex", "cursor"]


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
        assert f"- `{kind}` -" in _section(texts["next"], 2), kind


def test_the_next_skill_states_the_cap_behaviour(texts):
    text = texts["next"]
    assert "over_cap" in text
    assert "escalat" in text.lower()


def test_the_gate_skill_requires_a_reason_to_reject(texts):
    assert "--note" in texts["gate"]


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
    import re

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


@pytest.mark.parametrize("name", SKILLS)
def test_every_skill_can_find_the_helper_without_the_plugin_variable(texts, name):
    """Cloned into `~/.claude/skills/` rather than installed as a plugin,
    `$CLAUDE_PLUGIN_ROOT` is unset and every command becomes `python3 "/kl.py"`."""
    assert "is unset" in texts[name], "no fallback path for an unset plugin root"


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


def test_the_next_skill_warns_that_a_node_replays_its_tasks(texts):
    """State is per node, not per task, so resuming re-runs earlier tasks."""
    assert "idempotent" in texts["next"]


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
        "`mr.automated_review` is skipped in a child",
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
        "automated-review-skipped",
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


def _section(body: str, number: int) -> str:
    """One numbered `## N.` section of a skill, whitespace flattened."""
    return " ".join(body.split(f"## {number}.")[1].split("\n## ")[0].split())


def test_the_child_rebases_by_what_its_branch_was_cut_from(texts):
    """A slice cut from another slice's branch carries that slice's commits until
    it merges. Rebasing it onto the target before then drops them, so its
    `kraft.mr_rebase` does nothing, and a successful rebase makes it target-cut."""
    rebase = _section(texts["build-slice"], 5)
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


@pytest.mark.parametrize(
    "name, section, needle",
    [
        ("build-slice", 1, "never `HEAD`: ask the controller for the line"),
        ("build-slice", 1, "### A fresh container"),
        ("build-slice", 1, "never from `HEAD`"),
        ("build-slice", 1, "Never redo, reset or force-push away pushed work"),
        ("build-slice", 1, "lives in `<run>/<slice key>/chain`, never in the `chains` list"),
        ("build-slice", 1, "named in the **Slice** item of your starting instructions"),
        ("build-slice", 6, "no final whole-branch review and no finishing, merge or PR step"),
        ("run-slices", 3, "--status starting --branch <slice-branch> --fork <sha>"),
        ("run-slices", 3, "the feature chain's id, then key"),
        ("run-slices", 3, "locally its name too"),
        ("run-slices", 3, "### In `main` mode"),
        ("run-slices", 3, "Implementation stops after the last task's reviews"),
        ("run-slices", 4, "and every `waiting` one in `ready` or `early`, find its session"),
        ("run-slices", 5, "A check-in is armed while any slice is not merged"),
        ("run-slices", 5, "`kraft-lite:run-slices check-in, chain <id>: read state first.`"),
        ("run-slices", 5, "`KL slice-set --slice <key> --fork <sha>`"),
        ("run-slices", 6, "Never one cut from another slice that is still unmerged"),
        ("run-slices", 6, "--status running`, and review again"),
        ("run-slices", 6, "through `kraft-lite:next`, which says where its tasks run"),
        ("run-slices", 2, "A missing ability is never a block"),
        ("run-slices", 1, "delete any `.kraft-lite/proposals/<chain-id>.json`"),
        (
            "run-slices",
            3,
            "A slice that comes after two or more slices starts only from `ready`, once all "
            "of them are merged",
        ),
        (
            "run-slices",
            3,
            "`early`: it comes after exactly one slice, and that slice is in review or ready "
            "to merge. It is cut from that slice's branch",
        ),
        (
            "run-slices",
            3,
            "Start it now when your review of that slice has no findings or only small ones; "
            "otherwise it waits for `ready`.",
        ),
        ("run-slices", 3, "where `target` is the word itself, never a branch name"),
        ("build-slice", 1, "where `target` is the word itself, never a branch name"),
        ("build-slice", 1, "`base` holds the word `target` or a slice key, never a branch name"),
        ("build-slice", 1, 'echo <the word after "Cut from:", as given> > <run>/<slice key>/base'),
        (
            "build-slice",
            5,
            "| `target` | `kraft.mr_rebase` runs, or the controller says to | "
            "`origin/<target branch>` |",
        ),
        ("run-slices", 6, "Starting a review: `KL slice-set --slice <key> --status in_review`."),
        (
            "run-slices",
            6,
            "finding. Once your review has no findings or only small ones, start what "
            "`KL slices` lists, as section 3 says.",
        ),
        (
            "run-slices",
            0,
            "A harness with no reference here runs on every ability's fallback, whatever its "
            "own help shows: walk the slices here (section 10), and tell the user nothing wakes "
            "this session, so they invoke `kraft-lite:run-slices` to continue it.",
        ),
        ("status", 0, "one in `early` may start now from the branch of the one slice it comes"),
        ("status", 0, "| can start early from <key> |"),
        ("status", 0, "not even a slice in `ready` or `early`"),
        ("run-slices", 3, "write `none; continue on the controller's check-in` in its place"),
        ("run-slices", 5, "- [ ] Report (section 8). - [ ] Re-arm the check-in"),
        ("run-slices", 4, "then run one check-in (section 5): it reports and re-arms"),
        (
            "run-slices",
            1,
            "`KL gate`, then invoke `kraft-lite:gate`. It presents the gate's own artifact with "
            "the proposal beside it, and records the user's answer",
        ),
        ("run-slices", 6, "run no tests or checks yourself"),
        ("build-slice", 4, "the scheduling tool your **Controller** item names"),
        ("build-slice", 1, "Pass your child chain's `--chain-id <id>` to every verb but `start`"),
        (
            "build-slice",
            2,
            "for a push you asked the controller about, that is the permission: push, "
            "then send the controller a `status`",
        ),
        ("run-slices", 3, 'copied from your reference\'s "Be messaged" and "Check in later"'),
        ("run-slices", 8, "Report by invoking `kraft-lite:status`"),
        ("run-slices", 1, "few enough tasks for one session's context"),
        ("build-slice", 4, "Ask no one to run a skill or a command for you, the user included"),
        ("build-slice", 1, "git checkout -B <your slice branch> origin/<your slice branch>"),
        (
            "next",
            1,
            "first set it up as the repo says: a fresh one has nothing installed. Then run only "
            "the node's tasks with `task_kind` `builtin` and `ref` "
            "`kraft.verify_changed_test_scopes`",
        ),
        ("next", 1, "git worktree add -B <feature branch> <path> origin/<feature branch>"),
        ("next", 1, "runs from the controller's checkout"),
        ("next", 1, "never in the state branch's checkout"),
        (
            "next",
            1,
            "tell each slice cut from its branch or from the target to rebase onto the "
            "target, and archive its child",
        ),
        ("next", 4, "`gate.slice_gate` true and `sliced` null: invoke `kraft-lite:run-slices`"),
        ("next", 2, "export state after every `close`"),
        ("next", 1, "Record a slice merged only once every slice it comes after is"),
        ("run-slices", 6, "and only once every slice it comes after is merged"),
        ("run-slices", 6, "naming any slice it comes after that the user must merge first"),
        ("run-slices", 6, "It is refused while a slice it comes after is unmerged"),
        (
            "run-slices",
            6,
            "When `KL slices` shows `done: true` on every slice (merged; in `main` mode, "
            "ready to merge or merged):",
        ),
        (
            "run-slices",
            7,
            "git -C <state-checkout> diff --cached --quiet || "
            'git -C <state-checkout> commit -m "kraft-lite state"',
        ),
        (
            "run-slices",
            10,
            "No reference, or no session tools With no reference for this harness (above), "
            "or with the abilities really missing after searching deferred tools: say which, "
            "and walk the slices here",
        ),
        (
            "run-slices",
            1,
            "Unsliceable: tell the user its reason and draw nothing. At any other node: tell the "
            "user slicing comes at `shape.slice_gate`, and draw nothing now. In both, go on with "
            "`kraft-lite:next`, so each gate gets `kraft-lite:gate`'s own question.",
        ),
        ("run-slices", 1, "A rewind to the slice gate or earlier retires the slices"),
        (
            "run-slices",
            6,
            "`KL slices` then lists in `base_moved` each slice cut early from it, and each cut "
            "early from those: once it is back in review, tell each to rebase onto the new tip "
            "of the slice its `cut_from` names, naming that slice's branch, one after another "
            "down the line",
        ),
        ("run-slices", 6, "A recorded fork flags the slices cut from that one."),
        ("run-slices", 5, "A non-empty `base_moved`: the rebase in section 6."),
        (
            "run-slices",
            6,
            "`cut_from` in `KL slices` says which: the parent's key, or null for the target.",
        ),
        ("run-slices", 3, "from the slice its `cut_from` names, or `target` when that is null"),
        ("status", 0, "- rebase pending: <key> onto <its cut_from> (each key in `base_moved`)"),
        (
            "build-slice",
            5,
            "| a slice key | the controller says to rebase onto that slice's new tip | "
            "`origin/<that slice's branch>` |",
        ),
        ("build-slice", 5, "with `<onto>` a remote branch, never a sha from a message"),
        ("build-slice", 5, "Onto a slice's new tip, `base` keeps that slice's key."),
        ("build-slice", 5, "onto a slice's new tip `Cut from: <slice key> at <the new fork>`"),
        (
            "gate",
            2,
            "If it refuses, tell the human what it said and ask whether to approve unsliced.",
        ),
        (
            "gate",
            2,
            "The slice gate or earlier: the plan or its slicing is redone, so the rewind "
            "retires every slice. Archive their children.",
        ),
        (
            "gate",
            2,
            "The first build node, or a node between the slice gate and it, to redo some slices: "
            "reopen each slice to redo first,",
        ),
        (
            "next",
            1,
            "one that takes no paths is a reason to stop and say so, never to run the whole suite",
        ),
        (
            "next",
            1,
            "`KL close` with nothing run (`KL approve` on a gate node: `close` refuses one)",
        ),
        ("next", 2, "run `command` as an argument list, without a shell"),
        ("next", 2, "`KL produced --kind <produces> --path <file>`"),
        ("next", 2, "If `on_base_changed` is set, stop the node there and `KL restart`"),
        (
            "next",
            3,
            "The node failed, `on_failure` is set and `on_failure_ran` is false: run "
            "`on_failure.steps` as in section 2, `KL mark --on-failure-ran`, then the whole node "
            "again from its first step",
        ),
        (
            "next",
            3,
            "Every failure is a task with `task_kind` `builtin` or `forge` that ended in an error "
            "no repair can change",
        ),
        (
            "next",
            3,
            "Failing tests or failing checks such a task reports are findings, for the fix loop",
        ),
        (
            "next",
            0,
            "run `migrate` as `kraft-lite:start` says, then go on with the new id it prints",
        ),
        (
            "gate",
            1,
            "invoke `kraft-lite:start` with `--chain <edited file>` to start again on an edited "
            "copy of the chain. The old chain then stays unfinished in `chains`",
        ),
        (
            "init",
            0,
            "If `.kraft-lite/registry.yaml` exists and `.kraft-lite/bindings.yaml` does not",
        ),
        (
            "init",
            0,
            "finish that run on the previous Kraft Lite version, or start again - and let them "
            "choose",
        ),
        (
            "next",
            3,
            "When it prints `attempt` 2 or more and `fix_loop.judge` is set, run the judge first",
        ),
        ("next", 4, "Otherwise `KL gate`, then invoke `kraft-lite:gate`."),
        ("gate", 1, "`gate.message`, and the file at `gate.artifact_path`"),
        ("gate", 1, "Kraft Lite cannot apply it: the chain froze at `start`"),
        ("gate", 2, "A plain `reject` lands on `gate.reject_to`"),
        ("start", 0, 'kl.py" chains --available'),
        ("start", 0, "`--spec <path>` and `--plan <path>`"),
        ("status", 0, "For an exec node, its `steps` in order"),
        (
            "status",
            0,
            "For a gate node, `gate.message`, the file at `gate.artifact_path`, and that a "
            "rejection goes back to `gate.reject_to`.",
        ),
    ],
    ids=[
        "child-fork-never-head",
        "child-fresh-container",
        "child-fresh-container-fork",
        "child-keeps-pushed-work",
        "child-chain-file",
        "child-chain-id-from-instructions",
        "child-no-second-whole-branch-review",
        "controller-records-fork-at-start",
        "controller-names-the-chain-id",
        "controller-local-name",
        "controller-main-mode",
        "controller-method-stops-implementation",
        "controller-resume-matches-ready",
        "controller-check-in-while-unmerged",
        "controller-check-in-message",
        "controller-records-rebased-fork",
        "controller-rebase-only-cut-from-merged",
        "controller-failed-review",
        "controller-feature-worktree",
        "controller-missing-ability",
        "controller-deletes-unused-proposal",
        "controller-two-afters-wait-for-all-merges",
        "controller-early-start",
        "controller-early-start-after-a-clean-review",
        "controller-target-is-a-word",
        "child-target-is-a-word",
        "child-base-is-a-word",
        "child-base-as-given",
        "child-rebase-onto-target-branch",
        "controller-review-starts-a-review",
        "controller-review-then-starts-what-slices-lists",
        "controller-no-reference-takes-the-fallbacks",
        "status-early",
        "status-early-template",
        "status-starts-nothing",
        "controller-child-wake-up-unconfirmed",
        "controller-check-in-reports",
        "controller-resume-runs-a-check-in",
        "controller-gate-records-the-answer",
        "controller-review-runs-nothing",
        "child-wake-up-from-the-controller-item",
        "child-chain-id-to-every-verb-but-start",
        "child-pushes-on-the-users-words",
        "controller-gives-the-child-its-tools",
        "controller-status-report-owned-by-status",
        "controller-small-plan-heuristic",
        "child-asks-no-one-to-continue-it",
        "child-fresh-container-takes-the-remote-head",
        "next-scoped-verify-setup-then-test",
        "next-feature-worktree-from-origin",
        "next-kl-from-the-controller-checkout",
        "next-feature-worktree",
        "next-archives-merged-child",
        "next-slice-gate-to-run-slices",
        "next-exports-after-close",
        "next-records-merges-after-what-they-come-after",
        "controller-merges-after-what-it-comes-after",
        "controller-main-mode-names-the-merge-order",
        "controller-merge-refused-out-of-order",
        "controller-done-is-what-slices-says",
        "controller-state-commit-skips-a-no-op",
        "controller-no-reference-walks-here",
        "controller-unsliceable-chain-walks-unsliced",
        "controller-rewind-retires-the-slices",
        "controller-base-moved-rebases-onto-the-new-tip",
        "controller-recorded-fork-flags-its-cuts",
        "controller-check-in-acts-on-base-moved",
        "controller-cut-from-says-which",
        "controller-replacement-reads-cut-from",
        "status-rebase-pending",
        "child-rebase-onto-a-slices-new-tip",
        "child-rebase-onto-a-remote-branch",
        "child-new-tip-keeps-base",
        "child-new-tip-cut-from-line",
        "gate-refused-slices-asks",
        "gate-rewind-to-the-slice-gate-or-earlier-retires",
        "gate-rewind-to-the-first-build-node-reopens-first",
        "next-scoped-verify-never-the-whole-suite",
        "next-skip-approves-a-gate-node",
        "next-subprocess-without-a-shell",
        "next-records-what-a-task-produced",
        "next-base-moved-restarts",
        "next-on-failure-once",
        "next-builtin-or-forge-error-stops",
        "next-builtin-or-forge-findings-go-to-the-fix-loop",
        "next-pre-v1-refusal-migrates",
        "gate-chain-revision-restarts-through-start",
        "init-migrates-only-without-bindings",
        "init-migrate-refusal-names-the-ways-on",
        "next-judge-from-the-second-repair",
        "next-opens-a-gate-node",
        "gate-presents-message-and-artifact",
        "gate-chain-revision-cannot-apply",
        "gate-reject-lands-on-reject-to",
        "start-lists-available-chains",
        "start-attachments-skip-their-gates",
        "status-exec-node-steps",
        "status-gate-node",
    ],
)
def test_a_skill_carries_the_final_review_fixes(texts, name, section, needle):
    """Section 0 is the whole skill."""
    text = _section(texts[name], section) if section else " ".join(texts[name].split())
    assert needle in text, f"{name} section {section} lacks {needle!r}"


@pytest.mark.parametrize("harness", ["codex", "cursor"])
def test_a_reference_with_no_check_in_sends_the_user_to_run_slices(harness):
    """The controller's fallback continues the run, which is run-slices' to do;
    the child's own wake-up is a separate line, so its instructions carry no
    request that anyone invoke a skill."""
    path = PLUGIN / "skills" / "run-slices" / "references" / f"{harness}.md"
    check_in = " ".join(path.read_text().split("## Check in later")[1].split())
    assert "tell the user to invoke `kraft-lite:run-slices`" in check_in
    assert "kraft-lite:next" not in check_in
    assert "Child: scheduling its own wake-up is unconfirmed too" in check_in
    assert "`none; continue on the controller's check-in`" in check_in


def test_the_claude_code_reference_starts_a_child_from_its_fork():
    body = " ".join((PLUGIN / "skills/run-slices/references/claude-code.md").read_text().split())
    assert "`source_revision`: the commit `--fork` records" in body
    assert "a checkout on the branch or commit the slice is cut from" in body
    assert "Child: the same tool schedules its own wake-up" in body


def test_the_status_skill_hands_back_to_run_slices(texts):
    flat = " ".join(texts["status"].split())
    assert "When `kraft-lite:run-slices` invoked you, return to it after the report." in flat


def test_the_run_slices_skill_names_every_verb_without_a_chain_id(texts):
    flat = " ".join(texts["run-slices"].split())
    assert "except `settings`, `import`, `chains`, `detect` and `start`" in flat
