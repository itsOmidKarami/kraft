"""`kraft repo connect --verify`: a repo's declared commands, rehearsed once in
a throwaway worktree."""

from __future__ import annotations

import subprocess
from pathlib import Path

from support.harness import commit_all, make_repo

from kraft.cli import verify


def _entry(repo, **fields) -> dict:
    return {"path": str(repo), "setup_command": "", "test_command": None, **fields}


def _verify(entry, **kw) -> tuple[bool, str]:
    lines: list[str] = []
    return verify.verify(entry, say=lines.append, **kw), "\n".join(lines)


def _repo(tmp_path, files: dict[str, str] | None = None, name: str = "sample") -> Path:
    repo = make_repo(tmp_path, name=name)
    for rel, text in (files or {}).items():
        (repo / rel).write_text(text)
    commit_all(repo)
    return repo


def _worktrees(repo) -> int:
    listed = subprocess.run(
        ["git", "-C", str(repo), "worktree", "list", "--porcelain"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return listed.count("worktree ")


def test_setup_runs_first_in_a_fresh_worktree_and_the_tests_see_it(tmp_path):
    repo = _repo(tmp_path, {".gitignore": "prepared\n"})
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
    repo = _repo(tmp_path)
    ok, said = _verify(_entry(repo, test_command="sh -c 'echo boom >&2; exit 3'"))
    assert not ok
    assert "FAILED" in said and "boom" in said
    assert said.endswith("verify: failed -- fix the entry in repos.yaml and run again")
    assert _worktrees(repo) == 1


def test_a_failed_setup_runs_no_tests(tmp_path):
    repo = _repo(tmp_path)
    ok, said = _verify(_entry(repo, setup_command="exit 1", test_command="true"))
    assert not ok
    assert "the tests were not run" in said
    assert "test [**]" not in said


def test_a_test_program_that_is_not_installed_is_a_failure_not_a_crash(tmp_path):
    ok, said = _verify(_entry(_repo(tmp_path), test_command="no-such-runner-xyz"))
    assert not ok
    assert "No such file" in said


def test_an_undeclared_setup_or_test_command_fails_verify(tmp_path):
    repo = _repo(tmp_path)
    ok, said = _verify({"path": str(repo), "setup_command": None, "test_command": "true"})
    assert not ok and "setup: none declared" in said
    ok, said = _verify(_entry(repo))
    assert not ok and "tests: none declared" in said


def test_a_repo_declaring_no_tests_passes(tmp_path):
    ok, said = _verify(_entry(_repo(tmp_path), test_command=""))
    assert ok, said
    assert 'tests: "" (this repo declares no tests)' in said


def test_local_files_reach_the_worktree_as_they_would_a_work_items(tmp_path):
    repo = _repo(tmp_path, {".gitignore": ".python-version\n"})
    (repo / ".python-version").write_text("3.12\n")
    entry = _entry(repo, local_files=[".python-version"], test_command="test -f .python-version")
    ok, said = _verify(entry)
    assert ok, said


def test_a_local_file_the_repo_does_not_ignore_is_refused_as_a_worker_refuses_it(tmp_path):
    repo = _repo(tmp_path)
    (repo / ".env").write_text("X=1\n")
    ok, said = _verify(_entry(repo, local_files=[".env"], test_command="true"))
    assert not ok
    assert "local_files: .env not carried" in said


def test_an_areas_setup_runs_before_its_scope(tmp_path):
    repo = _repo(tmp_path, {".gitignore": "web-ready\n"})
    area = {
        "paths": ["web/**"],
        "setup": "touch web-ready",
        "verification": {"test_scopes": [{"paths": ["web/**"], "command": "test -f web-ready"}]},
    }
    ok, said = _verify(_entry(repo, test_command="true", areas={"web": area}))
    assert ok, said


def test_a_step_that_leaves_files_a_commit_would_take_fails(tmp_path):
    """Every work item's `git add -A` would put them in its merge request:
    a new file nobody ignored, or a tracked file the step rewrote."""
    repo = _repo(tmp_path)
    entry = _entry(repo, setup_command="touch build.out", test_command="sh -c 'echo >> calc.py'")
    ok, said = _verify(entry)
    assert not ok
    assert "setup: touch build.out ...\n    passed" in said
    assert "left files every work item would commit: build.out." in said
    assert "left files every work item would commit: calc.py." in said


def test_a_step_that_does_not_finish_is_killed_with_its_children(tmp_path):
    repo = _repo(tmp_path)
    pidfile = tmp_path / "child.pid"
    command = f"sh -c 'sleep 60 & echo $! > {pidfile}; wait'"
    ok, said = _verify(_entry(repo, test_command=command), timeout_minutes=0.02)
    assert not ok
    assert "TIMED OUT" in said and "watch mode" in said
    status = Path(f"/proc/{pidfile.read_text().strip()}/status")
    assert not status.exists() or "zombie" in status.read_text(), "the child was left running"


def test_it_rehearses_the_commit_a_work_item_starts_from(tmp_path):
    """origin's default branch: a commit not pushed yet is not in a worktree."""
    origin = _repo(tmp_path, name="origin")
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True)
    (clone / "local-only").write_text("")
    commit_all(clone, "not pushed")
    ok, said = _verify(_entry(clone, test_command="test ! -f local-only"))
    assert ok, said
    assert "a fresh worktree of refs/remotes/origin/main" in said


def test_a_sandboxed_repo_is_rehearsed_on_the_host_only_when_asked(tmp_path):
    repo = _repo(tmp_path)
    entry = _entry(repo, test_command="true", sandbox={"kind": "docker", "image": "x:1"})
    ok, said = _verify(entry)
    assert not ok and "pass --on-host" in said
    ok, said = _verify(entry, on_host=True)
    assert ok, said
