"""The decisions log: what the controller decided on the human's behalf, and the
questions it held for them, kept beside the chain and never in the issue store."""

from __future__ import annotations

import datetime as dt
import json
import re
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN))

import kl  # noqa: E402


def ids(entries):
    return [e["id"] for e in entries]


def test_a_decision_is_recorded_with_who_answered(walk, kl_cli, tmp_path):
    chain_id = walk.slice(tmp_path)

    entry = kl_cli(
        tmp_path,
        "decide",
        "--question",
        "Which flag name?",
        "--why",
        "the plan names both",
        "--answer",
        "dry-run",
        "--slice",
        "a",
    )

    assert re.fullmatch(r"kd-[0-9a-f]{8}", entry["id"])
    assert dt.datetime.strptime(entry["at"], kl.BD_TIME)
    assert entry == {
        "id": entry["id"],
        "at": entry["at"],
        "slice": "a",
        "question": "Which flag name?",
        "answer": "dry-run",
        "why": "the plan names both",
        "held": False,
        "answered_by": "controller",
        "answered_at": entry["at"],
        "reported": False,
        "seq": 1,
    }
    assert kl_cli(tmp_path, "decisions") == [entry]
    path = kl._decisions_path(tmp_path, chain_id)
    assert path == tmp_path / ".kraft-lite" / "decisions" / f"{chain_id}.jsonl"
    assert kl.read_jsonl(path) == [entry]


def test_a_held_question_has_no_answer_until_the_user_gives_one(walk, kl_cli, tmp_path):
    walk.slice(tmp_path)
    held = kl_cli(
        tmp_path, "decide", "--question", "Drop the v1 API?", "--why", "costly to undo", "--held"
    )
    assert (held["held"], held["answer"], held["answered_by"], held["answered_at"]) == (
        True,
        None,
        None,
        None,
    )

    answered = kl_cli(tmp_path, "answer", "--decision", held["id"], "--answer", "keep it")

    assert answered == held | {
        "answer": "keep it",
        "held": False,
        "answered_by": "user",
        "answered_at": answered["answered_at"],
        "seq": 2,
    }
    assert answered["answered_at"] >= held["at"]
    assert kl_cli(tmp_path, "decisions") == [answered]

    refused = kl_cli(tmp_path, "answer", "--decision", "kd-00000000", "--answer", "x", ok=False)
    assert "kd-00000000" in refused.stderr and "Traceback" not in refused.stderr
    assert kl_cli(tmp_path, "decisions") == [answered]


@pytest.mark.parametrize(
    "flags, named",
    [
        pytest.param(["--held", "--answer", "yes"], "--held", id="held_with_answer"),
        pytest.param([], "--answer", id="unheld_without_answer"),
        # As `slice-set` does: a decision filed under no real slice is lost to it.
        pytest.param(
            ["--answer", "yes", "--slice", "ghost"], "no slice 'ghost'", id="unknown_slice"
        ),
    ],
)
def test_decide_refuses_what_it_cannot_record(flags, named, walk, kl_cli, tmp_path):
    walk.slice(tmp_path)

    refused = kl_cli(tmp_path, "decide", "--question", "q", "--why", "w", *flags, ok=False)

    assert named in refused.stderr and "Traceback" not in refused.stderr
    assert kl_cli(tmp_path, "decisions") == []


def test_decisions_filters_held_and_unreported_and_mark_moves_the_cursor(walk, kl_cli, tmp_path):
    walk.slice(tmp_path)
    store = tmp_path / ".kraft-lite" / "chain.jsonl"
    written = store.read_text()

    def decide(*extra):
        return kl_cli(tmp_path, "decide", "--question", "q", "--why", "w", *extra)

    first = decide("--answer", "one")
    held = decide("--held")

    assert ids(kl_cli(tmp_path, "decisions")) == [first["id"], held["id"]]
    assert ids(kl_cli(tmp_path, "decisions", "--held")) == [held["id"]]
    assert ids(kl_cli(tmp_path, "decisions", "--unreported")) == [first["id"], held["id"]]
    assert ids(kl_cli(tmp_path, "decisions", "--unreported")) == [first["id"], held["id"]], (
        "--unreported alone moves nothing"
    )

    reported = kl_cli(tmp_path, "decisions", "--unreported", "--mark")
    assert ids(reported) == [first["id"], held["id"]]
    # Marking is a write of each entry, counted as `decide` and `answer` are.
    assert [e["seq"] for e in kl_cli(tmp_path, "decisions")] == [2, 2]
    assert kl_cli(tmp_path, "decisions", "--unreported") == []
    assert ids(kl_cli(tmp_path, "decisions", "--held")) == [held["id"]], "still held, reported"

    third = decide("--answer", "three")
    assert ids(kl_cli(tmp_path, "decisions", "--unreported", "--mark")) == [third["id"]]
    assert kl_cli(tmp_path, "decisions", "--unreported", "--mark") == []
    assert ids(kl_cli(tmp_path, "decisions")) == [first["id"], held["id"], third["id"]]

    # What was reported is kept in the decisions log, so no record in the store is
    # written for it: not the run record, and never the epic.
    assert store.read_text() == written


@pytest.mark.parametrize(
    "where",
    [
        pytest.param("here", id="answer_after_report"),
        # Answered in another checkout of the run and imported: the answer reaches
        # this log after the report that showed the question held.
        pytest.param("import", id="import_behind_cursor"),
    ],
)
def test_an_answer_to_a_reported_question_is_unreported_again(where, walk, kl_cli, tmp_path):
    here, there = tmp_path / "here", tmp_path / "there"
    here.mkdir(), there.mkdir()
    walk.slice(here)
    held = kl_cli(here, "decide", "--question", "q1", "--why", "w", "--held")
    later = kl_cli(here, "decide", "--question", "q2", "--why", "w", "--answer", "b")
    if where == "import":
        kl_cli(here, "export", "--to", str(tmp_path / "old"))
        kl_cli(there, "import", "--from", str(tmp_path / "old"))
    assert ids(kl_cli(here, "decisions", "--unreported", "--mark")) == [held["id"], later["id"]]

    if where == "here":
        kl_cli(here, "answer", "--decision", held["id"], "--answer", "yes")
    else:
        kl_cli(there, "answer", "--decision", held["id"], "--answer", "yes")
        kl_cli(there, "export", "--to", str(tmp_path / "new"))
        kl_cli(here, "import", "--from", str(tmp_path / "new"))

    [entry] = kl_cli(here, "decisions", "--unreported")
    assert (entry["id"], entry["answer"], entry["answered_by"]) == (held["id"], "yes", "user")


@pytest.mark.parametrize(
    "answered_by",
    [
        pytest.param("controller", id="a_controller_decision"),
        pytest.param("user", id="an_earlier_user_answer"),
    ],
)
def test_answer_refuses_a_decision_that_is_not_held(answered_by, walk, kl_cli, tmp_path):
    walk.slice(tmp_path)
    if answered_by == "controller":
        entry = kl_cli(tmp_path, "decide", "--question", "q", "--why", "w", "--answer", "mine")
    else:
        held = kl_cli(tmp_path, "decide", "--question", "q", "--why", "w", "--held")
        entry = kl_cli(tmp_path, "answer", "--decision", held["id"], "--answer", "theirs")

    refused = kl_cli(tmp_path, "answer", "--decision", entry["id"], "--answer", "x", ok=False)

    assert "not held" in refused.stderr and "Traceback" not in refused.stderr
    assert kl_cli(tmp_path, "decisions") == [entry]


@pytest.mark.parametrize(
    "sliced, flags, named",
    [
        pytest.param(True, ["--mark"], "--unreported", id="mark_without_unreported"),
        pytest.param(
            False, ["--unreported", "--mark"], "not sliced", id="mark_on_an_unsliced_chain"
        ),
        # `--held` would cut the window before the cursor is placed, and every
        # unheld entry in it would be marked reported without being reported.
        pytest.param(
            True, ["--held", "--unreported", "--mark"], "without --held", id="mark_with_held"
        ),
    ],
)
def test_mark_needs_unreported_and_a_sliced_run(sliced, flags, named, walk, kl_cli, tmp_path):
    (walk.slice if sliced else walk.start)(tmp_path)
    kl_cli(tmp_path, "decide", "--question", "q", "--why", "w", "--answer", "a")
    kl_cli(tmp_path, "decide", "--question", "q", "--why", "w", "--held")

    refused = kl_cli(tmp_path, "decisions", *flags, ok=False)

    assert named in refused.stderr and "Traceback" not in refused.stderr
    assert len(kl_cli(tmp_path, "decisions", "--unreported")) == 2


def test_decisions_never_reach_the_issue_store(walk_in_process, fake_bd, monkeypatch, tmp_path):
    monkeypatch.setattr(kl, "store_for", lambda root: kl.BdStore(run=fake_bd))
    run = walk_in_process.run
    chain_id = walk_in_process.slice(tmp_path)

    texts = ("QUESTION-ONE", "WHY-ONE", "QUESTION-TWO", "WHY-TWO", "ANSWER-ONE", "ANSWER-TWO")
    held = run(tmp_path, "decide", "--question", texts[0], "--why", texts[1], "--held")
    run(tmp_path, "decide", "--question", texts[2], "--why", texts[3], "--answer", texts[4])
    run(tmp_path, "answer", "--decision", held["id"], "--answer", texts[5])
    marked = run(tmp_path, "decisions", "--unreported", "--mark")
    run(tmp_path, "export", "--to", str(tmp_path / "state"))
    run(tmp_path, "import", "--from", str(tmp_path / "state"))

    assert len(marked) == 2
    assert kl.read_jsonl(kl._decisions_path(tmp_path, chain_id))
    assert any(chain_id in payload for payload in fake_bd.imports), "the chain itself is in bd"
    sent = [json.loads(line) for payload in fake_bd.imports for line in payload.splitlines()]
    # Not a decision, and not which ones were reported: that is kept in the log.
    for record in sent:
        assert "kd-" not in json.dumps(record)
        assert not [t for t in texts if t in json.dumps(record)]


DECISION_VERBS = [
    pytest.param(["decide", "--question", "q", "--why", "w", "--held"], id="decide"),
    pytest.param(["answer", "--decision", "kd-00000000", "--answer", "a"], id="answer"),
    pytest.param(["decisions"], id="decisions"),
    pytest.param(["export", "--to", "state"], id="export"),
]


@pytest.mark.parametrize("verb", DECISION_VERBS)
def test_decision_verbs_refuse_to_guess_between_two_unfinished_chains(verb, walk, kl_cli, tmp_path):
    walk.start(tmp_path)
    walk.start(tmp_path)

    refused = kl_cli(tmp_path, *verb, ok=False)

    assert "more than one unfinished chain" in refused.stderr


def test_decision_verbs_still_answer_after_the_chain_is_done(walk, kl_cli, tmp_path):
    walk.slice(tmp_path)
    held = kl_cli(tmp_path, "decide", "--question", "q", "--why", "w", "--held")
    walk.merge_slices(tmp_path)
    while kl_cli(tmp_path, "state")["status"] != "done":
        kl_cli(tmp_path, "close")

    assert kl_cli(tmp_path, "answer", "--decision", held["id"], "--answer", "a")["held"] is False
    late = kl_cli(tmp_path, "decide", "--question", "q2", "--why", "w", "--answer", "b")
    assert ids(kl_cli(tmp_path, "decisions")) == [held["id"], late["id"]]
    kl_cli(tmp_path, "export", "--to", str(tmp_path / "state"))
    assert (tmp_path / "state" / "decisions.jsonl").is_file()
