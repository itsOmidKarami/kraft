"""server.log: one line per record, the terminal still Rich (Kraft-zrf14.20)."""

from __future__ import annotations

import io
import logging
import re
import sys

import pytest
from rich.console import Console
from rich.logging import RichHandler

from kraft import cli


@pytest.mark.parametrize("terminal", [True, False], ids=["foreground", "detached"])
def test_server_log_is_one_line_per_record_and_terminal_keeps_rich(tmp_path, monkeypatch, terminal):
    """The MCP SDK's RichHandler wraps a record to the terminal's width; the
    file must not get that. A traceback stays whole after its record's line."""
    monkeypatch.setattr(sys, "stdout", sys.stdout)
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    shown = sys.stderr
    log_path = tmp_path / "server.log"
    if terminal:
        cli.admin._redirect_output_to_log(log_path)
    else:
        cli.admin._log_plain(open(log_path, "a"), terminal=False)
    handler = RichHandler(console=Console(stderr=True, width=60), rich_tracebacks=False)
    logger = logging.getLogger("test_serverlog")
    logger.propagate = False
    logger.addHandler(handler)
    try:
        try:
            raise ValueError("boom")
        except ValueError:
            logger.error("a very long message " * 5, exc_info=True)
        logger.warning("second")
        print("kraft: plain line", file=sys.stderr, flush=True)
    finally:
        logger.removeHandler(handler)
    lines = log_path.read_text().splitlines()
    first = lines[0]
    assert re.fullmatch(
        r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d ERROR test_admin_serverlog.py:\d+ "
        r"(a very long message ){5}",
        first,
    )
    assert lines[1] == "Traceback (most recent call last):"
    assert lines[lines.index("ValueError: boom") + 1].endswith(" second")
    assert (lines[-1] == "kraft: plain line") == terminal
    if terminal:  # the terminal still has Rich's wrapped rendering
        out = shown.getvalue()
        assert "ERROR" in out and "a very long message a very long message" not in out
