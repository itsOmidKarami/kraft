"""render.py is pure formatting: no app, no HTTP, no fixtures."""

from __future__ import annotations

import io
from datetime import UTC, datetime

import pytest

from kraft import render
from kraft.vocab import WorkItemStatus


@pytest.mark.parametrize("status", list(WorkItemStatus))
def test_every_status_has_a_colour_entry(status):
    assert status in render.STATUS_COLORS


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


@pytest.mark.parametrize(
    ("title", "shown"),
    [
        ("two\nlines", "two"),
        ("\nsecond", "second"),
        ("cr\r\nlf", "cr"),
        ("\n", "-"),
        ("bidi\u202eflip\x07", "bidiflip"),
        ("c1\x9b31mred", "c131mred"),
        ("iso\u2066late\u2069", "isolate"),
        ("nel\x85next", "nel"),
        ("mark\u200fkept", "mark\u200fkept"),
    ],
    ids=[
        "two-lines",
        "leading-break",
        "crlf",
        "only-a-break",
        "controls",
        "c1",
        "isolate",
        "nel",
        "rlm",
    ],
)
def test_table_shows_a_cells_first_line_only(title, shown):
    """A title 1.4 stored with a line break wrapped `kraft view list`'s row
    under its ID column, and a control character in it reached the terminal
    (R11F-03)."""
    rows = [{"title": title, "id": "Kraft-a"}]
    out = render.table(rows, [("TITLE", "title"), ("ID", "id")], width=80).splitlines()
    assert len(out) == 2
    assert out[1].split() == [shown, "Kraft-a"]


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
        "invalid_intake": "intake.yaml: 'interval_s': not a number",
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
    assert "invalid intake" in out and "'interval_s': not a number" in out
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


@pytest.mark.parametrize(
    ("installed", "pending"), [("2.0.0rc1", False), ("2.0.0rc2", True)], ids=["same", "newer"]
)
def test_health_block_names_the_running_version_and_a_pending_restart(installed, pending):
    """`kraft admin update` without `--restart` leaves the old server up. Only
    `--json` said so; the operations page verifies a restart with this."""
    payload = {"status": "ok", "version": "2.0.0rc1", "installed": installed, "index": {}}
    out = render.health_block(payload)
    assert "2.0.0rc1" in out
    assert ("restart pending" in out) is pending
    assert ("2.0.0rc2" in out) is pending


def test_health_block_says_an_installed_model_is_failing():
    """`[vector]` installed but the model will not download: `available` alone
    printed "available" over a search that was answering 500."""
    payload = {
        "status": "ok",
        "bind": "127.0.0.1",
        "index": {
            "documents": 1,
            "repos_scanned": 1,
            "last_scan_at": None,
            "embeddings": {"available": True, "model": "m", "chunks": 0, "reason": "403 Forbidden"},
            "errors": [],
        },
    }
    out = render.health_block(payload)
    assert "failing (403 Forbidden)" in out


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


_COMMITTED = {
    **_DIFF,
    "files": [],
    "diff": "",
    "untracked": [],
    "landed": {
        "commits": ["add the fix", "add the tests"],
        "files": [{"path": "calc.py", "insertions": 1, "deletions": 1}],
        "diff": (
            "diff --git a/calc.py b/calc.py\n--- a/calc.py\n+++ b/calc.py\n"
            "@@ -1 +1 @@\n-a - b\n+a + b\n"
        ),
    },
}


def test_diff_names_the_committed_block_as_the_change_under_review(monkeypatch):
    """Kraft commits after every task, so at a gate the reviewed change is the
    committed block. It used to be filed under "landed", while the empty
    working tree wore the "change under review" title."""
    monkeypatch.setenv("NO_COLOR", "1")
    for renderer in (render.diff_stat, render.diff_body):
        lines = renderer(_COMMITTED).splitlines()
        review = next(i for i, ln in enumerate(lines) if "the change under review" in ln)
        uncommitted = next(i for i, ln in enumerate(lines) if ln.startswith("uncommitted"))
        assert "2 commits" in lines[review]
        assert any("calc.py" in ln for ln in lines[review:uncommitted])
        assert "(nothing uncommitted)" in lines[uncommitted:]
        assert "landed" not in "\n".join(lines) and "in flight" not in "\n".join(lines)


def test_diff_without_commits_keeps_a_single_unlabelled_block(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    out = render.diff_body({**_DIFF, "landed": {"commits": [], "files": [], "diff": ""}})
    assert "uncommitted" not in out and "the change under review" not in out


@pytest.mark.parametrize(
    ("n", "text"),
    [(0, "0K"), (512 * 1024, "512K"), (10 * 1024**3, "10G"), (int(12.1 * 1024**3), "12.1G")],
    ids=["zero", "K", "whole-G", "fraction-G"],
)
def test_human_size(n, text):
    assert render.human_size(n) == text


def test_health_block_shows_storage():
    block = render.health_block(
        {
            "status": "degraded",
            "storage": {
                "state": "held",
                "used_bytes": 12 * 1024**3,
                "quota_bytes": 8 * 1024**3,
                "limit_bytes": 10 * 1024**3,
            },
        }
    )
    assert "12G of 10G" in block and "held" in block
    assert "kraft view storage" in block


def test_storage_block_shows_usage_categories_and_each_worktree():
    payload = {
        "measured_at": None,
        "state": "held",
        "used_bytes": 12 * 1024**3,
        "quota_bytes": 8 * 1024**3,
        "limit_bytes": 10 * 1024**3,
        "reclaimable_bytes": 2 * 1024**3,
        "categories": {"worktrees": 11 * 1024**3, "logs": 512 * 1024},
        "items": [
            {
                "id": "aaa",
                "title": "Old work",
                "status": "completed",
                "archived": False,
                "bytes": 2 * 1024**3,
                "updated_at": None,
                "reclaimable": True,
            },
            {
                "id": "bbb",
                "title": "Live work",
                "status": "completed",
                "archived": True,
                "bytes": 1024**3,
                "updated_at": None,
                "reclaimable": False,
            },
        ],
        "orphans": [{"name": "ccc", "bytes": 1024**2}],
    }

    rows = [line.split() for line in render.strip_ansi(render.storage_block(payload)).splitlines()]

    assert ["worktrees", "12G", "of", "10G", "(held)"] in rows
    assert ["quota", "8G"] in rows
    assert ["reclaimable", "2G"] in rows
    assert ["worktrees", "11G"] in rows and ["logs", "512K"] in rows
    assert ["aaa", "completed", "2G", "-", "yes", "Old", "work"] in rows
    assert ["bbb", "archived", "1G", "-", "-", "Live", "work"] in rows
    assert ["ccc", "orphan", "1M", "-", "-", "no", "work", "item"] in rows
    assert rows.index(["aaa", "completed", "2G", "-", "yes", "Old", "work"]) < rows.index(
        ["bbb", "archived", "1G", "-", "-", "Live", "work"]
    )


def test_storage_block_without_a_limit_says_so():
    payload = {
        "measured_at": None,
        "state": None,
        "used_bytes": 1024**3,
        "quota_bytes": None,
        "limit_bytes": None,
        "reclaimable_bytes": 0,
        "categories": {"worktrees": 1024**3},
        "items": [],
        "orphans": [],
    }

    block = render.strip_ansi(render.storage_block(payload))

    rows = [line.split() for line in block.splitlines()]
    assert ["worktrees", "1G", "(no", "limit", "set)"] in rows
    assert "quota" not in block


def test_storage_preview_says_what_each_archive_loses_and_the_total():
    payload = {
        "freed_bytes": 1024**3,
        "used_after_bytes": 11 * 1024**3,
        "state_after": "over_quota",
        "items": [
            {
                "id": "aaa",
                "title": "Fix",
                "bytes": 1024**3,
                "archivable": True,
                "refusal": None,
                "uncommitted_files": 3,
                "unpushed_commits": 2,
                "branch_kept": True,
            },
            {
                "id": "bbb",
                "title": "Live",
                "bytes": 2 * 1024**3,
                "archivable": False,
                "refusal": "already archived",
                "uncommitted_files": None,
                "unpushed_commits": 0,
                "branch_kept": False,
            },
        ],
    }

    lines = render.strip_ansi(render.storage_preview(payload)).splitlines()
    rows = [line.split() for line in lines]

    assert ["ID", "SIZE", "UNCOMMITTED", "BRANCH", "TITLE"] in rows
    assert ["aaa", "1G", "3", "stays", "Fix"] in rows
    assert ["bbb", "2G", "-", "-", "Live", "(not", "archived:", "already", "archived)"] in rows
    assert lines[-1] == "archiving frees 1G; worktrees would use 11G (over quota)"


def test_archive_results_has_one_line_per_id():
    payload = {
        "results": [
            {"id": "w1", "ok": True, "status": "completed"},
            {"id": "w2", "ok": False, "error": "not found"},
        ]
    }
    assert render.archive_results(payload) == "archived w1\nnot archived w2: not found"
