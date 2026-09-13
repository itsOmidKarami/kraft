"""render.py is pure formatting: no app, no HTTP, no fixtures."""

from __future__ import annotations

import io
from datetime import UTC, datetime

from kraft import render


def test_table_aligns_columns_and_prints_a_header():
    rows = [
        {"id": "Kraft-a", "title": "short"},
        {"id": "Kraft-bbbb", "title": "longer title"},
    ]
    out = render.table(rows, [("ID", "id"), ("TITLE", "title")], width=80).splitlines()
    assert out[0].split() == ["ID", "TITLE"]
    # every row starts its second column at the same offset
    assert out[1].index("short") == out[2].index("longer title")


def test_table_truncates_the_last_column_rather_than_wrapping():
    rows = [{"id": "Kraft-a", "title": "x" * 200}]
    out = render.table(rows, [("ID", "id"), ("TITLE", "title")], width=40)
    assert len(out.splitlines()) == 2  # header + one row, never wrapped
    assert out.splitlines()[1].endswith("…")
    assert len(out.splitlines()[1]) <= 40


def test_table_says_so_when_there_is_nothing():
    assert render.table([], [("ID", "id")], width=80) == "(nothing)"


def test_table_renders_a_missing_key_as_a_dash():
    out = render.table([{"id": "Kraft-a"}], [("ID", "id"), ("GATE", "pending_gate")], width=80)
    assert out.splitlines()[1].split() == ["Kraft-a", "-"]


def test_relative_time_is_compact():
    now = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)
    assert render.relative_time("2026-09-06T11:56:00+00:00", now) == "4m ago"
    assert render.relative_time("2026-09-06T11:59:30+00:00", now) == "just now"
    assert render.relative_time("2026-09-04T12:00:00+00:00", now) == "2d ago"
    # unparseable or absent is not a crash: this renders a board, not a report
    assert render.relative_time(None, now) == "-"
    assert render.relative_time("not a date", now) == "-"


def test_color_is_off_when_the_stream_is_not_a_tty():
    assert render.use_color(io.StringIO()) is False


def test_color_is_off_when_no_color_is_set(monkeypatch):
    class Tty(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.delenv("NO_COLOR", raising=False)
    assert render.use_color(Tty()) is True
    monkeypatch.setenv("NO_COLOR", "1")
    assert render.use_color(Tty()) is False


def test_kv_aligns_labels():
    out = render.kv([("id", "Kraft-a"), ("status", "active")]).splitlines()
    assert out[0].index("Kraft-a") == out[1].index("active")


def test_kv_indents_the_continuation_of_a_multiline_value():
    """A description is prose and may contain newlines. Without this, every line
    after the first starts at column zero and the block stops being a block."""
    out = render.kv([("id", "Kraft-a"), ("description", "line one\nline two")]).splitlines()
    assert out[1].index("line one") == out[2].index("line two")
    assert out[2].startswith(" ")


def test_log_line_shows_source_and_text():
    out = render.log_line({"n": 3, "t": "2026-09-06T12:00:00+00:00", "src": "stdout", "text": "hi"})
    assert "hi" in out
    assert "stdout" in out


def test_log_line_survives_a_line_with_no_timestamp():
    out = render.log_line({"n": 0, "t": None, "src": "stderr", "text": "boom"})
    assert "boom" in out


def test_log_line_prefers_the_summary_over_the_raw_line():
    """`kraft view logs` prints one readable line per entry; --json still emits
    the row untouched (cli._print_log)."""
    out = render.log_line(
        {
            "n": 1,
            "t": "2026-09-06T12:00:00+00:00",
            "src": "tool",
            "text": '{"type":"assistant","message":{"content":[{"type":"tool_use"}]}}',
            "summary": "Read(src/kraft/api.py)",
        }
    )
    assert "Read(src/kraft/api.py)" in out
    assert "tool_use" not in out


_DIFF = {
    "work_item_id": "Kraft-x",
    "base_ref": "abc123",
    "files": [
        {"path": "a.py", "insertions": 3, "deletions": 1},
        {"path": "b.py", "insertions": 0, "deletions": 7},
    ],
    "diff": "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1,3 @@\n-old\n+new\n+more\n",
    "untracked": ["notes.md"],
    "truncated": False,
}


def test_diff_stat_lists_files_and_totals():
    out = render.diff_stat(_DIFF)
    assert "a.py" in out and "b.py" in out
    assert "+3" in out and "-7" in out
    assert "2 files" in out


def test_diff_stat_and_body_both_list_untracked_files():
    for renderer in (render.diff_stat, render.diff_body):
        out = renderer(_DIFF)
        assert "untracked" in out.lower()
        assert "notes.md" in out


def test_truncation_is_never_silent():
    """The single most important assertion in this sub-project."""
    cut = {**_DIFF, "truncated": True}
    for renderer in (render.diff_stat, render.diff_body):
        out = renderer(cut)
        assert "truncated" in out.lower()
        assert out.rstrip().splitlines()[-1].lower().startswith("warning")


def test_no_baseline_is_not_an_empty_diff():
    none = {**_DIFF, "base_ref": None, "files": [], "diff": "", "untracked": []}
    assert "no baseline" in render.diff_body(none)
    assert "no baseline" in render.diff_stat(none)


def test_diff_body_colours_only_on_a_tty(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    assert "\033[" not in render.diff_body(_DIFF)


def test_page_writes_plainly_when_not_a_tty(capsys):
    render.page("hello")
    assert capsys.readouterr().out == "hello\n"


def test_health_block_names_each_degraded_reason():
    payload = {
        "status": "degraded",
        "bind": "127.0.0.1",
        "invalid_templates": {"broken.yaml": "no nodes"},
        "invalid_policy": "unknown key: foo",
        "reattach_summary": {
            "scanned": 2,
            "adopted": ["s1"],
            "resolved_from_file": [],
            "unknown": [],
            "resumed_work_items": [],
        },
        "index": {
            "documents": 12,
            "repos_scanned": 1,
            "last_scan_at": None,
            "embeddings": {
                "available": False,
                "model": "m",
                "chunks": 0,
                "reason": "fastembed not installed",
            },
            "errors": [],
        },
    }
    out = render.health_block(payload)
    assert "degraded" in out
    assert "broken.yaml" in out and "no nodes" in out
    assert "unknown key: foo" in out
    assert "12" in out  # documents
    assert "fastembed not installed" in out
    assert "1 adopted" in out and "2 scanned" in out


def test_health_block_ok_is_short():
    payload = {
        "status": "ok",
        "bind": "127.0.0.1",
        "invalid_templates": {},
        "invalid_policy": None,
        "reattach_summary": {
            "scanned": 0,
            "adopted": [],
            "resolved_from_file": [],
            "unknown": [],
            "resumed_work_items": [],
        },
        "index": {
            "documents": 0,
            "repos_scanned": 0,
            "last_scan_at": None,
            "embeddings": {"available": True, "model": "m", "chunks": 0, "reason": None},
            "errors": [],
        },
    }
    out = render.health_block(payload)
    assert "ok" in out
    assert "invalid" not in out.lower()


def test_doctor_block_marks_warn_separately_from_skip_and_ok():
    rows = [
        {"name": "spa bundle", "ok": True, "detail": "fine", "skipped": False, "warn": False},
        {"name": "hooks", "ok": True, "detail": "not zsh", "skipped": True, "warn": False},
        {"name": "version", "ok": True, "detail": "no feed", "skipped": False, "warn": True},
    ]
    out = render.doctor_block(rows)
    assert "warn" in out
    assert "skip" in out
    assert "3 warned" not in out  # only the version row warned
    assert "all 3 checks passed, 1 warned" in out


def test_doctor_block_a_failure_still_wins_the_summary_over_a_warn():
    rows = [
        {"name": "version", "ok": True, "detail": "no feed", "skipped": False, "warn": True},
        {"name": "spa bundle", "ok": False, "detail": "missing", "skipped": False, "warn": False},
    ]
    assert "1 of 2 checks failed" in render.doctor_block(rows)
