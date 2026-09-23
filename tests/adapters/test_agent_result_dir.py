"""`kraft.adapters.agent` on codex's sandbox (Kraft-rs9pk).

codex's workspace-write sandbox writes only the worktree and /tmp, and
$KRAFT_RESULT_PATH lives in `run_dirs.results`, so Kraft hands a harness that
declares `result_dir` that directory on every launch. And `codex exec resume`
rejects `-s` and `--add-dir` after `resume`, so every codex option must be a
form the resume path parses too."""

# The `run` fixture's (tests/adapters/conftest.py) stand-in `run_dirs.results`.
RESULTS = "/kraft/run/results"

#: The only options `codex exec resume` accepts that codex.yaml can use
#: (`codex exec resume --help`, codex-cli 0.155.0).
RESUME_FLAGS = {"-c", "--json", "-m"}


def test_a_codex_launch_may_write_its_result_files_directory(run):
    cmd = run(harness="codex", command="codex")["cmd"]
    assert f"sandbox_workspace_write.writable_roots=['{RESULTS}']" in cmd
    # Ahead of the bare prompt, which a preceding flag would otherwise swallow.
    assert cmd[-1] == "do the thing"


def test_a_harness_without_result_dir_is_given_no_directory(run):
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
