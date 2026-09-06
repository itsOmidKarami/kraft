"""Column widths are measured on what the terminal draws, not what it stores.

These tests force colour on: pytest has no tty, so `use_color()` is false and
every other test in the suite exercises the unpainted path only — which is
exactly why the shear in `table` shipped (spec F §2.1).
"""

from __future__ import annotations

import pytest

from kraft import render

_COLUMNS = [("STATUS", "status"), ("ID", "id"), ("TITLE", "title")]
_ROWS = [
    {"status": "active", "id": "wi-1", "title": "first"},
    {"status": "paused", "id": "wi-2", "title": "second"},
]


@pytest.fixture
def painting(monkeypatch):
    monkeypatch.setattr(render, "use_color", lambda stream=None: True)


def _paint_status(rows):
    return [
        {**row, "status": render.paint(row["status"], render.STATUS_COLORS[row["status"]])}
        for row in rows
    ]


def test_painted_and_unpainted_tables_are_the_same_shape(painting, monkeypatch):
    monkeypatch.setenv("COLUMNS", "120")
    painted = render.table(_paint_status(_ROWS), _COLUMNS)
    # the header line is painted in both, so strip before comparing
    plain = render.strip_ansi(render.table(_ROWS, _COLUMNS))
    assert render.strip_ansi(painted) == plain


def test_a_painted_cell_does_not_push_the_columns_after_it(painting, monkeypatch):
    monkeypatch.setenv("COLUMNS", "120")
    # only the first row is painted: an unpainted row would have to line up with
    # it, which is the failure a human sees as a sheared table
    mixed = [_paint_status(_ROWS)[0], _ROWS[1]]
    lines = render.strip_ansi(render.table(mixed, _COLUMNS)).splitlines()
    assert lines[1].index("wi-1") == lines[2].index("wi-2")


def test_the_last_column_truncates_to_a_visible_width(painting):
    row = [{"a": "x", "b": render.paint("abcdefghij", "\033[32m")}]
    out = render.table(row, [("A", "a"), ("B", "b")], width=8)
    body = out.splitlines()[1]
    # cut, and cut clean: a sliced escape sequence corrupts the rest of the line
    assert "\033" not in body
    assert body.endswith("…")


def test_a_short_painted_last_column_keeps_its_colour(painting):
    row = [{"a": "x", "b": render.paint("ok", "\033[32m")}]
    body = render.table(row, [("A", "a"), ("B", "b")], width=40).splitlines()[1]
    assert "\033[32m" in body
