"""`dev/targeted_tests.py`: which tests Kraft's own `just test-targeted` runs.

CI's lint job does not run the script, so these pin what it decides: a change
testmon cannot see runs the full suite, anything else runs testmon's picks plus
the generated-file tests.
"""

from __future__ import annotations

import importlib.util
import io
import re
import sys
from pathlib import Path

import pytest
from support.harness import commit_all, git, make_repo, write

_SCRIPT = Path(__file__).resolve().parents[1] / "dev" / "targeted_tests.py"


@pytest.fixture(scope="module")
def tt():
    spec = importlib.util.spec_from_file_location("_dev_targeted_tests", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    # A dataclass looks its module up here to resolve its annotations.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    yield mod
    del sys.modules[spec.name]


@pytest.mark.parametrize(
    ("changed", "full"),
    [
        ([], False),
        (["src/kraft/a.py"], False),
        (["src/kraft/templates/lucide_icons.txt"], True),
        (["config/chains/quick-task.yaml"], True),
        (["tests/support/remote.py"], False),
        (["tests/support/fake_agent.py"], True),
        (["tests/support/sample_repo/calc.py"], True),
        (["tests/support/justfile"], True),
        (["fixtures/fake-claude.sh"], True),
        (["docs/intent/x.md"], False),
        (["plugins/kraft/skills/a/SKILL.md"], True),
        (["docsite/content/a.md"], False),
        (["docs/café.md"], False),
        (None, True),
        (["src/kraft/a.py", "config/x.yaml"], True),
    ],
    ids=[
        "empty-list",
        "python-under-src",
        "non-python-under-src",
        "chain-yaml",
        "support-module-imported-in-process",
        "support-script-run-as-subprocess",
        "sample-repo-python",
        "support-non-python",
        "fixtures",
        "intent-doc",
        "skill",
        "a-docs-page",
        "a-non-ascii-path",
        "no-list-at-all",
        "one-blind-among-many",
    ],
)
def test_the_plan_runs_the_full_suite_only_for_a_change_testmon_cannot_see(tt, changed, full):
    plan = tt.plan(changed)
    if full:
        [step] = plan.steps
        # Still recorded, or the next selection grows and never shrinks (Kraft-4t45n.5).
        assert step.argv[1:] == [
            "-m",
            "pytest",
            "-m",
            "not e2e",
            "-n",
            "logical",
            "--testmon",
            "--testmon-noselect",
        ]
        assert step.env == {"COVERAGE_CORE": "ctrace"}
        blind = [p for p in changed or [] if tt.is_blind(p)]
        assert plan.reason and all(p in plan.reason for p in blind)
        if changed is None:
            assert "no merge base" in plan.reason
        return
    assert plan.reason is None
    generated, selected = plan.steps
    assert generated.argv[3 : 3 + len(tt.GENERATED_TESTS)] == list(tt.GENERATED_TESTS)
    # What reads docs/intent testmon cannot trace, so a change there adds it.
    assert (set(tt.INTENT_TESTS) <= set(generated.argv)) == any(map(tt.is_intent, changed))
    assert "--no-testmon" in generated.argv and generated.env == {}
    assert "--testmon-forceselect" in selected.argv and "--testmon" in selected.argv
    assert selected.argv[selected.argv.index("-n") + 1] == "logical"
    assert selected.env == {"COVERAGE_CORE": "ctrace"}


def test_a_long_list_of_blind_paths_names_five_and_counts_the_rest(tt):
    plan = tt.plan([f"config/{n}.yaml" for n in range(8)])
    assert plan.reason == (
        "testmon cannot see a change to config/0.yaml, config/1.yaml, config/2.yaml, "
        "config/3.yaml, config/4.yaml (and 3 more)"
    )


def test_the_blind_command_lists_what_just_test_warns_about(tt, monkeypatch, capsys):
    paths = [
        "src/kraft/a.py",
        "src/kraft/b.txt",
        "tests/support/remote.py",
        "tests/support/stub.txt",
        "tests/support/fake_reviewer.py",
        "docs/intent/x.md",
        "README.md",
    ]
    monkeypatch.setattr(sys, "stdin", io.StringIO("\n".join(paths)))
    assert tt.main(["blind"]) == 0
    assert capsys.readouterr().out.split() == [
        "src/kraft/b.txt",
        "tests/support/stub.txt",
        "tests/support/fake_reviewer.py",
        "docs/intent/x.md",
    ]


def test_every_support_script_a_test_runs_as_a_subprocess_is_blind(tt):
    """testmon traces a support module a test imports, not one a test starts as
    its own process. Finds those by how tests name them: a script's own `__main__`
    or shebang, or a `Path` joined with `support` and the file name. A script
    started another way (`-m support.<name>`) needs adding to BLIND by hand."""
    tests = Path(__file__).parent
    scripts = {
        p.name
        for p in (tests / "support").glob("*.py")
        if re.search(r"^(#!|if __name__ ==)", p.read_text(), re.M)
    }
    for p in tests.rglob("*.py"):
        scripts.update(re.findall(r'/\s*"support(?:"\s*/\s*"|/)(\w+\.py)"', p.read_text()))

    assert {"fake_agent.py", "fake_reviewer.py", "fake_codex_app_server.py"} <= scripts
    assert [n for n in sorted(scripts) if not tt.is_blind(f"tests/support/{n}")] == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [("a\nb\n", ["a", "b"]), ("", [])],
    ids=["the-list-file", "an-empty-list-file"],
)
def test_read_changed_reads_the_list_file(tt, tmp_path, text, expected):
    listing = tmp_path / "changed-files"
    listing.write_text(text)
    assert tt.read_changed({"KRAFT_CHANGED_FILES": str(listing)}, tmp_path) == expected


def test_an_unreadable_list_file_means_unknown(tt, tmp_path):
    assert tt.read_changed({"KRAFT_CHANGED_FILES": str(tmp_path / "missing")}, tmp_path) is None


def test_read_changed_measures_the_branch_against_origin_mains_merge_base(tt, tmp_path):
    repo = make_repo(tmp_path)
    write(repo, "a.txt", "a")
    write(repo, "b.txt", "b")
    write(repo, "café.md", "c")
    first = commit_all(repo, "first")
    git(repo, "update-ref", "refs/remotes/origin/main", first)
    git(repo, "mv", "a.txt", "renamed.txt")
    write(repo, "café.md", "c, changed")
    commit_all(repo, "later")
    write(repo, "b.txt", "b, staged")
    git(repo, "add", "b.txt")
    write(repo, "untracked.txt", "u")

    assert set(tt.read_changed({}, repo)) == {
        "a.txt",
        "renamed.txt",
        "b.txt",
        "café.md",
        "untracked.txt",
    }


def test_read_changed_without_origin_main_is_unknown_not_head(tt, tmp_path):
    repo = make_repo(tmp_path)
    write(repo, "x.txt", "uncommitted")
    assert tt.read_changed({}, repo) is None
