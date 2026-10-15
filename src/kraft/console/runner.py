"""Typed commands (spec §4.7): the `kraft` child that runs one.

A command runs the real CLI in a child process, never in this one: Textual's
stdout reports a terminal, so a verb run here would colour its output and
start a pager over the screen, and a thread cannot be stopped."""

from __future__ import annotations

import asyncio
import codecs
import contextlib
import os
import signal
import sys
from collections.abc import Callable

#: What runs `kraft`: this interpreter, `-P` so a file in the cwd named like a
#: module Kraft imports cannot run instead. A test swaps it for a `-c` script.
COMMAND = (sys.executable, "-P", "-m", "kraft")
#: How long a quitting console waits for a command to stop on SIGINT before
#: it kills it.
QUIT_GRACE_S = 3.0


class Run:
    """One `kraft <argv>` child: its own session, so a stop reaches what it
    started (git, a test command) too; no stdin, so nothing waits on a
    question; output streamed to `on_line` one line at a time."""

    def __init__(self, argv: list[str], on_line: Callable[[str], None]) -> None:
        self.argv = argv
        self.on_line = on_line
        self.process: asyncio.subprocess.Process | None = None
        #: The exit code once it has ended; negative when a signal ended it.
        self.code: int | None = None

    async def finish(self) -> int:
        """Start it, pass on its output as it comes, and return its exit code."""
        self.process = await asyncio.create_subprocess_exec(
            *COMMAND,
            *self.argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
            # Unbuffered, or a Python child's output arrives only when it ends;
            # and git must not ask for a password on the console's terminal.
            env={**os.environ, "PYTHONUNBUFFERED": "1", "GIT_TERMINAL_PROMPT": "0"},
        )
        assert self.process.stdout is not None
        # Chunks, not readline(): one `--json` line can outgrow its 64 KiB limit.
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        pending = ""
        while chunk := await self.process.stdout.read(65536):
            *lines, pending = (pending + decoder.decode(chunk)).split("\n")
            for line in lines:
                self.on_line(line)
        pending += decoder.decode(b"", final=True)
        if pending:
            self.on_line(pending)
        self.code = await self.process.wait()
        return self.code

    def stop(self) -> None:
        """SIGINT to its process group: the CLI's own clean-up runs (`repo
        connect --verify` removes its worktree only on an interrupt)."""
        if self.process is not None and self.process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(self.process.pid, signal.SIGINT)

    async def end(self) -> None:
        """Stop it, and kill its group if it has not stopped in `QUIT_GRACE_S`.
        It watches the exit code rather than awaiting `wait()`: on Python 3.12
        (before gh-119710) that also waits for the output pipe to close, which
        a quitting console no longer reads."""
        self.stop()
        for _ in range(round(QUIT_GRACE_S / 0.05)):
            if self.process is None or self.process.returncode is not None:
                return
            await asyncio.sleep(0.05)
        with contextlib.suppress(ProcessLookupError):
            os.killpg(self.process.pid, signal.SIGKILL)
