"""The output panel: every command this session ran, its output and exit
code; Esc closes it, Ctrl+O reopens it, `x` stops the command, and quitting
stops it too. `kraft` is a `python -c` script here (`kraft_is`)."""

from __future__ import annotations

import os
import signal
import time

import pytest
from rich.color import Color

from kraft.console import output, runner
from kraft.console.output import OutputPanel

#: Prints its arguments, then fails with the code it is given last.
ECHO = "import sys; print(' '.join(sys.argv[1:])); sys.exit(int(sys.argv[-1]))"
#: Up until SIGINT, then the clean-up a CLI does on an interrupt.
WAITS = """
import signal, sys, time
def stop(*_):
    print("cleaned up", flush=True)
    sys.exit(130)
signal.signal(signal.SIGINT, stop)
print("up", flush=True)
time.sleep(30)
"""


@pytest.mark.parametrize(
    ("code", "bad"),
    [(0, False), (3, True)],
    ids=["exit-0", "exit-3"],
)
def test_a_command_shows_its_output_and_exit_code(console, kraft_is, code, bad):
    """Spec §8: a failed command's exit code is in the bad colour, and the
    panel stays open on it."""
    kraft_is(ECHO)

    async def scenario(app, pilot):
        app.run_worker(app.run_command(["view", "list", str(code)]))
        await console.until(pilot, lambda: console.output(app)[-1:] == [f"exit {code}"])
        log = app.screen.query_one("#output-log")
        exit_colour = next(seg.style.color for seg in log.lines[-1] if seg.text.strip())
        return console.output(app), exit_colour, isinstance(app.screen, OutputPanel), app

    lines, exit_colour, open_, app = console.run(scenario)
    assert lines == [f"kraft view list {code}", f"view list {code}", f"exit {code}"]
    assert open_
    assert (exit_colour == Color.parse(app.current_theme.error)) is bad


def test_esc_closes_the_panel_and_leaves_the_command_running(console, kraft_is):
    """Esc only hides it; Ctrl+O brings back everything this session ran;
    `x` stops the command, which cleans up as on Ctrl+C. While it runs, a
    second command waits its turn rather than mixing into its output."""
    kraft_is(WAITS)

    async def scenario(app, pilot):
        app.run_worker(app.run_command(["repo", "connect", "--verify"]))
        await console.until(pilot, lambda: "up" in console.output(app))
        await pilot.press("escape")
        closed = (app.screen is app.default_screen, app.command is not None)
        second = await app.run_command(["view", "list"])
        await pilot.press("ctrl+o")
        await pilot.press("x")
        await console.until(pilot, lambda: app.command is None)
        return closed, second, console.output(app)

    closed, second, lines = console.run(scenario)
    assert closed == (True, True)
    assert second is None
    assert lines == ["kraft repo connect --verify", "up", "cleaned up", "exit 130"]


IGNORES = WAITS.replace(
    "signal.signal(signal.SIGINT, stop)", "signal.signal(signal.SIGINT, signal.SIG_IGN)"
)


def test_a_second_stop_kills_a_command_that_ignores_the_first(console, kraft_is, monkeypatch):
    """`x` is Ctrl+C; pressed again on a command that ignored it, it is the kill."""
    kraft_is(IGNORES)
    monkeypatch.setattr(runner, "QUIT_GRACE_S", 0.3)

    async def scenario(app, pilot):
        app.run_worker(app.run_command(["repo", "connect", "--verify"]))
        await console.until(pilot, lambda: "up" in console.output(app))
        await pilot.press("x")
        await pilot.pause(0.3)
        still_running = app.command is not None
        await pilot.press("x")
        await console.until(pilot, lambda: app.command is None)
        return still_running, console.output(app)

    still_running, lines = console.run(scenario)
    assert still_running
    assert lines == ["kraft repo connect --verify", "up", f"exit {-signal.SIGKILL}"]


def test_quitting_stops_a_running_command(console, kraft_is, tmp_path):
    """Spec §8: SIGINT first, so the CLI's clean-up runs. The child is in its
    own session, so nothing else would ever stop it; and Textual cancels the
    worker awaiting it before the app unmounts, which must not lose it."""
    stopped = tmp_path / "stopped"
    kraft_is(WAITS.replace('print("cleaned up", flush=True)', f"open({str(stopped)!r}, 'w')"))

    async def scenario(app, pilot):
        app.run_worker(app.run_command(["repo", "connect", "--verify"]))
        await console.until(pilot, lambda: "up" in console.output(app))
        return app.command.process.pid

    pid = console.run(scenario)
    try:
        deadline = time.monotonic() + 5
        while not stopped.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert stopped.exists()
    finally:
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def test_a_command_prints_as_it_is_written(console, kraft_is):
    """Markup off: `[/]` is text, not a tag. A CRLF line keeps its words."""
    kraft_is(r"import sys; sys.stdout.write('[/] [bold]x[/bold] [red]\ncrlf line\r\n')")

    async def scenario(app, pilot):
        app.run_worker(app.run_command(["view", "list"]))
        await console.until(pilot, lambda: console.output(app)[-1:] == ["exit 0"])
        return console.output(app)

    assert console.run(scenario)[1:-1] == ["[/] [bold]x[/bold] [red]", "crlf line"]


def test_the_panel_keeps_only_its_newest_lines(console, kraft_is, monkeypatch):
    monkeypatch.setattr(output, "MAX_LINES", 3)
    kraft_is("print('a'); print('b'); print('c'); print('d')")

    async def scenario(app, pilot):
        app.run_worker(app.run_command(["view", "list"]))
        await console.until(pilot, lambda: console.output(app)[-1:] == ["exit 0"])
        return console.output(app)

    assert console.run(scenario) == ["c", "d", "exit 0"]


def test_the_stop_button_interrupts_the_command(console, kraft_is):
    kraft_is(WAITS)

    async def scenario(app, pilot):
        app.run_worker(app.run_command(["repo", "connect", "--verify"]))
        await console.until(pilot, lambda: "up" in console.output(app))
        enabled = not app.screen.query_one("#stop").disabled
        await pilot.click("#stop")
        await console.until(pilot, lambda: app.command is None)
        return enabled, app.screen.query_one("#stop").disabled, console.output(app)

    assert console.run(scenario) == (
        True,
        True,
        ["kraft repo connect --verify", "up", "cleaned up", "exit 130"],
    )
