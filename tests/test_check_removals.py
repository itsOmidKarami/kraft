"""`dev/check_removals.py` (Kraft-79382): a PR that removes a test or an
intent `## REQ` has to say so in its body, or CI fails. Pinned here because
nothing else runs it except CI on a real pull request."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "dev" / "check_removals.py"


@pytest.fixture(scope="module")
def cr():
    spec = importlib.util.spec_from_file_location("_dev_check_removals", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_tests_in_names_functions_and_class_methods_but_not_helpers(cr):
    source = (
        "def helper(): pass\n"
        "def test_a(): pass\n"
        "async def test_b(): pass\n"
        "class TestC:\n    def test_d(self): pass\n    def helper(self): pass\n"
        "class Helper:\n    def test_e(self): pass\n"
    )
    assert cr.tests_in("t/test_x.py", source) == {
        "t/test_x.py::test_a",
        "t/test_x.py::test_b",
        "t/test_x.py::TestC::test_d",
    }


def test_a_test_file_that_does_not_parse_is_a_failure_not_nothing_removed(cr):
    with pytest.raises(SyntaxError):
        cr.tests_in("t/test_x.py", "def test_a(:\n")


def test_declared_reads_only_the_lines_under_each_removed_heading(cr):
    body = (
        "## Summary\n- tests/test_x.py::test_mentioned_in_passing\n\n"
        "## Removed tests\n- `tests/test_x.py::test_a` -- replaced by test_b\n"
        "* tests/test_y.py\n\n"
        "### Removed requirements\n- some-req superseded\n\n"
        "## Test plan\n- other-req\n"
    )
    assert cr.declared(body) == {
        "tests": {"tests/test_x.py::test_a", "tests/test_y.py"},
        "requirements": {"some-req"},
    }


def test_a_removal_inside_an_html_comment_is_not_declared(cr):
    """The PR template's example sections sit in a comment a reviewer never
    sees; left there, they must not count."""
    template = (_SCRIPT.parents[1] / ".github" / "PULL_REQUEST_TEMPLATE.md").read_text()
    assert cr.declared(template) == {"tests": set(), "requirements": set()}
    body = "<!--\n## Removed tests\n- tests/test_x.py::test_a\n-->\n## Removed tests\n- t/b.py\n"
    assert cr.declared(body)["tests"] == {"t/b.py"}


@pytest.mark.parametrize(
    ("opening", "closing"),
    [("```markdown", "```"), ("~~~", "~~~"), ("````", "`````"), ("```", None)],
    ids=["backticks", "tildes", "a-longer-close", "never-closed"],
)
def test_a_removed_tests_heading_inside_a_code_fence_declares_nothing(cr, opening, closing):
    """A PR body that shows the wildcard as an example, fenced under a literal
    `## Removed tests`, is read as an example the way a reviewer reads it: the
    removal is still missing and no wildcard problem is printed."""
    removed = {"tests/test_n.py::test_get_notify_with_a"}
    example = (
        f"## Summary\nFor example:\n\n{opening}\n## Removed tests\n"
        "- tests/test_n.py::test_get_notify_with_a\n"
        "- tests/test_n.py::test_cluster_* -- folded into tests/test_n.py::test_folded\n"
    )
    body = example + (f"{closing}\n" if closing else "")
    assert cr.declared(body) == {"tests": set(), "requirements": set()}
    assert cr.undeclared(removed, set(), set(), body, set())["tests"] == sorted(removed)
    assert cr.wildcard_problems(removed, body, set()) == []
    if closing:  # a real section after the fence still declares
        live = body + "\n## Removed tests\n- tests/test_n.py::test_get_notify_with_a\n"
        assert cr.undeclared(removed, set(), set(), live, set())["tests"] == []


@pytest.mark.parametrize(
    ("body", "missing"),
    [
        ("", {"tests": ["t/test_x.py::test_a"], "requirements": ["req-a"]}),
        (
            "## Removed tests\n- t/test_x.py::test_a\n## Removed requirements\n- req-a\n",
            {"tests": [], "requirements": []},
        ),
        # A file that is only edited is not declared by its path: that would
        # cover every test it loses, including ones the author never noticed.
        (
            "## Removed tests\n- t/test_x.py\n## Removed requirements\n- req-a\n",
            {"tests": ["t/test_x.py::test_a"], "requirements": []},
        ),
        # The two headings are separate lists: a REQ under the tests heading
        # is not declared.
        (
            "## Removed tests\n- t/test_x.py::test_a\n- req-a\n",
            {"tests": [], "requirements": ["req-a"]},
        ),
    ],
    ids=["nothing-declared", "each-one-listed", "an-edited-files-path", "under-the-wrong-heading"],
)
def test_undeclared_names_every_removal_the_body_leaves_out(cr, body, missing):
    assert cr.undeclared({"t/test_x.py::test_a"}, set(), {"req-a"}, body) == missing


def test_a_deleted_files_tests_are_declared_by_its_path(cr):
    removed = {"t/test_gone.py::test_a", "t/test_gone.py::test_b", "ui/A.test.tsx"}
    deleted = {"t/test_gone.py", "ui/A.test.tsx"}
    body = "## Removed tests\n- t/test_gone.py\n- ui/A.test.tsx\n"
    assert cr.undeclared(removed, deleted, set(), body) == {"tests": [], "requirements": []}


def _git(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit_all(repo, message):
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", message)
    return _git(repo, "rev-parse", "HEAD")


def test_the_check_fails_a_silent_revert_and_passes_once_it_is_declared(
    cr, repo, tmp_path, monkeypatch, capsys
):
    """End to end on a real git history: a deleted test, a renamed one, a
    deleted frontend test file and a dropped REQ are all named, a REQ moved to
    another file is not, and the block the failure prints, pasted into the
    body, is exactly what passes."""
    (repo / "test_kept.py").write_text("def test_stays(): pass\ndef test_goes(): pass\n")
    (repo / "test_renamed.py").write_text("def test_old_name(): pass\n")
    (repo / "ui").mkdir()
    (repo / "ui" / "A.test.tsx").write_text("it('works', () => {})\n")
    (repo / "a.md").write_text("## REQ dropped\n\ntext\n\n## REQ moved\n\ntext\n")
    (repo / "b.md").write_text("# other\n")
    base = _commit_all(repo, "base")
    _git(repo, "checkout", "-qb", "pr")
    (repo / "test_kept.py").write_text("def test_stays(): pass\n")
    (repo / "test_renamed.py").write_text("def test_new_name(): pass\n")
    (repo / "ui" / "A.test.tsx").unlink()
    (repo / "a.md").write_text("# nothing left\n")
    (repo / "b.md").write_text("# other\n\n## REQ moved\n\ntext\n")
    _commit_all(repo, "the PR")
    monkeypatch.chdir(repo)
    body = tmp_path / "body.md"

    body.write_text("## Summary\nA small change.\n")
    assert cr.main(["check_removals.py", base, str(body)]) == 1
    printed = capsys.readouterr().out
    block = printed[printed.index("## Removed tests") :]
    assert block.splitlines() == [
        "## Removed tests",
        "- test_kept.py::test_goes",
        "- test_renamed.py::test_old_name",
        "- ui/A.test.tsx",
        "",
        "## Removed requirements",
        "- dropped",
        "",
    ]

    body.write_text(f"## Summary\nA small change.\n\n{block}")
    assert cr.main(["check_removals.py", base, str(body)]) == 0


def test_a_binary_file_in_the_change_does_not_crash_the_check(cr, repo, tmp_path, monkeypatch):
    """Every changed file is read at both revisions; an added PNG (PR #233's
    extension icon) is not UTF-8 and must not crash the check."""
    (repo / "a.md").write_text("# doc\n")
    base = _commit_all(repo, "base")
    _git(repo, "checkout", "-qb", "pr")
    (repo / "icon.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\xff\xfe")
    _commit_all(repo, "add an icon")
    monkeypatch.chdir(repo)
    body = tmp_path / "body.md"
    body.write_text("## Summary\nAn icon.\n")
    assert cr.main(["check_removals.py", base, str(body)]) == 0


_PARAMETRIZED = (
    "import pytest\n"
    "@pytest.mark.parametrize('x', [1, pytest.param(2, id='two')], ids=['one', None])\n"
    "@pytest.mark.parametrize('y', ['a', -1])\n"
    "def test_p(x, y): pass\n"
)


def test_tests_in_names_each_parametrize_case_as_pytest_does(cr):
    """Stacked decorators multiply, nearest the function first; an `ids=`
    entry, a `pytest.param(id=)` and a constant's own text each name one."""
    assert cr.tests_in("t/test_x.py", _PARAMETRIZED) == {
        "t/test_x.py::test_p",
        "t/test_x.py::test_p[a-one]",
        "t/test_x.py::test_p[a-two]",
        "t/test_x.py::test_p[-1-one]",
        "t/test_x.py::test_p[-1-two]",
    }


def test_a_dropped_parametrize_case_is_a_removal(cr, repo, tmp_path, monkeypatch, capsys):
    """`ids=["ok", "refused", "secret"]` cut to `["ok"]` loses two cases while
    the function stays; each has to be declared by its own id, and the test
    removed whole is declared by its id alone, cases and all."""
    (repo / "test_cases.py").write_text(
        "import pytest\n"
        "@pytest.mark.parametrize('v', [1, 2, 3], ids=['ok', 'refused', 'secret'])\n"
        "def test_kept(v): pass\n"
        "@pytest.mark.parametrize('v', [1, 2])\n"
        "def test_gone(v): pass\n"
    )
    base = _commit_all(repo, "base")
    _git(repo, "checkout", "-qb", "pr")
    (repo / "test_cases.py").write_text(
        "import pytest\n@pytest.mark.parametrize('v', [1], ids=['ok'])\ndef test_kept(v): pass\n"
    )
    _commit_all(repo, "drop cases")
    monkeypatch.chdir(repo)
    body = tmp_path / "body.md"
    body.write_text("## Removed tests\n- test_cases.py::test_kept -- not the cases\n")
    assert cr.main(["check_removals.py", base, str(body)]) == 1
    printed = capsys.readouterr().out
    assert printed[printed.index("## Removed tests") :].splitlines()[:5] == [
        "## Removed tests",
        "- test_cases.py::test_gone",
        "- test_cases.py::test_kept[refused]",
        "- test_cases.py::test_kept[secret]",
        "",
    ]
    body.write_text(
        "## Removed tests\n- test_cases.py::test_kept[refused]\n"
        "- test_cases.py::test_kept[secret]\n- test_cases.py::test_gone\n"
    )
    assert cr.main(["check_removals.py", base, str(body)]) == 0


@pytest.mark.parametrize(
    ("after", "skipped"),
    [
        ("@pytest.mark.skip(reason='later')\ndef test_a(): pass\n", True),
        ("@pytest.mark.skip\ndef test_a(): pass\n", True),
        ("pytestmark = pytest.mark.skip\ndef test_a(): pass\n", True),
        ("@pytest.mark.skipif(False, reason='never')\ndef test_a(): pass\n", False),
        ("def test_a():\n    assert 1\n", False),
    ],
    ids=["skip-reason", "bare-skip", "module-pytestmark", "skipif", "unmarked"],
)
def test_a_test_newly_marked_skip_is_a_removal(cr, repo, tmp_path, monkeypatch, after, skipped):
    """A skipped test pins nothing, the same as a deleted one; a `skipif`
    still runs wherever its condition is false, so it does not count."""
    (repo / "test_skip.py").write_text("import pytest\ndef test_a(): pass\n")
    base = _commit_all(repo, "base")
    _git(repo, "checkout", "-qb", "pr")
    (repo / "test_skip.py").write_text(f"import pytest\n{after}")
    _commit_all(repo, "skip it")
    monkeypatch.chdir(repo)
    removed, _, _ = cr.removals(base, "HEAD")
    assert removed == ({"test_skip.py::test_a"} if skipped else set())


def test_a_removed_tests_heading_ending_in_a_colon_still_declares(cr):
    body = "## Removed tests:\n- t/test_x.py::test_a\n### Removed requirements:\n- req-a\n"
    assert cr.declared(body) == {"tests": {"t/test_x.py::test_a"}, "requirements": {"req-a"}}
    assert cr.declared("## Removed tests, mostly\n- t/test_x.py::test_a\n")["tests"] == set()


#: A fold's replacement, standing at HEAD, for the wildcard tests below.
_FOLDED = "tests/test_n.py::test_get_notify_reports_a_bad_config_file_cleanly"


@pytest.mark.parametrize(
    ("name", "left"),
    [
        ("test_get_notify_with_*", ["without_a_file"]),
        ("test_get_notify_with_a*", ["without_a_file"]),
        ("test_get_notify_with_a_bad_yaml[*", ["with_a_missing_file", "without_a_file"]),
        (
            "test_get_notify_with_a_bad_yaml[refused]*",
            ["with_a_bad_yaml[a-tab]", "with_a_missing_file", "without_a_file"],
        ),
    ],
    ids=["the-cluster", "a-longer-prefix", "one-tests-cases", "a-whole-case-id"],
)
def test_a_wildcard_declaration_covers_every_removed_test_with_that_prefix_in_that_file(
    cr, name, left
):
    """`[` in a declared prefix is a literal, not fnmatch's character class:
    `test_y[refused]*` covers `test_y[refused]`, not `test_yr`."""
    removed = {
        f"tests/test_n.py::test_get_notify_{t}"
        for t in [
            "with_a_bad_yaml[refused]",
            "with_a_bad_yaml[a-tab]",
            "with_a_missing_file",
            "without_a_file",
        ]
    }
    body = f"## Removed tests\n- tests/test_n.py::{name} -- folded into {_FOLDED}[missing]\n"
    assert cr.undeclared(removed, set(), set(), body, {_FOLDED}) == {
        "tests": [f"tests/test_n.py::test_get_notify_{t}" for t in left],
        "requirements": [],
    }
    assert cr.wildcard_problems(removed, body, {_FOLDED}) == []


def test_a_wildcard_covers_nothing_in_another_file(cr):
    """The path stays literal: a wildcard across files would hide a revert."""
    removed = {
        "tests/test_n.py::test_get_notify_with_a",
        "tests/test_other.py::test_get_notify_with_a",
        "tests/test_n.pyi::test_get_notify_with_a",
    }
    body = f"## Removed tests\n- tests/test_n.py::test_get_notify_with_* -- into {_FOLDED}\n"
    assert cr.undeclared(removed, set(), set(), body, {_FOLDED})["tests"] == [
        "tests/test_n.pyi::test_get_notify_with_a",
        "tests/test_other.py::test_get_notify_with_a",
    ]


@pytest.mark.parametrize(
    "pattern",
    ["tests/test_gone*", "tests/*.py", "tests/*.py::test_get_notify_with_a"],
    ids=["a-path-prefix", "a-path-glob", "a-glob-in-the-path-part"],
)
def test_a_wildcard_with_no_double_colon_is_not_a_declaration(cr, pattern):
    removed = {"tests/test_gone.py::test_get_notify_with_a"}
    deleted = {"tests/test_gone.py"}
    body = f"## Removed tests\n- {pattern} -- folded into {_FOLDED}\n"
    assert cr.undeclared(removed, deleted, set(), body, {_FOLDED})["tests"] == sorted(removed)
    [problem] = cr.wildcard_problems(removed, body, {_FOLDED})
    assert problem.startswith(f"- {pattern} -- ") and "path stays literal" in problem
    # A deleted file is still declared by its literal path.
    literal = "## Removed tests\n- tests/test_gone.py -- the feature went\n"
    assert cr.undeclared(removed, deleted, set(), literal, set())["tests"] == []
    assert cr.wildcard_problems(removed, literal, set()) == []


def test_a_wildcard_that_matches_nothing_is_reported_as_unused(cr):
    """A typo in the prefix must not pass silently, even when every removal is
    declared some other way."""
    removed = {"tests/test_n.py::test_get_notify_with_a"}
    typo = "tests/test_n.py::test_get_notfy_with_*"
    body = f"## Removed tests\n- tests/test_n.py::test_get_notify_with_a\n- {typo} -- {_FOLDED}\n"
    assert cr.undeclared(removed, set(), set(), body, {_FOLDED})["tests"] == []
    [problem] = cr.wildcard_problems(removed, body, {_FOLDED})
    assert problem.startswith(f"- {typo} -- ") and "matches no test this PR removes" in problem


@pytest.mark.parametrize(
    "pattern",
    [
        "tests/test_n.py::test_*",
        "tests/test_n.py::test*",
        "tests/test_n.py::*",
        "tests/test_n.py::TestNotify::test_*",
    ],
    ids=["test_", "test", "empty", "a-classes-methods"],
)
def test_a_wildcard_with_a_bare_test_prefix_is_refused(cr, pattern):
    removed = {
        "tests/test_n.py::test_get_notify_with_a",
        "tests/test_n.py::TestNotify::test_b",
    }
    body = f"## Removed tests\n- {pattern} -- folded into {_FOLDED}\n"
    assert cr.undeclared(removed, set(), set(), body, {_FOLDED})["tests"] == sorted(removed)
    [problem] = cr.wildcard_problems(removed, body, {_FOLDED})
    assert problem.startswith(f"- {pattern} -- ") and "longer than `test_`" in problem


@pytest.mark.parametrize(
    ("replacement", "passes"),
    [
        ("tests/test_n.py::test_folded[a-case]", True),
        ("`tests/test_n.py::test_folded`.", True),
        ("tests/test_n.py::test_never_written", False),
        ("tests/test_n.py::test_cluster_one", False),  # it existed at base, not at HEAD
        ("tests/test_n.py", False),
        ("the new parametrized test", False),
    ],
    ids=["parametrized", "quoted", "a-typo", "one-of-the-removed", "a-path-alone", "prose"],
)
def test_a_wildcard_must_name_a_replacement_that_exists_at_head(
    cr, repo, tmp_path, monkeypatch, capsys, replacement, passes
):
    """End to end, so the replacement is looked up in the head tree. A refused
    wildcard declares nothing, so the ready-to-paste block still lists the
    tests it meant to cover."""
    (repo / "tests").mkdir(exist_ok=True)
    (repo / "tests" / "test_n.py").write_text(
        "def test_cluster_one(): pass\ndef test_cluster_two(): pass\n"
    )
    base = _commit_all(repo, "base")
    _git(repo, "checkout", "-qb", "pr")
    (repo / "tests" / "test_n.py").write_text("def test_folded(): pass\n")
    _commit_all(repo, "fold the cluster")
    monkeypatch.chdir(repo)
    body = tmp_path / "body.md"
    pattern = "tests/test_n.py::test_cluster_*"
    body.write_text(f"## Removed tests\n- {pattern} -- folded into {replacement}\n")

    assert cr.main(["check_removals.py", base, str(body)]) == (0 if passes else 1)
    printed = capsys.readouterr().out
    if not passes:
        assert f"- {pattern} -- name the test that replaces" in printed
        block = printed[printed.index("## Removed tests") :]
        assert block.splitlines()[:3] == [
            "## Removed tests",
            "- tests/test_n.py::test_cluster_one",
            "- tests/test_n.py::test_cluster_two",
        ]
