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
        (["addons/a/library.yaml"], False),
        ([".kraft/collection.json"], False),
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
        "kraft-collection-addon",
        "kraft-collection-manifest",
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
    collection = any(p.startswith(("addons/", ".kraft/")) for p in changed)
    assert ("tests/addons/test_kraft_sdlc_collection.py" in generated.argv) == collection
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


_TCSS = "src/kraft/console/kraft.tcss"
_POLICY = "config/policy.yaml"
_SKILL = "plugins/kraft/skills/board/SKILL.md"


@pytest.mark.parametrize(
    ("path", "then", "now", "adds"),
    [
        (_POLICY, "a: 1\n", "# what a is\na: 1\n\n# more\n", "comment"),
        (_POLICY, "a: 1\n", "a: 2\n", None),
        (_POLICY, "a: 1\n", "a: 1\n\n", None),
        (_POLICY, b"a: 1\n# \xff\n", "a: 1\n", None),
        # Equal data, but a test that `replace`s "a: [1, 2]" would miss it.
        (_POLICY, "a: [1, 2]\n", "a:\n- 1\n- 2\n", None),
        # A `#` line inside a block scalar is text, not a comment.
        (_POLICY, "a: |\n  x\n", "a: |\n  x\n  # y\n", None),
        ("config/.kraft/addon.json", '{"a": 1}\n', '# c\n{"a": 1}\n', None),
        (_POLICY, "a: [\n", "# c\na: [\n", None),
        # Comments alone, so only the add or delete itself makes it blind.
        (_POLICY, None, "# new\n", None),
        (_POLICY, "# gone", None, None),
        # Read as text by the fixture nearly every test uses.
        ("src/kraft/harnesses/claude.yaml", "a: 1\n", "# c\na: 1\n", None),
        ("src/kraft/prices.json", '{\n"a": 1}\n', '{\n\n"a": 1}\n', ()),
        (_TCSS, "Screen {}\n", "Screen { color: red; }\n", "edit"),
        (_TCSS, None, "Screen {}\n", None),
        (_TCSS, "Screen {}\n", None, None),
        (_SKILL, "---\nname: board\n---\n", "---\nname: board\n---\nMore.\n", "edit"),
        (_SKILL, None, "---\nname: board\n---\n", None),
    ],
    ids=[
        "comment-added-to-config-yaml",
        "value-changed",
        "trailing-blank-line-at-eof",
        "not-utf-8-at-the-base",
        "reformatted-to-equal-data",
        "hash-line-inside-block-scalar",
        "unparseable-at-one-end",
        "unparseable-at-both-ends",
        "file-added",
        "file-deleted",
        "comment-in-a-tree-read-as-text",
        "blank-line-in-json-outside-config",
        "mapped-file-edited",
        "mapped-file-added",
        "mapped-file-deleted",
        "plugin-skill-edited",
        "plugin-skill-added",
    ],
)
def test_a_blind_change_with_known_readers_adds_them_instead_of_the_full_suite(
    tt, tmp_path, path, then, now, adds
):
    repo = make_repo(tmp_path)
    if isinstance(then, bytes):
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_bytes(then)
    elif then is not None:
        write(repo, path, then)
    base = commit_all(repo, "base")
    if now is None:
        (repo / path).unlink()
    else:
        write(repo, path, now)

    plan = tt.plan([path], base, repo)

    if adds is None:
        assert [step.argv[-1] for step in plan.steps] == ["--testmon-noselect"]
        assert path in plan.reason
        return
    assert plan.reason is None
    if adds == "comment":
        expected = dict(tt.COMMENT_TESTS)["config"]
        # Readers that print or `replace` the raw text, found only in review.
        assert {"tests/cli/test_admin.py", "tests/api/test_skip.py"} <= set(expected)
    elif adds == "edit":
        expected = next(t for prefix, t in tt.EDIT_TESTS if path.startswith(prefix))
    else:
        expected = adds
    named = plan.steps[0].argv
    start = 3 + len(tt.GENERATED_TESTS)
    assert named[start : named.index("--no-testmon")] == list(expected)
    assert (named[-4:] == ["-m", "not e2e", "-n", "logical"]) == bool(expected)


def test_no_base_or_no_yaml_parser_leaves_a_comment_only_change_blind(tt, tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    write(repo, _POLICY, "a: 1\n")
    base = commit_all(repo, "base")
    write(repo, _POLICY, "# c\na: 1\n")
    assert tt.plan([_POLICY], base, repo).reason is None

    assert _POLICY in tt.plan([_POLICY], None, repo).reason
    monkeypatch.setitem(sys.modules, "yaml", None)  # `import yaml` raises ImportError
    assert _POLICY in tt.plan([_POLICY], base, repo).reason


@pytest.mark.parametrize(
    ("env", "expected"),
    [({"KRAFT_TEST_BASE": "abc123"}, "abc123"), ({}, "merge-base")],
    ids=["kraft-hands-the-base", "else-origin-mains-merge-base"],
)
def test_the_base_is_krafts_else_the_merge_base(tt, tmp_path, env, expected):
    repo = make_repo(tmp_path)
    first = commit_all(repo, "first")
    git(repo, "update-ref", "refs/remotes/origin/main", first)
    write(repo, "x.txt", "later")
    commit_all(repo, "later")
    assert tt.read_base(env, repo) == (first if expected == "merge-base" else expected)


def test_run_judges_against_the_base_kraft_hands_it(tt, tmp_path, monkeypatch, capsys):
    repo = make_repo(tmp_path)
    write(repo, _POLICY, "a: 1\n")
    base = commit_all(repo, "base")
    write(repo, _POLICY, "# c\na: 1\n")
    listing = write(tmp_path, "changed-files", f"{_POLICY}\n")
    monkeypatch.setenv("KRAFT_CHANGED_FILES", str(listing))
    monkeypatch.setenv("KRAFT_TEST_BASE", base)
    monkeypatch.chdir(repo)

    assert tt.main(["run", "--dry-run"]) == 0
    assert capsys.readouterr().out.startswith("test-targeted: testmon selection")
