"""`kraft.adapters.agent` on codex's sandbox (Kraft-rs9pk).

codex's workspace-write sandbox writes only the worktree and /tmp. A worker
must also write $KRAFT_RESULT_PATH, in `run_dirs.results`, and commit, which
writes the main checkout's `.git`. So Kraft hands a harness that declares
`writable_dirs` both, on every launch. And `codex exec resume` rejects `-s`
and `--add-dir` after `resume`, so every codex option must be a form the
resume path parses too."""

import json
import subprocess

# The `run` fixture's (tests/adapters/conftest.py) stand-in `run_dirs.results`.
RESULTS = "/kraft/run/results"
ROOTS = "sandbox_workspace_write.writable_roots="

#: The only options `codex exec resume` accepts that codex.yaml can use
#: (`codex exec resume --help`, codex-cli 0.155.0).
RESUME_FLAGS = {"-c", "--json", "-m"}


def _roots(cmd: list[str]) -> list[str]:
    (arg,) = [a for a in cmd if a.startswith(ROOTS)]
    return json.loads(arg.removeprefix(ROOTS))


def test_a_codex_worktree_launch_may_write_its_results_and_its_git_dir(run, repo, tmp_path):
    """From a linked worktree, the git dir a commit writes is the main
    checkout's `.git`, not anything under the worktree."""
    worktree = tmp_path / "wt"
    subprocess.run(
        ["git", "worktree", "add", "-q", "-b", "wt", str(worktree)], cwd=repo, check=True
    )

    cmd = run(harness="codex", command="codex", cwd=str(worktree))["cmd"]

    assert _roots(cmd) == [RESULTS, str((repo / ".git").resolve())]
    # Ahead of the bare prompt, which a preceding flag would otherwise swallow.
    assert cmd[-1] == "do the thing"


def test_outside_a_repo_only_the_results_dir_is_granted(run, tmp_path):
    assert _roots(run(harness="codex", command="codex", cwd=str(tmp_path))["cmd"]) == [RESULTS]


def test_a_harness_without_writable_dirs_is_given_no_directory(run):
    assert not any(RESULTS in a for a in run(harness="claude")["cmd"])


def test_every_codex_option_parses_on_resume(run):
    cmd = run(
        harness="codex",
        command="codex",
        model="gpt-5",
        effort="high",
        permission_mode="read-only",
        resume_session_id="abc-123",
    )["cmd"]
    assert cmd[:4] == ["codex", "exec", "resume", "abc-123"]
    flags = {a for a in cmd[4:-1] if a.startswith("-")}
    assert flags <= RESUME_FLAGS, flags - RESUME_FLAGS
    assert "sandbox_mode=read-only" in cmd
