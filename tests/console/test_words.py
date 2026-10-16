"""The console's half of `wording_cases.json`: the web's board wording, ported.
`frontend/src/ng/board/wording.test.ts` runs the same cases against the web."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from kraft.console import words

CASES = json.loads((Path(__file__).parent / "wording_cases.json").read_text())
NOW = datetime.fromisoformat(CASES["now"]).timestamp()


@pytest.mark.parametrize("case", CASES["rows"], ids=[case["name"] for case in CASES["rows"]])
def test_a_row_sits_and_reads_as_on_the_web(case):
    assert words.group_of(case["row"]) == case["group"]
    assert words.reason_tail(case["row"], NOW) == case["tail"]


def test_the_board_sorts_as_the_web_does():
    order = CASES["order"]
    assert [row["id"] for row in words.sort_rows(order["rows"])] == order["sorted"]


@pytest.mark.parametrize("scale", ["ago", "until"])
def test_a_time_reads_as_on_the_web(scale):
    said = getattr(words, scale)
    assert [said(iso, NOW) for iso, _ in CASES[scale]] == [want for _, want in CASES[scale]]
