"""`kraft.console.runner`: the child that runs a command. The children are
`python -c` scripts standing in for `kraft`, except one real
`python -m kraft` with no server."""

from __future__ import annotations

import asyncio
import os
import signal

import pytest

from kraft.console import runner


async def _started(run: runner.Run, lines: list[str], count: int) -> asyncio.Task:
    """`run` going, once it has printed `count` lines."""
    task = asyncio.create_task(run.finish())
    async with asyncio.timeout(10):
        while len(lines) < count:
            await asyncio.sleep(0.01)
    return task


STOPS_WITH_ITS_CHILD = """
import os, subprocess, sys, time
# No flush: this only arrives before the exit if output is unbuffered.
print(repr(sys.stdin.read()), os.environ["GIT_TERMINAL_PROMPT"])
grandchild = subprocess.Popen([sys.executable, "-c", '''
import time
print("grandchild up", flush=True)
try:
    time.sleep(30)
except KeyboardInterrupt:
    print("grandchild stopped", flush=True)
'''])
try:
    time.sleep(30)
except KeyboardInterrupt:
    grandchild.wait()
    print("child stopped")
    sys.exit(130)
"""


async def test_a_command_runs_with_no_terminal_and_stops_with_what_it_started(kraft_is):
    """Its output streams while it runs; it has no stdin to wait on and git
    cannot prompt; a stop reaches what it started too, or a test command it
    ran would outlive it and hold its output open."""
    kraft_is(STOPS_WITH_ITS_CHILD)
    lines: list[str] = []
    run = runner.Run(["repo", "connect", "--verify"], lines.append)
    # The console's stdin is its terminal. Here it is a pipe with a line
    # waiting, which a child that shared it would read.
    typed, typing = os.pipe()
    os.write(typing, b"typed\n")
    os.close(typing)
    own = os.dup(0)
    os.dup2(typed, 0)
    try:
        task = await _started(run, lines, 2)
    finally:
        os.dup2(own, 0)
        os.close(own)
        os.close(typed)
    assert run.code is None
    run.stop()
    async with asyncio.timeout(10):
        code = await task
    assert lines == ["'' 0", "grandchild up", "grandchild stopped", "child stopped"]
    assert code == 130


async def test_a_long_line_and_an_unended_one_arrive_whole(kraft_is):
    """`--json` prints a whole board on one line, past asyncio's 64 KiB line
    limit; a last line without a newline is output all the same."""
    kraft_is("import sys; sys.stdout.write('x' * 200_000 + '\\nno newline')")
    lines: list[str] = []
    assert await runner.Run([], lines.append).finish() == 0
    assert lines == ["x" * 200_000, "no newline"]


async def test_a_real_kraft_with_no_server_says_so_and_fails():
    lines: list[str] = []
    code = await runner.Run(["view", "show", "a91c"], lines.append).finish()
    assert code == 1
    assert "no Kraft server at" in "\n".join(lines)


WAITS_FOR_SIGINT = """
import signal, sys, time
{on_sigint}
print("up", flush=True)
{then}
"""


@pytest.mark.parametrize(
    ("on_sigint", "then", "code"),
    [
        ("signal.signal(signal.SIGINT, lambda *a: sys.exit(130))", "time.sleep(30)", 130),
        ("signal.signal(signal.SIGINT, signal.SIG_IGN)", "time.sleep(30)", -signal.SIGKILL),
        # The leader is gone at once; what it started ignores SIGINT and holds the output.
        (
            "import subprocess\n"
            "signal.signal(signal.SIGINT, signal.SIG_IGN)\n"
            "subprocess.Popen([sys.executable, '-c', 'import signal, time; "
            "signal.signal(signal.SIGINT, signal.SIG_IGN); time.sleep(30)'])\n"
            # Long enough for it to have started ignoring SIGINT.
            "time.sleep(1)",
            "sys.exit(0)",
            0,
        ),
    ],
    ids=["stops-on-sigint", "ignores-sigint", "leader-gone-child-holds-the-output"],
)
async def test_ending_a_command_interrupts_then_kills(kraft_is, monkeypatch, on_sigint, then, code):
    """Quitting the console: SIGINT first, so the CLI cleans up; a command
    still there after the grace is killed rather than left running."""
    kraft_is(WAITS_FOR_SIGINT.format(on_sigint=on_sigint, then=then))
    monkeypatch.setattr(runner, "QUIT_GRACE_S", 0.3)
    lines: list[str] = []
    run = runner.Run([], lines.append)
    task = await _started(run, lines, 1)
    await run.end()
    async with asyncio.timeout(10):
        assert await task == code
    # Nothing it started is left running.
    with pytest.raises(ProcessLookupError):
        for _ in range(100):
            os.killpg(run.process.pid, 0)
            await asyncio.sleep(0.05)


async def test_a_stop_before_the_child_exists_still_stops_it(kraft_is):
    """Esc or quit while the command is still being started."""
    kraft_is("import time; time.sleep(30)")
    run = runner.Run([], lambda line: None)
    run.stop()
    async with asyncio.timeout(10):
        assert await run.finish() != 0
