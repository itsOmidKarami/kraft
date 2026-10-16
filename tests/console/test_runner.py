"""`kraft.console.runner`: what a typed line means, and the child that runs
it. The children are `python -c` scripts standing in for `kraft`, except one
real `python -m kraft` with no server."""

from __future__ import annotations

import asyncio
import os
import signal

import pytest

from kraft.console import runner


@pytest.mark.parametrize(
    ("line", "does"),
    [
        ("/item resume a91c", ("run", ["item", "resume", "a91c"])),
        ("kraft item resume a91c", ("run", ["item", "resume", "a91c"])),
        ("/kraft view show", ("run", ["view", "show"])),
        ('/item create "two words"', ("run", ["item", "create", "two words"])),
        ('/item create "unclosed', ("say", "That line does not parse: No closing quotation.")),
        ("/admin", ("show", "server")),
        ("/repo", ("show", "repos")),
        ("/addon", ("show", "addons")),
        ("/admin addon", ("show", "addons")),
        ("/view", ("show", "board")),
        ("/item", ("show", "board")),
        ("/admin start", ("say", runner.ATTACHED)),
        ("kraft", ("say", runner.ATTACHED)),
        ("/admin start --detach", ("run", ["admin", "start", "--detach"])),
        ("/admin mcp", ("say", runner.REFUSED[("admin", "mcp")])),
        ("/admin permission-hook codex", ("say", runner.REFUSED[("admin", "permission-hook")])),
        ("/console", ("say", runner.REFUSED[("console",)])),
        ("/view watch", ("show", "board")),
        ("/view logs -f a91c", ("say", runner.FOLLOW_LATER)),
        ("/view events a91c --follow", ("say", runner.FOLLOW_LATER)),
        ("/view logs a91c", ("run", ["view", "logs", "a91c"])),
        ("/admin update -y --restart --yes", ("run", ["admin", "update", "--restart"])),
        ("/admin update --ye --restart", ("run", ["admin", "update", "--restart"])),
        ("/view logs a91c --fol", ("say", runner.FOLLOW_LATER)),
        ("/admin start --det", ("run", ["admin", "start", "--det"])),
        ("/view logs x -fn 5", ("say", runner.FOLLOW_LATER)),
        ("/view logs x -fn5", ("say", runner.FOLLOW_LATER)),
        ("/admin update -yy --restart", ("run", ["admin", "update", "--restart"])),
        ("/admin start -dd", ("run", ["admin", "start", "-dd"])),
    ],
    ids=[
        "slash",
        "pasted-kraft",
        "slash-and-kraft",
        "quoted",
        "unclosed-quote",
        "bare-admin",
        "bare-repo",
        "bare-addon",
        "bare-admin-addon",
        "bare-view",
        "bare-item",
        "start-attached",
        "bare-kraft-serves",
        "start-detached",
        "mcp",
        "permission-hook",
        "console",
        "watch",
        "logs-follow",
        "events-follow",
        "logs-without-follow",
        "update-never-yes",
        "update-yes-abbrev",
        "follow-abbrev",
        "detach-abbrev",
        "follow-bundled",
        "follow-bundled-attached",
        "update-yes-bundled",
        "detach-bundled",
    ],
)
def test_a_typed_line_runs_opens_a_view_or_says_why_not(line, does):
    """Spec §4.7 and §7.7. A server started inside the console would stop
    when it quits; `admin update -y` would skip the refusals a person must
    see; a followed stream belongs in a view, not an endless panel."""
    assert runner.route(line) == does


@pytest.mark.parametrize(
    ("argv", "asked"),
    [
        (["admin", "restart", "-y"], ("This stops the server and starts it again.", "restarting")),
        (["admin", "stop"], ("This stops the server.", "stopping")),
        (["admin", "update"], ("This installs the newest Kraft release.", None)),
        (
            ["admin", "update", "--restart"],
            ("This installs the newest Kraft release.", "restarting"),
        ),
        (["repo", "disconnect"], ("This disconnects the repo from Kraft.", None)),
        (
            ["item", "cancel", "a91c"],
            ("This cancels the item; its worktree and branch stay.", None),
        ),
        (["item", "abandon", "a91c"], None),
        (["item", "abandon", "a91c", "--yes"], runner.CONFIRMS[("item", "abandon")]),
        (["item", "abandon", "a91c", "--ye"], runner.CONFIRMS[("item", "abandon")]),
        (["item", "archive", "--y"], runner.CONFIRMS[("item", "archive")]),
        (
            ["admin", "update", "--res"],
            ("This installs the newest Kraft release.", "restarting"),
        ),
        (["item", "archive", "--yes"], runner.CONFIRMS[("item", "archive")]),
        (["admin", "addon", "uninstall", "x@y"], ("This uninstalls the add-on.", None)),
        (["admin", "addon", "list"], None),
        (["view", "show"], None),
    ],
    ids=[
        "restart",
        "stop",
        "update",
        "update-restart",
        "disconnect",
        "cancel",
        "abandon-preview",
        "abandon",
        "abandon-abbrev",
        "archive-abbrev",
        "update-restart-abbrev",
        "archive",
        "addon-uninstall",
        "addon-list",
        "a-read",
    ],
)
def test_the_console_asks_before_what_cannot_be_undone(argv, asked):
    """Spec §7.8: the CLI's own question is skipped without a terminal. A
    stop lists the running items it ends; `abandon` and `archive` without
    `--yes` only preview, so they need no question."""
    assert runner.ask(argv) == asked


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
            "grandchild = subprocess.Popen([sys.executable, '-c', 'import signal, time; "
            'signal.signal(signal.SIGINT, signal.SIG_IGN); print("ready", flush=True); '
            "time.sleep(30)'], stdout=subprocess.PIPE)\n"
            # It is ignoring SIGINT once it has said so.
            "grandchild.stdout.readline()",
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
