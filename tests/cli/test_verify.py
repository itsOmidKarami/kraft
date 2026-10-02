"""`kraft repo connect --verify`: a repo's declared commands, rehearsed once in
a throwaway worktree."""

from __future__ import annotations

import subprocess

from support.harness import make_repo

from kraft.cli import verify


def _entry(repo, **fields) -> dict:
    return {"path": str(repo), "setup_command": "", "test_command": None, **fields}


def _verify(entry) -> tuple[bool, str]:
    lines: list[str] = []
    return verify.verify(entry, say=lines.append), "\n".join(lines)


def _worktrees(repo) -> int:
    listed = subprocess.run(
        ["git", "-C", str(repo), "worktree", "list", "--porcelain"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return listed.count("worktree ")


def test_setup_runs_first_in_a_fresh_worktree_and_the_tests_see_it(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "untracked.txt").write_text("not in a worktree")
    entry = _entry(
        repo,
        setup_command="touch prepared",
        test_scopes=[{"paths": ["**"], "command": "test -f prepared -a ! -f untracked.txt"}],
    )
    ok, said = _verify(entry)
    assert ok, said
    assert "setup: touch prepared ...\n    passed" in said
    assert _worktrees(repo) == 1, "the throwaway worktree is removed"


def test_a_failing_test_fails_verify_showing_its_output(tmp_path):
    repo = make_repo(tmp_path)
    ok, said = _verify(_entry(repo, test_command="sh -c 'echo boom >&2; exit 3'"))
    assert not ok
    assert "FAILED" in said and "boom" in said
    assert said.endswith("verify: failed -- fix the entry in repos.yaml and run again")
    assert _worktrees(repo) == 1


def test_a_failed_setup_runs_no_tests(tmp_path):
    repo = make_repo(tmp_path)
    ok, said = _verify(_entry(repo, setup_command="exit 1", test_command="true"))
    assert not ok
    assert "the tests were not run" in said
    assert "test [**]" not in said


def test_a_test_program_that_is_not_installed_is_a_failure_not_a_crash(tmp_path):
    ok, said = _verify(_entry(make_repo(tmp_path), test_command="no-such-runner-xyz"))
    assert not ok
    assert "No such file" in said


def test_an_undeclared_setup_or_test_command_fails_verify(tmp_path):
    repo = make_repo(tmp_path)
    ok, said = _verify({"path": str(repo), "setup_command": None, "test_command": "true"})
    assert not ok and "setup: none declared" in said
    ok, said = _verify(_entry(repo))
    assert not ok and "tests: none declared" in said


def test_a_repo_declaring_no_tests_passes(tmp_path):
    ok, said = _verify(_entry(make_repo(tmp_path), test_command=""))
    assert ok, said
    assert 'tests: "" (this repo declares no tests)' in said


def test_local_files_reach_the_worktree_as_they_would_a_work_items(tmp_path):
    repo = make_repo(tmp_path)
    (repo / ".gitignore").write_text(".python-version\n")
    (repo / ".python-version").write_text("3.12\n")
    entry = _entry(repo, local_files=[".python-version"], test_command="test -f .python-version")
    ok, said = _verify(entry)
    assert ok, said


def test_an_areas_setup_runs_before_its_scope(tmp_path):
    repo = make_repo(tmp_path)
    area = {
        "paths": ["web/**"],
        "setup": "touch web-ready",
        "verification": {"test_scopes": [{"paths": ["web/**"], "command": "test -f web-ready"}]},
    }
    ok, said = _verify(_entry(repo, test_command="true", areas={"web": area}))
    assert ok, said
