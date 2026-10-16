"""The console's half of `wording_cases.json`: the web's wording, ported.
`frontend/src/ng/board/wording.test.ts` and `frontend/src/ng/item/wording.test.ts`
run the same cases against the web."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from kraft.console import words

CASES = json.loads((Path(__file__).parent / "wording_cases.json").read_text())
NOW = datetime.fromisoformat(CASES["now"]).timestamp()
NODES = CASES["chain"]["nodes"]


@pytest.mark.parametrize("case", CASES["rows"], ids=[case["name"] for case in CASES["rows"]])
def test_a_row_sits_and_reads_as_on_the_web(case):
    row = {**case["row"], "chain_definition": CASES["chain"]}
    assert words.group_of(row) == case["group"]
    assert words.reason_tail(row, NOW) == case["tail"]
    if "marks" in case:
        assert words.chain_marks(row) == [
            {"node": node["id"], "gate": node["kind"] == "gate", "state": state}
            for node, state in zip(NODES, case["marks"], strict=True)
        ]


def test_the_board_sorts_as_the_web_does():
    order = CASES["order"]
    assert [row["id"] for row in words.sort_rows(order["rows"])] == order["sorted"]


@pytest.mark.parametrize("scale", ["ago", "until"])
def test_a_time_reads_as_on_the_web(scale):
    said = getattr(words, scale)
    assert [said(iso, NOW) for iso, _ in CASES[scale]] == [want for _, want in CASES[scale]]


@pytest.mark.parametrize("case", CASES["banners"], ids=[case["name"] for case in CASES["banners"]])
def test_a_stop_banner_reads_as_on_the_web(case):
    assert words.banner_of(case["item"]) == case["banner"]


@pytest.mark.parametrize("case", CASES["events"], ids=[case["name"] for case in CASES["events"]])
def test_an_event_reads_as_on_the_web(case):
    assert words.event_line(case["event"]) == case["line"]


def test_an_events_age_reads_as_on_the_web():
    ages = CASES["event_ages"]
    assert [words.event_age(iso, NOW) for iso, _ in ages] == [want for _, want in ages]
