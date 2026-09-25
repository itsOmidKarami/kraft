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
