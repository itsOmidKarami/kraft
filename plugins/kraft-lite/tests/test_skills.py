"""The skills are the executor, so their prose is load-bearing. These check the
things that go silently wrong: a hook named that the chain does not have, a gate
invented, a kind Lite cannot run, a missing frontmatter name."""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN))

import kl  # noqa: E402

SKILLS = ["init", "start", "next", "gate", "status"]
CHAIN = json.loads((PLUGIN / "chains" / "default.json").read_text())
HOOKS = {hook for node in CHAIN["nodes"] for hook in node["tasks"]}
GATES = {node["gate_after"] for node in CHAIN["nodes"]} - {None}


@pytest.fixture(scope="module")
def texts():
    return {name: (PLUGIN / "skills" / name / "SKILL.md").read_text() for name in SKILLS}


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


def test_no_skill_cites_a_hook_the_chain_does_not_have(texts):
    cited = {h for text in texts.values() for h in re.findall(r"`(on\.[\w.]+)`", text)}
    assert cited, "the skills cite no hooks at all"
    assert cited <= HOOKS, f"unknown hooks: {sorted(cited - HOOKS)}"


def test_no_skill_invents_a_gate(texts):
    cited = {
        g for text in texts.values() for g in re.findall(r"`(\w*(?:approval|finalized))`", text)
    }
    assert cited <= GATES, f"unknown gates: {sorted(cited - GATES)}"


def test_the_next_skill_refuses_kraft_only_handler_kinds(texts):
    """A registry copied from Kraft will contain `agent` and `builtin`. Lite must
    say so by name rather than running something unexpected."""
    for kind in ("agent", "builtin"):
        assert f"`kind: {kind}`" in texts["next"]


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
    }
    nodes = re.search(r"chain itself is `chains/default\.json`: (\w+) nodes", readme)
    gates = re.search(r"with (\w+) gates", readme)
    assert nodes and gates, "the README no longer states the counts this test guards"
    assert words[nodes.group(1)] == len(CHAIN["nodes"])
    assert words[gates.group(1)] == len(GATES)


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


def test_the_next_skill_handles_a_hook_with_no_registry_entry(texts):
    """It has an instruction for a bound-but-missing skill and none for a hook
    that is not in the registry at all."""
    assert "not in the registry" in texts["next"] or "no registry entry" in texts["next"]


def test_the_next_skill_warns_that_a_node_replays_its_hooks(texts):
    """State is per node, not per hook, so resuming re-runs earlier hooks."""
    assert "idempotent" in texts["next"]


def test_a_skill_says_the_registry_can_be_edited_mid_run(texts):
    """Chains freeze at start and cannot be reordered; registries are re-read at
    every dispatch, so rebinding is the only mid-run correction."""
    body = texts["next"]
    assert "cannot be reordered" in body or "cannot be changed" in body
    assert "registry.yaml" in body


def test_a_key_with_a_body_is_bound():
    assert kl._bound_hooks("on.merge:\n  kind: skill\n  skill: x:y\n") == {"on.merge"}


def test_a_key_with_no_body_is_not_bound():
    # The Kraft-l5z case: this passed validation and died at dispatch, six nodes in.
    assert kl._bound_hooks("on.ci.poll:\non.merge:\n  kind: skill\n") == {"on.merge"}


def test_a_comment_only_body_is_not_bound():
    text = "on.ci.poll:\n# TODO: pick a client\non.merge:\n  kind: skill\n"
    assert kl._bound_hooks(text) == {"on.merge"}


def test_a_trailing_key_with_no_body_is_not_bound():
    assert kl._bound_hooks("on.merge:\n  kind: skill\non.ci.poll:\n") == {"on.merge"}


def test_blank_lines_between_a_key_and_its_body_do_not_unbind_it():
    assert kl._bound_hooks("on.merge:\n\n  kind: skill\n") == {"on.merge"}


def test_kl_parses_on_the_oldest_supported_python():
    """kl.py ships into repos with whatever python3 they have, and the plugin's
    own CI matrix pins the floor at 3.10. The Kraft monorepo requires >=3.14, so
    `ruff format` will happily rewrite this file into syntax the floor cannot
    parse -- PEP 758's unparenthesized `except A, B:` is the one that bit."""
    source = (PLUGIN / "kl.py").read_text()
    ast.parse(source, feature_version=(3, 10))
