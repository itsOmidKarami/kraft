"""What a candidate plugin may not carry, checked before anything is written."""

import pytest
import yaml
from support.plugins import AGENT, chain, plugin_json

from kraft.plugins import manifest, update


def _files(library=None, chains=None):
    files = {}
    if library is not None:
        files["library.yaml"] = ("100644", yaml.safe_dump(library).encode())
    for name, body in (chains or {}).items():
        files[f"chains/{name}.yaml"] = ("100644", yaml.safe_dump(body).encode())
    return files


def _manifest(**requires):
    data = plugin_json("release", requires={"kraft": "2", "harnesses": ["codex"], **requires})
    return manifest.plugin(data, "plugin.json")


def _task(**fields):
    return {"tasks": {"base": {**AGENT, **fields}}}


def _gate_chain(**gate):
    return {"nodes": [*chain("base")["nodes"], {"id": "ok", "kind": "gate", **gate}]}


JUDGE = {
    "nodes": {
        "fix": {
            "kind": "exec",
            "tasks": [{"id": "t", "extends": "base"}],
            "fix_loop": {
                "tasks": [{"id": "repair", "extends": "base"}],
                "judge": {**AGENT, "id": "judge", "policy": {"grants": ["git-push"]}},
            },
        }
    }
}


@pytest.mark.parametrize(
    ("library", "chains", "why"),
    [
        ({"tasks": {"run": {"kind": "subprocess", "command": "make"}}}, {}, "subprocess task"),
        (_task(policy={"sandbox": {"image": "x"}}), {}, "policy.sandbox is not a limit"),
        (_task(policy={"unrestricted_network": True}), {}, "policy.unrestricted_network"),
        (_task(policy={"grants": ["git-push"]}), {}, "policy.grants is not a limit"),
        (_task(policy={"allowed_tools": ["Bash"]}), {}, "policy.allowed_tools"),
        (_task(policy={"allowed_harnesses": ["codex"]}), {}, "policy.allowed_harnesses"),
        (
            {"tasks": {"base": AGENT}},
            {"ship": {**chain("base"), "policy": {"escalation_harness": "codex"}}},
            "policy.escalation_harness",
        ),
        (_task(policy={"something_new": 1}), {}, "policy.something_new is not a limit"),
        (
            {"tasks": {"base": AGENT}},
            {"ship": _gate_chain(policy={"grants": ["x"]})},
            r"nodes\[ok\]: policy.grants",
        ),
        ({"tasks": {"base": AGENT}, **JUDGE}, {}, r"nodes.fix.fix_loop.judge: policy.grants"),
        (_task(harness="claude"), {}, "harness 'claude' is not listed"),
        (_task(extends="other:base"), {}, "reaches outside the plugin"),
    ],
    ids=[
        "subprocess",
        "sandbox",
        "unrestricted-network",
        "grants",
        "allowed-tools",
        "allowed-harnesses",
        "escalation-harness",
        "unknown-policy-key",
        "gate-scope",
        "judge-task",
        "unlisted-harness",
        "reference-to-another-plugin",
    ],
)
def test_a_plugin_cannot_carry(library, chains, why):
    with pytest.raises(update.Refused, match=why):
        update.check(_manifest(), _files(library, chains))


@pytest.mark.parametrize(
    "policy",
    [{"time_cap_minutes": 90}, {"budget_usd": 8, "token_budget": 100000}, {"deny_tools": ["Bash"]}],
    ids=["time-cap", "budget", "deny-tools"],
)
def test_a_plugin_may_set_limits(policy):
    """Limits are not permissions: how long, how much and how many times are
    the author's to set, and a task named like a field is still a task."""
    library = {"tasks": {"base": {**AGENT, "policy": policy}, "policy": AGENT, "harness": AGENT}}
    update.check(_manifest(), _files(library, {"ship": chain("base")}))


@pytest.mark.parametrize(
    ("skill", "installed", "refused"),
    [
        ("deploy-review", {"mobile"}, False),
        ("release:deploy-review", {"mobile"}, False),
        ("kraft:spec", {"mobile"}, False),
        ("superpowers:brainstorming", {"mobile"}, False),
        ("mobile:deploy-review", {"mobile"}, True),
    ],
    ids=["bare", "own-name", "kraft", "another-tools-plugin", "another-kraft-plugin"],
)
def test_a_skill_may_not_reach_into_another_kraft_plugin(skill, installed, refused):
    found = update.problems(_manifest(), _files(_task(skill=skill)), other_namespaces=installed)
    assert bool(found) is refused
