"""Which local skill, prompt or command runs a step: `.kraft-lite/bindings.yaml`
keyed by Kraft's task names, and the dispatch that picks one for a task."""

from __future__ import annotations

import pytest

import kl_bindings
import kl_chains


def task(**fields) -> dict:
    """A frozen chain task: the keys the resolver gives every task, absent ones None."""
    base = dict.fromkeys(("prompt", "skill", "produces", "profile", "model", "effort"))
    base.update(dict.fromkeys(("ref", "target", "command")))
    return {
        "id": "main_task",
        "path": "build.main.main_task",
        "kind": "agent",
        "components": [],
        "steering": [],
        **base,
        **fields,
    }


def prompt(text: str) -> dict:
    return {"kind": "prompt", "prompt": text}


def skill(name: str, text: str = "fallback") -> dict:
    return {"kind": "skill", "skill": name, "prompt": text}


@pytest.mark.parametrize(
    ("chain_task", "bindings", "installed", "slice_layer", "expect"),
    [
        pytest.param(
            task(path="spec.main.author", components=["spec_author"]),
            {
                "tasks": {
                    "spec.main.author": prompt("by path"),
                    "spec_author": prompt("by name"),
                }
            },
            lambda name: True,
            False,
            {
                "kind": "prompt",
                "prompt": "by path",
                "source": "bindings:tasks.spec.main.author",
            },
            id="path_binding_wins",
        ),
        pytest.param(
            task(components=["spec_author"]),
            {"tasks": {"spec_author": prompt("by name")}},
            lambda name: True,
            False,
            {
                "kind": "prompt",
                "prompt": "by name",
                "source": "bindings:tasks.spec_author",
            },
            id="component_binding",
        ),
        pytest.param(
            task(components=["spec_author", "author"]),
            {"tasks": {"author": prompt("by ancestor")}},
            lambda name: True,
            False,
            {
                "kind": "prompt",
                "prompt": "by ancestor",
                "source": "bindings:tasks.author",
            },
            id="ancestor_component_binding",
        ),
        pytest.param(
            task(kind="builtin", ref="kraft.mr_rebase"),
            {"builtins": {"kraft.mr_rebase": prompt("rebase it")}},
            lambda name: True,
            False,
            {
                "kind": "prompt",
                "prompt": "rebase it",
                "source": "bindings:builtins.kraft.mr_rebase",
            },
            id="builtin_binding",
        ),
        pytest.param(
            task(kind="forge", target="mr.ci"),
            {"forge": {"mr.ci": {"kind": "subprocess", "command": ["gh", "pr", "checks"]}}},
            lambda name: True,
            False,
            {
                "kind": "subprocess",
                "command": ["gh", "pr", "checks"],
                "source": "bindings:forge.mr.ci",
            },
            id="forge_binding",
        ),
        pytest.param(
            task(skill="superpowers:brainstorming", prompt="Write a spec."),
            {},
            lambda name: True,
            False,
            {
                "kind": "skill",
                "skill": "superpowers:brainstorming",
                "prompt": "Write a spec.",
                "source": "default:agent",
            },
            id="agent_skill_installed",
        ),
        pytest.param(
            task(skill="superpowers:brainstorming", prompt="Write a spec."),
            {},
            lambda name: False,
            False,
            {"kind": "prompt", "prompt": "Write a spec.", "source": "default:agent"},
            id="agent_skill_missing_uses_prompt",
        ),
        pytest.param(
            task(components=["spec_author"]),
            {"tasks": {"spec_author": skill("gone:skill", "Do it by hand.")}},
            lambda name: False,
            False,
            {
                "kind": "prompt",
                "prompt": "Do it by hand.",
                "source": "bindings:tasks.spec_author (skill missing)",
            },
            id="skill_binding_missing_falls_back",
        ),
        pytest.param(
            task(kind="subprocess", command=["just", "lint"]),
            {},
            lambda name: True,
            False,
            {
                "kind": "subprocess",
                "command": ["just", "lint"],
                "source": "default:subprocess",
            },
            id="subprocess_task",
        ),
        pytest.param(
            task(kind="builtin", ref="kraft.verify_changed_test_scopes"),
            {},
            lambda name: True,
            False,
            {
                "kind": "prompt",
                "prompt": kl_bindings.BUILTIN_DEFAULTS["kraft.verify_changed_test_scopes"][
                    "prompt"
                ],
                "source": "default:builtins.kraft.verify_changed_test_scopes",
            },
            id="builtin_default",
        ),
        pytest.param(
            task(kind="forge", target="mr.merge"),
            {},
            lambda name: True,
            False,
            {
                "kind": "prompt",
                "prompt": kl_bindings.FORGE_DEFAULTS["mr.merge"]["prompt"],
                "source": "default:forge.mr.merge",
            },
            id="forge_default",
        ),
        pytest.param(
            task(prompt="Implement it.", steering=["Be brief.", "No mocks."]),
            {},
            lambda name: False,
            False,
            {
                "kind": "prompt",
                "prompt": "Implement it.\n\nBe brief.\n\nNo mocks.",
                "source": "default:agent",
            },
            id="steering_appended",
        ),
        pytest.param(
            task(components=["reviewer"]),
            {"tasks": {"reviewer": {"kind": "skip", "why": "reviewed elsewhere"}}},
            lambda name: True,
            False,
            {
                "kind": "skip",
                "why": "reviewed elsewhere",
                "source": "bindings:tasks.reviewer",
            },
            id="skip_binding",
        ),
        pytest.param(
            task(components=["implementer"]),
            {
                "tasks": {"implementer": prompt("outer")},
                "slice": {"tasks": {"implementer": prompt("inner")}},
            },
            lambda name: True,
            True,
            {
                "kind": "prompt",
                "prompt": "inner",
                "source": "bindings:slice.tasks.implementer",
            },
            id="slice_layer_user_binding",
        ),
        pytest.param(
            task(components=["implementer"]),
            {"tasks": {"implementer": prompt("outer")}},
            lambda name: True,
            True,
            {
                "kind": "prompt",
                "prompt": kl_bindings.SLICE_DEFAULTS["tasks"]["implementer"]["prompt"],
                "source": "slice-default:tasks.implementer",
            },
            id="slice_layer_default_implementer",
        ),
        pytest.param(
            task(components=["implementer"]),
            {"slice": {"tasks": {"implementer": prompt("inner")}}},
            lambda name: True,
            False,
            {"kind": "prompt", "prompt": None, "source": "default:agent"},
            id="slice_layer_off_ignores_slice_section",
        ),
    ],
)
def test_dispatch(chain_task, bindings, installed, slice_layer, expect):
    got = kl_bindings.dispatch(chain_task, bindings, installed, slice_layer=slice_layer)
    assert {key: got[key] for key in expect} == expect


def test_dispatch_carries_path_produces_and_hints():
    got = kl_bindings.dispatch(
        task(produces="spec", profile="fast", model="m", effort="low", prompt="x"),
        {},
        lambda name: False,
    )
    assert (got["path"], got["produces"], got["hints"]) == (
        "build.main.main_task",
        "spec",
        {"profile": "fast", "model": "m", "effort": "low"},
    )


def test_slice_default_skips_the_automated_review():
    got = kl_bindings.dispatch(
        task(kind="forge", target="mr.automated_review"),
        {},
        lambda name: False,
        slice_layer=True,
    )
    assert (got["kind"], got["why"], got["source"]) == (
        "skip",
        "the controller's PR review is this review",
        "slice-default:forge.mr.automated_review",
    )


def test_every_kraft_ref_and_target_has_a_default():
    assert set(kl_bindings.BUILTIN_DEFAULTS) == set(kl_chains.BUILTIN_REFS)
    assert set(kl_bindings.FORGE_DEFAULTS) == set(kl_chains.FORGE_TARGETS)
    for entry in (
        *kl_bindings.BUILTIN_DEFAULTS.values(),
        *kl_bindings.FORGE_DEFAULTS.values(),
    ):
        assert entry["kind"] == "prompt"
        assert entry["prompt"].strip()


def test_missing_bindings_file_is_empty(tmp_path):
    assert kl_bindings.read_bindings(tmp_path) == {}


def test_bindings_file_is_read(tmp_path):
    (tmp_path / ".kraft-lite").mkdir()
    (tmp_path / kl_bindings.BINDINGS).write_text(
        "tasks:\n"
        "  spec_author:\n"
        "    kind: skill\n"
        "    skill: superpowers:brainstorming\n"
        "    prompt: Write a spec.\n"
        "forge:\n"
        "  mr.ci:\n"
        "    kind: subprocess\n"
        "    command: [gh, pr, checks]\n"
        "slice:\n"
        "  builtins:\n"
        "    kraft.mr_rebase:\n"
        "      kind: skip\n"
        "      why: no rebase in a slice\n"
    )
    got = kl_bindings.read_bindings(tmp_path)
    assert got["tasks"]["spec_author"]["skill"] == "superpowers:brainstorming"
    assert got["forge"]["mr.ci"]["command"] == ["gh", "pr", "checks"]
    assert got["slice"]["builtins"]["kraft.mr_rebase"]["why"] == "no rebase in a slice"


@pytest.mark.parametrize(
    ("text", "names"),
    [
        pytest.param("hooks:\n  a: {}\n", ["hooks"], id="unknown_section"),
        pytest.param(
            "tasks:\n  spec_author:\n    kind: wizardry\n",
            ["tasks.spec_author", "wizardry"],
            id="unknown_kind",
        ),
        pytest.param(
            "forge:\n  mr.ci:\n    kind: subprocess\n",
            ["forge.mr.ci", "command"],
            id="subprocess_without_command",
        ),
        pytest.param(
            "tasks:\n  spec_author:\n    kind: skill\n    skill: a:b\n",
            ["tasks.spec_author", "prompt"],
            id="skill_without_prompt",
        ),
    ],
)
def test_bad_bindings_refused(tmp_path, text, names):
    (tmp_path / ".kraft-lite").mkdir()
    (tmp_path / kl_bindings.BINDINGS).write_text(text)
    with pytest.raises(SystemExit) as refused:
        kl_bindings.read_bindings(tmp_path)
    message = str(refused.value)
    assert message.startswith("kraft-lite: ")
    for name in [".kraft-lite/bindings.yaml", *names]:
        assert name in message
