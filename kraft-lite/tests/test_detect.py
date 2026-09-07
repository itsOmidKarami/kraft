"""init writes a filled-in registry rather than asking ten questions, so the
guesses have to be right — and honest about ambiguity when it exists."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN))

import kl  # noqa: E402


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


def test_installed_skills_are_matched_to_hooks(tmp_path):
    skills = tmp_path / ".claude" / "skills" / "superpowers" / "skills"
    for name in ("brainstorming", "writing-plans", "test-driven-development"):
        (skills / name).mkdir(parents=True)
        (skills / name / "SKILL.md").write_text(f"---\nname: {name}\n---\n")
    (tmp_path / ".claude" / "skills" / "superpowers" / ".claude-plugin").mkdir(parents=True)
    (tmp_path / ".claude" / "skills" / "superpowers" / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "superpowers"})
    )

    found = kl.detect(tmp_path)["skills"]
    assert "superpowers:brainstorming" in found["on.spec.requested"]
    assert "superpowers:writing-plans" in found["on.plan.requested"]
    assert "superpowers:test-driven-development" in found["on.implementation.start"]


def test_a_skill_outside_a_plugin_is_reported_unprefixed(tmp_path):
    plain = tmp_path / ".claude" / "skills" / "code-review"
    plain.mkdir(parents=True)
    (plain / "SKILL.md").write_text("---\nname: code-review\n---\n")
    found = kl.detect(tmp_path)["skills"]
    assert "code-review" in found["on.review.local.run"]


def test_every_hook_in_the_chain_gets_a_key_even_with_nothing_installed(tmp_path, monkeypatch):
    monkeypatch.setattr(kl, "SKILL_ROOTS", (Path(".claude") / "skills",))
    chain = json.loads((PLUGIN / "chains" / "default.json").read_text())
    hooks = {hook for node in chain["nodes"] for hook in node["tasks"]}
    found = kl.detect(tmp_path)["skills"]
    assert set(found) == hooks, "a hook with no key would leave a hole in the registry"
    assert all(candidates == [] for candidates in found.values())


def test_hook_keywords_covers_exactly_the_chains_hooks():
    chain = json.loads((PLUGIN / "chains" / "default.json").read_text())
    hooks = {hook for node in chain["nodes"] for hook in node["tasks"]}
    assert set(kl.HOOK_KEYWORDS) == hooks


def test_keywords_do_not_match_on_short_substrings(tmp_path, monkeypatch):
    """`pr` once matched `compress`, `improver` and `project-artifact`. A keyword
    short enough to appear inside unrelated words offers the human a menu of
    nonsense and buries the real candidate."""
    monkeypatch.setattr(kl, "SKILL_ROOTS", (Path(".claude") / "skills",))
    skills = tmp_path / ".claude" / "skills"
    for name in ("compress", "claude-md-improver", "project-artifact"):
        (skills / name).mkdir(parents=True)
        (skills / name / "SKILL.md").write_text(f"---\nname: {name}\n---\n")
    assert kl.detect(tmp_path)["skills"]["on.mr.open"] == []


def _registry(root, hooks):
    directory = root / kl.STATE_DIR
    directory.mkdir(parents=True, exist_ok=True)
    body = "# a registry\n" + "".join(
        f"{hook}:\n  kind: prompt\n  prompt: do the thing\n  # also found: (none)\n"
        for hook in hooks
    )
    (directory / "registry.yaml").write_text(body)


def test_registry_hooks_reads_the_top_level_keys_only(tmp_path):
    """kl.py may not import yaml, so the scan has to be a regex. Nested keys are
    indented and must not be mistaken for hooks."""
    _registry(tmp_path, ["on.spec.requested", "on.test.run"])
    assert kl.registry_hooks(tmp_path) == {"on.spec.requested", "on.test.run"}


def test_registry_hooks_is_none_when_there_is_no_registry(tmp_path):
    assert kl.registry_hooks(tmp_path) is None


def test_start_rejects_a_chain_whose_hooks_are_not_bound(tmp_path, monkeypatch, capsys):
    """An unbound hook is otherwise only discovered mid-walk, by which point the
    chain has already run its earlier nodes."""
    monkeypatch.setattr(kl.shutil, "which", lambda name: None)
    monkeypatch.chdir(tmp_path)
    _registry(tmp_path, ["on.spec.requested"])
    with pytest.raises(SystemExit) as caught:
        kl.main(["start", "--title", "t"])
    message = str(caught.value)
    assert "on.plan.requested" in message, "the unbound hooks are named"
    assert "on.spec.requested" not in message, "the bound one is not"


def test_start_accepts_the_shipped_chain_against_a_full_registry(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(kl.shutil, "which", lambda name: None)
    monkeypatch.chdir(tmp_path)
    chain = json.loads((PLUGIN / "chains" / "default.json").read_text())
    _registry(tmp_path, sorted({t for n in chain["nodes"] for t in n["tasks"]}))
    kl.main(["start", "--title", "t"])
    assert "chain_id" in json.loads(capsys.readouterr().out)


def test_start_without_a_registry_warns_but_runs(tmp_path, monkeypatch, capsys):
    """The start skill already refuses to run without a registry. Erroring here
    too would only cost every existing test a fixture."""
    monkeypatch.setattr(kl.shutil, "which", lambda name: None)
    monkeypatch.chdir(tmp_path)
    kl.main(["start", "--title", "t"])
    captured = capsys.readouterr()
    assert "chain_id" in json.loads(captured.out)
    assert "registry" in captured.err


def _chain_file(tmp_path, *hooks):
    """A one-node chain naming exactly these hooks."""
    path = tmp_path / "custom.json"
    path.write_text(
        json.dumps(
            {
                "id": "custom",
                "loops": {},
                "nodes": [{"id": "only", "tasks": list(hooks), "gate_after": None}],
            }
        )
    )
    return path


def test_a_custom_hook_gets_candidates_from_its_own_words(tmp_path, monkeypatch):
    monkeypatch.setattr(
        kl, "_installed_skills", lambda root: ["acme:deploy-checklist", "other:unrelated"]
    )
    got = kl.detect(tmp_path, _chain_file(tmp_path, "on.deploy.staging"))
    assert got["skills"] == {"on.deploy.staging": ["acme:deploy-checklist"]}


def test_a_curated_hook_keeps_its_curated_keywords(tmp_path, monkeypatch):
    # `finishing` is not a word in `on.mr.open`; only the curated table knows it.
    monkeypatch.setattr(
        kl, "_installed_skills", lambda root: ["superpowers:finishing-a-development-branch"]
    )
    got = kl.detect(tmp_path, _chain_file(tmp_path, "on.mr.open"))
    assert got["skills"]["on.mr.open"] == ["superpowers:finishing-a-development-branch"]


def test_a_hook_of_only_structural_words_reports_no_candidates(tmp_path, monkeypatch):
    monkeypatch.setattr(kl, "_installed_skills", lambda root: ["anything:at-all"])
    got = kl.detect(tmp_path, _chain_file(tmp_path, "on.run.start"))
    assert got["skills"] == {"on.run.start": []}


def test_the_packaged_chain_still_reports_its_twelve_hooks(tmp_path):
    got = kl.detect(tmp_path)
    assert set(got["skills"]) == set(kl.HOOK_KEYWORDS)
    assert len(got["skills"]) == 12


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
