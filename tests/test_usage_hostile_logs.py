"""A session log is the worker's to write: its CLI prints it, and the worker
can print to the same stdout. No line of it may stop Kraft reading the rest,
or recording the session's exit and spend."""

import pytest

from kraft import usage
from kraft.adapters import agent

#: Deeper than the JSON parser recurses: `json.loads` raises RecursionError,
#: which is neither OSError nor ValueError.
_DEEP = "[" * 100_000

_PARTS = ("stream", "envelope", "rate_limit", "session_id", "unfinished_jobs")


@pytest.mark.parametrize(
    ("reader", "part"),
    [(r, p) for r in usage.READERS for p in _PARTS if getattr(usage.READERS[r], p, None)],
)
def test_every_log_reader_skips_a_line_nested_past_the_parser(tmp_path, reader, part):
    log = tmp_path / "s.log"
    log.write_text(f"{_DEEP}\n{{\n")
    read = getattr(usage.READERS[reader], part)
    found = read(log.read_text().splitlines(), {}) if part == "stream" else read(log)
    assert not found  # no line it could read: None, or no unfinished jobs


def test_an_envelope_nested_past_the_parser_leaves_the_status_as_it_was(tmp_path):
    log = tmp_path / "s.log"
    log.write_text(f"{_DEEP}\n")
    assert agent._envelope_is_error("done", log, 0, "claude-stream-json") == "done"
