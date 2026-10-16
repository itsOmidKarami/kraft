"""Typed commands (spec §4.7): what a line typed at the prompt means, and the
`kraft` child that runs it.

A command runs the real CLI in a child process, never in this one: Textual's
stdout reports a terminal, so a verb run here would colour its output and
start a pager over the screen, and a thread cannot be stopped."""

from __future__ import annotations

import asyncio
import codecs
import contextlib
import os
import shlex
import signal
import sys
from collections.abc import Callable

#: What runs `kraft`: this interpreter, `-P` so a file in the cwd named like a
#: module Kraft imports cannot run instead. A test swaps it for a `-c` script.
COMMAND = (sys.executable, "-P", "-m", "kraft")
#: How long a quitting console waits for a command to stop on SIGINT before
#: it kills it.
QUIT_GRACE_S = 3.0

#: Typed lines refused before anything runs, and why (by their first words).
REFUSED = {
    ("admin", "mcp"): "`admin mcp` serves an agent over stdin; `admin init` registers it.",
    ("admin", "permission-hook"): "A harness's hook runs `admin permission-hook`, not a person.",
    ("console",): "This is the console.",
}
#: `admin start` without `--detach`, and bare `kraft`, which serves: the server
#: would run inside the console and stop when it quits.
ATTACHED = "The server would stop with the console; `/admin start --detach` starts it on its own."
#: A bare group, and the view it opens (spec §7.7).
GROUPS = {"admin": "server", "repo": "repos", "addon": "addons", "view": "board", "item": "board"}
#: Verbs whose `-f` follows a stream the Item view shows.
FOLLOWED = {("view", "logs"), ("view", "events")}
FOLLOW_LATER = "Following arrives with the Item screen; leave out -f to print what is there now."

#: The typed verbs the console asks about before they run (spec §7.8): what
#: going on does, and the word for what ends the agents of running items, for
#: the ones that do.
CONFIRMS = {
    ("admin", "restart"): ("This stops the server and starts it again.", "restarting"),
    ("admin", "stop"): ("This stops the server.", "stopping"),
    ("admin", "update"): ("This installs the newest Kraft release.", None),
    ("repo", "disconnect"): ("This disconnects the repo from Kraft.", None),
    ("item", "cancel"): ("This cancels the item; its worktree and branch stay.", None),
    ("item", "archive"): ("This deletes the worktrees; uncommitted changes are lost.", None),
    ("item", "abandon"): (
        "This deletes the worktree and branch; uncommitted work and unpushed commits are lost.",
        None,
    ),
    ("admin", "addon", "uninstall"): ("This uninstalls the add-on.", None),
}


def route(line: str) -> tuple[str, str | list[str]]:
    """What a typed line does: `("run", argv)` for `kraft <argv>`, `("show",
    view)` to open a view instead, or `("say", sentence)` to refuse it. A
    leading `/` and a leading `kraft` are dropped, so a sentence the CLI
    printed ("kraft item resume a91c") can be pasted back."""
    try:
        words = shlex.split(line.strip().removeprefix("/"))
    except ValueError as exc:
        return "say", f"That line does not parse: {exc}."
    if words[:1] == ["kraft"]:
        words = words[1:]
    head = tuple(words[:2])
    if words in (["addon"], ["admin", "addon"]):
        return "show", "addons"
    if len(words) == 1 and words[0] in GROUPS:
        return "show", GROUPS[words[0]]
    if not words or (head == ("admin", "start") and not {"-d", "--detach"} & set(words)):
        return "say", ATTACHED
    for prefix, why in REFUSED.items():
        if tuple(words[: len(prefix)]) == prefix:
            return "say", why
    if head == ("view", "watch"):
        return "show", "board"
    if head in FOLLOWED and {"-f", "--follow"} & set(words):
        return "say", FOLLOW_LATER
    if head == ("admin", "update"):
        # Never `-y`: its own refusals (add-ons it would drop, a 0.x config) still show.
        words = [word for word in words if word not in ("-y", "--yes")]
    return "run", words


def ask(argv: list[str]) -> tuple[str, str | None] | None:
    """What the console asks before running `kraft <argv>`: the sentence, and
    the word for a stop that ends running agents (None: lists no items). None
    when it runs without asking."""
    found = next((v for k, v in CONFIRMS.items() if tuple(argv[: len(k)]) == k), None)
    if found is None:
        return None
    sentence, doing = found
    if argv[:2] in (["item", "archive"], ["item", "abandon"]) and "--yes" not in argv:
        return None  # without --yes the CLI only previews; nothing is lost
    if argv[:2] == ["admin", "update"] and "--restart" in argv:
        doing = "restarting"
    return sentence, doing


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
        self._stopped = False

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
        if self._stopped:  # asked to stop while it was starting
            self.stop()
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
        connect --verify` removes its worktree only on an interrupt). Until
        the run has finished, not until the leader has exited: what it started
        can outlive it and still hold the output open."""
        self._stopped = True
        self._signal(signal.SIGINT)

    def _signal(self, sig: int) -> None:
        if self.process is not None and self.code is None:
            # PermissionError: macOS, for a group whose only member is a zombie.
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(self.process.pid, sig)

    def _alive(self) -> bool:
        """Whether any member of its group is still there."""
        if self.process is None or self.code is not None:
            return False
        try:
            os.killpg(self.process.pid, 0)
        except (ProcessLookupError, PermissionError):
            return False
        return True

    async def end(self) -> None:
        """Stop it, and kill its group if it has not stopped in `QUIT_GRACE_S`,
        even when the leader is gone and a child of it is not. It polls the
        group rather than awaiting `wait()`: on Python 3.12 (before gh-119710)
        that also waits for the output pipe to close, which a quitting console
        no longer reads."""
        self.stop()
        for _ in range(round(QUIT_GRACE_S / 0.05)):
            if not self._alive():
                return
            await asyncio.sleep(0.05)
        self._signal(signal.SIGKILL)
