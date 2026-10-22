"""init writes the bindings from what `detect` finds rather than asking ten
questions, so the guesses have to be right -- and honest about ambiguity when
it exists."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import kl
import kl_bindings
import kl_chains

PLUGIN = Path(__file__).resolve().parents[1]


def test_a_justfile_test_recipe_wins(tmp_path):
    (tmp_path / "justfile").write_text("build:\n    echo hi\n\ntest *ARGS:\n    pytest {{ARGS}}\n")
    (tmp_path / "Makefile").write_text("test:\n\tmake-test\n")
    assert kl.detect(tmp_path)["test_command"] == ["just", "test"]


def test_a_makefile_target_is_next(tmp_path):
    (tmp_path / "Makefile").write_text("all:\n\tbuild\n\ntest:\n\tpytest\n")
    assert kl.detect(tmp_path)["test_command"] == ["make", "test"]


def test_package_json_scripts_are_next(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "vitest"}}))
    assert kl.detect(tmp_path)["test_command"] == ["npm", "test"]


def test_a_pyproject_falls_back_to_pytest(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    assert kl.detect(tmp_path)["test_command"] == ["pytest", "-q"]


def test_an_unrecognised_repo_reports_no_command_rather_than_guessing(tmp_path):
    assert kl.detect(tmp_path)["test_command"] is None


def test_a_makefile_without_a_test_target_is_not_a_match(tmp_path):
    (tmp_path / "Makefile").write_text("all:\n\tbuild\n")
    assert kl.detect(tmp_path)["test_command"] is None


def _install(tmp_path, *names, plugin=None):
    """SKILL.md files under the repo's `.claude/skills`, inside `plugin`'s
    manifest when one is named."""
    base = tmp_path / ".claude" / "skills"
    if plugin:
        (base / plugin / ".claude-plugin").mkdir(parents=True)
        (base / plugin / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": plugin}))
        base = base / plugin / "skills"
    for name in names:
        (base / name).mkdir(parents=True)
        (base / name / "SKILL.md").write_text(f"---\nname: {name}\n---\n")


def _chain_file(tmp_path, *task_ids):
    """A one-node chain of inline agent tasks with exactly these ids."""
    path = tmp_path / "custom.yaml"
    tasks = "".join(
        f"      - id: {t}\n        kind: agent\n        prompt: Do {t}.\n" for t in task_ids
    )
    path.write_text(f"id: custom\nnodes:\n  - id: only\n    kind: exec\n    tasks:\n{tasks}")
    return path


def test_installed_skills_are_matched_to_tasks(tmp_path):
    _install(
        tmp_path,
        "brainstorming",
        "writing-plans",
        "test-driven-development",
        plugin="superpowers",
    )
    found = kl.detect(tmp_path)["candidates"]["tasks"]
    assert "superpowers:brainstorming" in found["spec_author"]
    assert "superpowers:writing-plans" in found["plan_author"]
    assert "superpowers:test-driven-development" in found["implementer"]


def test_a_skill_outside_a_plugin_is_reported_unprefixed(tmp_path):
    _install(tmp_path, "code-review")
    assert "code-review" in kl.detect(tmp_path)["candidates"]["tasks"]["code_review"]


def test_every_task_in_the_chain_gets_a_key_even_with_nothing_installed(tmp_path, monkeypatch):
    monkeypatch.setattr(kl, "SKILL_ROOTS", (Path(".claude") / "skills",))
    chain = kl_chains.resolve(tmp_path, {}, "default")
    tasks = [t for n in chain["nodes"] if n["kind"] == "exec" for t in _chain_tasks(n)]
    found = kl.detect(tmp_path)["candidates"]
    assert set(found["tasks"]) == {t["components"][0] for t in tasks if t["kind"] == "agent"}
    assert set(found["builtins"]) == {t["ref"] for t in tasks if t["kind"] == "builtin"}
    assert set(found["forge"]) == {t["target"] for t in tasks if t["kind"] == "forge"}
    assert all(not c for section in found.values() for c in section.values()), (
        "a task with no key would leave a hole in the bindings"
    )


def _chain_tasks(node):
    rejected = node["on_merge_rejected"]
    handlers = (node, node["on_failure"], node["fix_loop"], rejected and rejected["repair"])
    judge = [node["fix_loop"]["judge"]] if node["fix_loop"] and node["fix_loop"]["judge"] else []
    return [t for c in handlers if c for s in c["steps"] for t in s["tasks"]] + judge


def test_task_keywords_cover_the_library():
    """A component with no `TASK_KEYWORDS` entry silently falls back to splitting
    its own name into keywords -- the behaviour meant for a repo's own tasks,
    not one the bundled library curated on purpose."""
    _, library = kl_chains.libraries(PLUGIN, {})
    agents = {n for n, raw in library.components["tasks"].items() if raw["kind"] == "agent"}
    expected = agents | set(kl_chains.BUILTIN_REFS) | set(kl_chains.FORGE_TARGETS)
    assert set(kl.TASK_KEYWORDS) == expected


@pytest.mark.parametrize(
    ("skills", "task", "expected"),
    [
        # `pr` once matched `compress`, `improver` and `project-artifact`: a keyword
        # inside unrelated words offers the human a menu of nonsense.
        (("compress", "claude-md-improver", "project-artifact"), "describe_mr", []),
        (("explanation-style", "airplane-mode", "plans-index"), "plan_author", ["plans-index"]),
        (("pr-description", "mr-metadata"), "describe_mr", ["mr-metadata", "pr-description"]),
    ],
    ids=["short_keyword_inside_a_word", "long_keyword_inside_a_word", "short_keyword_whole_word"],
)
def test_keywords_match_whole_words_only(tmp_path, monkeypatch, skills, task, expected):
    monkeypatch.setattr(kl, "SKILL_ROOTS", (Path(".claude") / "skills",))
    _install(tmp_path, *skills)
    assert kl.detect(tmp_path)["candidates"]["tasks"][task] == expected


def test_a_tasks_own_installed_skill_is_a_candidate(tmp_path, monkeypatch):
    # Nothing in the name `deploy` suggests `acme:zzz`; only the task's own `skill` does.
    monkeypatch.setattr(kl, "_installed_skills", lambda root: ["acme:zzz"])
    chain = tmp_path / "own.yaml"
    chain.write_text(
        "id: own\nnodes:\n  - id: only\n    kind: exec\n    tasks:\n"
        "      - id: deploy\n        kind: agent\n        prompt: Deploy.\n"
        "        skill: acme:zzz\n"
    )
    got = kl.detect(tmp_path, str(chain))
    assert got["candidates"]["tasks"] == {"only.main.deploy": ["acme:zzz"]}
    assert got["proposed"] == {}


def test_a_custom_task_gets_candidates_from_its_own_words(tmp_path, monkeypatch):
    monkeypatch.setattr(
        kl, "_installed_skills", lambda root: ["acme:deploy-checklist", "other:unrelated"]
    )
    got = kl.detect(tmp_path, str(_chain_file(tmp_path, "deploy_staging")))
    assert got["candidates"]["tasks"] == {"only.main.deploy_staging": ["acme:deploy-checklist"]}


def test_a_curated_target_keeps_its_curated_keywords(tmp_path, monkeypatch):
    # `finishing` is not a word in `mr.open_draft`; only the curated table knows it.
    monkeypatch.setattr(
        kl, "_installed_skills", lambda root: ["superpowers:finishing-a-development-branch"]
    )
    got = kl.detect(tmp_path)["candidates"]["forge"]
    assert got["mr.open_draft"] == ["superpowers:finishing-a-development-branch"]


def test_a_task_of_only_structural_words_reports_no_candidates(tmp_path, monkeypatch):
    monkeypatch.setattr(kl, "_installed_skills", lambda root: ["anything:at-all"])
    got = kl.detect(tmp_path, str(_chain_file(tmp_path, "run_start")))
    assert got["candidates"]["tasks"] == {"only.main.run_start": []}


@pytest.mark.parametrize(
    ("chain", "expected"),
    [
        pytest.param(None, "default", id="the_default_chain"),
        pytest.param("quick-task", "quick-task", id="a_library_id"),
        pytest.param("PATH", "custom", id="a_chain_file"),
    ],
)
def test_detect_resolves_its_chain_like_start(tmp_path, kl_cli, chain, expected):
    path = _chain_file(tmp_path, "one")
    given = [] if chain is None else ["--chain", str(path) if chain == "PATH" else chain]
    args = ["detect", *given]
    assert kl_cli(tmp_path, *args)["chain"] == expected


def test_detect_refuses_a_chain_that_does_not_exist(tmp_path, kl_cli):
    refused = kl_cli(tmp_path, "detect", "--chain", "no-such-chain", ok=False)
    assert "no-such-chain" in refused.stderr


@pytest.fixture
def skilled(tmp_path, monkeypatch):
    """`detect` over a repo with a test command, `skills` installed (`plugin:name`
    inside that plugin) and `ci` as the CI command."""
    monkeypatch.setattr(kl, "SKILL_ROOTS", (Path(".claude") / "skills",))

    def detect(*skills, test=True, ci=None):
        if test:
            (tmp_path / "Makefile").write_text("test:\n\tpytest\n")
        for skill in skills:
            plugin, _, name = skill.rpartition(":")
            _install(tmp_path, name, plugin=plugin or None)
        monkeypatch.setattr(kl, "_ci_command", lambda root: ci)
        return kl.detect(tmp_path)

    return detect


@pytest.mark.parametrize(
    ("skills", "test", "ci", "found", "expect"),
    [
        pytest.param(
            (),
            True,
            None,
            {},
            {
                "builtins": {
                    "kraft.verify_changed_test_scopes": {
                        "kind": "subprocess",
                        "command": ["make", "test"],
                    }
                }
            },
            id="test_command_becomes_subprocess",
        ),
        pytest.param((), False, None, {}, {}, id="no_test_command_proposes_nothing"),
        pytest.param(
            ("writing-plans",),
            False,
            None,
            {},
            {
                "tasks": {
                    "plan_author": {
                        "kind": "skill",
                        "skill": "writing-plans",
                        "prompt": (
                            "Produce an executable implementation plan from the approved spec."
                        ),
                    }
                }
            },
            id="single_skill_candidate_bound",
        ),
        pytest.param(
            ("writing-plans", "plan-critic"), False, None, {}, {}, id="two_candidates_left_unbound"
        ),
        pytest.param(
            (),
            False,
            ["gh", "pr", "checks"],
            {},
            {"forge": {"mr.ci": {"kind": "subprocess", "command": ["gh", "pr", "checks"]}}},
            id="ci_command_becomes_the_ci_subprocess",
        ),
        # kraft:spec is the library's own skill for spec_author, and it is installed:
        # the default already runs it, so binding it again says nothing.
        pytest.param(
            ("kraft:spec",),
            False,
            None,
            {"spec_author": ["kraft:spec"]},
            {},
            id="task_already_bound_to_its_own_skill_proposes_nothing",
        ),
        pytest.param(
            ("kraft:spec", "acme:spec-writer"),
            False,
            None,
            {"spec_author": ["acme:spec-writer", "kraft:spec"]},
            {},
            id="second_candidate_beside_the_own_skill_left_to_init",
        ),
    ],
)
def test_proposed_bindings(skilled, skills, test, ci, found, expect):
    got = skilled(*skills, test=test, ci=ci)
    assert got["proposed"] == expect
    assert {task: got["candidates"]["tasks"][task] for task in found} == found


def test_the_proposal_passes_the_validator_that_reads_bindings(tmp_path, monkeypatch):
    monkeypatch.setattr(kl, "_installed_skills", lambda root: ["acme:writing-plans"])
    monkeypatch.setattr(kl, "_ci_command", lambda root: ["glab", "ci", "status"])
    (tmp_path / "Makefile").write_text("test:\n\tpytest\n")
    proposed = kl.detect(tmp_path)["proposed"]
    assert set(proposed) == set(kl_bindings.SECTIONS)
    # JSON is YAML, and read_bindings is what `start` runs the file through.
    (tmp_path / ".kraft-lite").mkdir()
    (tmp_path / kl_bindings.BINDINGS).write_text(json.dumps(proposed))
    assert kl_bindings.read_bindings(tmp_path) == proposed


def test_a_gitlab_repo_with_glab_installed_polls_with_glab(tmp_path, monkeypatch):
    (tmp_path / ".gitlab-ci.yml").write_text("stages: [test]\n")
    monkeypatch.setattr(kl.shutil, "which", lambda cli: f"/usr/bin/{cli}")
    assert kl.detect(tmp_path)["ci_command"] == ["glab", "ci", "status"]


def test_a_github_repo_with_gh_installed_polls_with_gh(tmp_path, monkeypatch):
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    monkeypatch.setattr(kl.shutil, "which", lambda cli: f"/usr/bin/{cli}")
    assert kl.detect(tmp_path)["ci_command"] == ["gh", "pr", "checks"]


def test_the_origin_remote_outranks_a_stray_ci_file(tmp_path, monkeypatch):
    # A repo can carry a .github/workflows it no longer uses; origin is the forge
    # whose CI a merge request actually runs on.
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    monkeypatch.setattr(kl, "_origin_url", lambda root: "git@gitlab.com:me/x.git")
    monkeypatch.setattr(kl.shutil, "which", lambda cli: f"/usr/bin/{cli}")
    assert kl.detect(tmp_path)["ci_command"] == ["glab", "ci", "status"]


def test_a_forge_whose_cli_is_missing_reports_no_command(tmp_path, monkeypatch):
    (tmp_path / ".gitlab-ci.yml").write_text("stages: [test]\n")
    monkeypatch.setattr(kl.shutil, "which", lambda cli: None)
    assert kl.detect(tmp_path)["ci_command"] is None


def test_a_repo_with_no_forge_reports_no_command(tmp_path, monkeypatch):
    monkeypatch.setattr(kl, "_origin_url", lambda root: "")
    assert kl.detect(tmp_path)["ci_command"] is None


def test_the_forge_is_read_from_the_host_not_the_repo_name(tmp_path, monkeypatch):
    # A GitHub repo may be named after the other forge -- a mirror, a migration
    # tool. Matching the whole URL picks the wrong client for it.
    monkeypatch.setattr(kl, "_origin_url", lambda root: "git@github.com:me/gitlab-mirror.git")
    monkeypatch.setattr(kl.shutil, "which", lambda cli: f"/usr/bin/{cli}")
    assert kl.detect(tmp_path)["ci_command"] == ["gh", "pr", "checks"]


def test_a_self_hosted_gitlab_host_is_still_gitlab(tmp_path, monkeypatch):
    monkeypatch.setattr(kl, "_origin_url", lambda root: "https://gitlab.example.com/me/x.git")
    monkeypatch.setattr(kl.shutil, "which", lambda cli: f"/usr/bin/{cli}")
    assert kl.detect(tmp_path)["ci_command"] == ["glab", "ci", "status"]
