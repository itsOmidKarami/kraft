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
