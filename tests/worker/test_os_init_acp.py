"""The `os` launcher for an ACP agent: it speaks on the client's own stdin and stdout."""

# ruff: noqa: F811 -- `launcher` is an imported fixture, taken by name.
from worker.test_os_init import init_argv, launcher, py, run  # noqa: F401


def test_a_split_launch_keeps_stdin_attached_and_stdout_and_stderr_apart(launcher, tmp_path):
    started = tmp_path / "started"
    code = (
        "import sys; line = sys.stdin.readline(); print(line.upper(), end='', flush=True); "
        "print('diag', file=sys.stderr, flush=True)"
    )
    r = run(launcher("launch", init_argv(*py(code)), started=started, split=True), input="hi\n")
    assert (r.returncode, r.stdout, r.stderr) == (0, "HI\n", "diag\n")
    assert started.read_text() == "started\n"
