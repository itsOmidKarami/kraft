"""`kraft.adapters.agent`: the `result_dir` capability (Kraft-rs9pk).

codex's workspace-write sandbox writes only the worktree and /tmp, and
$KRAFT_RESULT_PATH lives in `run_dirs.results`, so Kraft hands a harness that
declares `result_dir` that directory on every launch."""

# The `run` fixture's (tests/adapters/conftest.py) stand-in `run_dirs.results`.
RESULTS = "/kraft/run/results"


def test_a_codex_launch_may_write_its_result_files_directory(run):
    cmd = run(harness="codex", command="codex")["cmd"]
    assert cmd[cmd.index("--add-dir") + 1] == RESULTS
    # Ahead of the bare prompt, which a preceding flag would otherwise swallow.
    assert cmd[-1] == "do the thing"


def test_a_harness_without_result_dir_is_given_no_directory(run):
    assert RESULTS not in run(harness="claude")["cmd"]
