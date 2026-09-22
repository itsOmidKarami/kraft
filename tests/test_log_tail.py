"""Kraft-21jy / Kraft-2vvus: a log tail reads each byte once, and every reader is
bounded for a huge log -- with a marker saying so, never silently."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from kraft import logs
from kraft.api.routes import sessions


class _Counting:
    """A binary file handle that adds every byte it hands out to `total`."""

    def __init__(self, fh, total: list[int]):
        self._fh, self._total = fh, total

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._fh.close()

    def __getattr__(self, name):
        return getattr(self._fh, name)

    def read(self, *a):
        data = self._fh.read(*a)
        self._total[0] += len(data)
        return data

    def readline(self, *a):
        data = self._fh.readline(*a)
        self._total[0] += len(data)
        return data


def _count_log_reads(monkeypatch) -> list[int]:
    total = [0]
    real_open = Path.open

    def spy(self, mode="r", *args, **kwargs):
        fh = real_open(self, mode, *args, **kwargs)
        return _Counting(fh, total) if self.suffix == ".log" else fh

    monkeypatch.setattr(Path, "open", spy)
    return total


def test_a_tail_reads_each_byte_once(tmp_path, monkeypatch):
    log = tmp_path / "s.log"
    log.write_bytes(b"one\ntwo\n")
    tail = logs.Tail(log)
    total = _count_log_reads(monkeypatch)

    assert [r["text"] for r in tail.read()] == ["one", "two"]
    assert tail.read() == []  # nothing new, and nothing re-read
    with open(log, "ab") as fh:
        fh.write(b"three\n")
    assert [(r["n"], r["text"]) for r in tail.read()] == [(2, "three")]
    assert total[0] == len(b"one\ntwo\nthree\n")


def test_a_partial_line_waits_for_its_newline_until_the_final_read(tmp_path):
    log = tmp_path / "s.log"
    log.write_bytes(b"one\nhal")
    tail = logs.Tail(log)
    assert [r["text"] for r in tail.read()] == ["one"]
    with open(log, "ab") as fh:
        fh.write(b"f done\nta")
    assert [(r["n"], r["text"]) for r in tail.read()] == [(1, "half done")]
    # the session has ended: a line it never finished still counts
    assert [(r["n"], r["text"]) for r in tail.read(final=True)] == [(2, "ta")]


def test_a_huge_log_returns_its_tail_behind_a_truncation_marker(tmp_path, monkeypatch):
    monkeypatch.setattr(logs, "MAX_LOG_BYTES", 40)
    log = tmp_path / "s.log"
    lines = [f"line {i:02d}" for i in range(20)]  # 7 bytes + newline each
    log.write_text("".join(f"{ln}\n" for ln in lines))
    logs.times_path(log).write_text(
        "".join(json.dumps({"n": n, "t": f"t{n}"}) + "\n" for n in range(20))
    )

    peak = [0]
    real_stamp = logs._stamp

    def stamp(line, times, first):
        real_stamp(line, times, first)
        peak[0] = max(peak[0], len(times))

    monkeypatch.setattr(logs, "_stamp", stamp)

    rows = list(logs.jsonl(log))
    marker, shown = rows[0], rows[1:]
    # the skipped lines' stamps are never held, even for the length of a read
    assert peak[0] == len(shown)
    # 160 bytes, the last 40 of them kept: the window opens on line 15's
    # boundary exactly, so nothing is cut mid-line
    assert [r["n"] for r in shown] == [15, 16, 17, 18, 19]
    assert [r["text"] for r in shown] == lines[15:]
    assert [r["t"] for r in shown] == ["t15", "t16", "t17", "t18", "t19"]
    assert marker["truncated"] == {"lines": 15, "bytes": 15 * 8}
    assert marker["src"] == "sys" and "15 earlier lines" in marker["summary"]


def test_a_window_that_opens_mid_line_skips_that_line_whole(tmp_path, monkeypatch):
    monkeypatch.setattr(logs, "MAX_LOG_BYTES", 10)
    log = tmp_path / "s.log"
    log.write_text("aaaaaaa\nbbbbbbb\nccc\n")  # 20 bytes; the window opens inside "bbbbbbb"
    rows = list(logs.jsonl(log))
    assert [(r["n"], r["text"]) for r in rows[1:]] == [(2, "ccc")]
    assert rows[0]["truncated"] == {"lines": 2, "bytes": 16}


def test_a_log_under_the_cap_carries_no_marker(tmp_path):
    log = tmp_path / "s.log"
    log.write_text("one\ntwo\n")
    assert not any("truncated" in r for r in logs.jsonl(log))


def test_one_overlong_line_is_capped_in_memory_and_numbering_survives(tmp_path, monkeypatch):
    monkeypatch.setattr(logs, "MAX_LINE_BYTES", 5000)
    log = tmp_path / "s.log"
    log.write_bytes(b"x" * 50_000)
    tail = logs.Tail(log)
    assert tail.read() == []  # still being written
    assert len(tail._pending) <= logs.MAX_LINE_BYTES
    with open(log, "ab") as fh:
        fh.write(b"\nafter\n")
    rows = tail.read()
    assert [r["n"] for r in rows] == [0, 1]
    assert rows[0]["text"] == "x" * logs.MAX_TEXT + "…"
    assert rows[1]["text"] == "after"
    assert len(tail._pending) == 0


def _record_where_reads_run(monkeypatch) -> list[bool]:
    """True per `Tail.read` that ran with an event loop running in its thread."""
    on_loop: list[bool] = []
    real_read = logs.Tail.read

    def read(self, **kw):
        try:
            asyncio.get_running_loop()
            on_loop.append(True)
        except RuntimeError:
            on_loop.append(False)
        return real_read(self, **kw)

    monkeypatch.setattr(logs.Tail, "read", read)
    monkeypatch.setattr(
        sessions, "_session_row", lambda st, sid: {"status": "done", "log_path": None}
    )
    return on_loop


def test_the_sse_tail_reads_off_the_event_loop(tmp_path, monkeypatch):
    """The read runs in a worker thread: no event loop is running where it runs."""
    log = tmp_path / "s.log"
    log.write_text("one\n")
    on_loop = _record_where_reads_run(monkeypatch)

    async def drain():
        return [frame async for frame in sessions._tail(None, "s", log, poll_s=0)]

    frames = asyncio.run(drain())
    assert '"text": "one"' in frames[0] and frames[-1].startswith("event: end")
    assert on_loop and not any(on_loop), "a log read ran on the event loop"


def test_the_backlog_endpoint_reads_off_the_event_loop(tmp_path, monkeypatch):
    log = tmp_path / "s.log"
    log.write_text("one\n")
    on_loop = _record_where_reads_run(monkeypatch)
    monkeypatch.setattr(
        sessions, "_session_row", lambda st, sid: {"status": "done", "log_path": str(log)}
    )
    request = SimpleNamespace(app=SimpleNamespace(state=None))
    body = asyncio.run(sessions.get_log("s", request, format="jsonl"))
    assert [r["text"] for r in body["lines"]] == ["one"]
    assert on_loop and not any(on_loop), "a log read ran on the event loop"


def test_the_backlog_payload_says_where_the_next_line_starts(tmp_path, monkeypatch):
    """Kraft-tbnse: a `-n 0 -f` reader has no printed line to resume after,
    so the payload names the next line number (past any lines the cap hid)."""
    log = tmp_path / "s.log"
    log.write_text("one\ntwo\n")
    monkeypatch.setattr(
        sessions, "_session_row", lambda st, sid: {"status": "running", "log_path": str(log)}
    )
    request = SimpleNamespace(app=SimpleNamespace(state=None))
    body = asyncio.run(sessions.get_log("s", request, format="jsonl"))
    assert body["next_line"] == 2
