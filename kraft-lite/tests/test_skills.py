"""The skills are the executor, so their prose is load-bearing. These check the
things that go silently wrong: a hook named that the chain does not have, a gate
invented, a kind Lite cannot run, a missing frontmatter name."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
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


def test_the_marketplace_offers_this_plugin_from_the_repo_root():
    """The published repo is its own marketplace, so `source` has to be the root
    the plugin manifest sits in, and the plugin name has to be the one users type
    after `@`."""
    market = json.loads((PLUGIN / ".claude-plugin" / "marketplace.json").read_text())
    plugin = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text())
    assert market["name"] == plugin["name"], "`/plugin install kraft-lite@kraft-lite` needs both"
    assert [(e["name"], e["source"]) for e in market["plugins"]] == [(plugin["name"], "./")]


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
    words = {"two": 2, "three": 3, "four": 4, "five": 5, "ten": 10, "eleven": 11, "twelve": 12}
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


def test_the_readme_warns_that_main_is_force_pushed():
    """The README's own install instruction is `git clone`. Without this, the
    first publish after a user installs breaks their `git pull` with no stated
    recovery, and the plugin looks broken."""
    readme = (PLUGIN / "README.md").read_text()
    assert "## Contributing" in readme
    assert "force-pushed" in readme
    assert "git reset --hard origin/main" in readme


# Not just links: a bare `docs/superpowers/specs/...` in prose is the same dead
# reference to a reader who only ever sees the published repo. `(?<![\w./-])`
# keeps `/dev/null` and `.kraft-lite/chain.jsonl` out of it.
OUTSIDE = re.compile(
    r"(?<![\w./-])(?:\.\./|/(?:Users|home)/|docs/|design/|src/|templates/|dev/|fixtures/)"
)


def test_no_shipped_prose_cites_a_path_outside_the_plugin(texts):
    """These files ship to a repo where nothing above the plugin exists. The
    monorepo has a `docs/` and a `templates/` that the split leaves behind, and
    citing one reads fine here and 404s there."""
    prose = {"README.md": (PLUGIN / "README.md").read_text()}
    prose.update({f"skills/{name}/SKILL.md": text for name, text in texts.items()})
    offenders = {
        name: sorted(set(OUTSIDE.findall(text)))
        for name, text in prose.items()
        if OUTSIDE.search(text)
    }
    assert not offenders, f"paths that do not survive the split: {offenders}"


def test_the_readme_links_nowhere_outside_the_published_tree():
    readme = (PLUGIN / "README.md").read_text()
    outside = re.findall(r"\]\((?:\.\./|/)[^)]*\)", readme)
    assert not outside, f"links outside the published tree: {outside}"
