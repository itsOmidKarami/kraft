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
    ],
)
def test_decide_requires_an_answer_unless_held(flags, named, walk, kl_cli, tmp_path):
    walk.slice(tmp_path)

    refused = kl_cli(tmp_path, "decide", "--question", "q", "--why", "w", *flags, ok=False)

    assert named in refused.stderr and "Traceback" not in refused.stderr
    assert kl_cli(tmp_path, "decisions") == []


def test_decisions_filters_held_and_unreported_and_mark_moves_the_cursor(walk, kl_cli, tmp_path):
    chain_id = walk.slice(tmp_path)
    store = tmp_path / ".kraft-lite" / "chain.jsonl"

    def decide(*extra):
        return kl_cli(tmp_path, "decide", "--question", "q", "--why", "w", *extra)

    def cursor():
        return kl.label_value(kl.run_record(kl.read_jsonl(store), chain_id), kl.REPORTED_LABEL)

    first = decide("--answer", "one")
    held = decide("--held")

    assert ids(kl_cli(tmp_path, "decisions")) == [first["id"], held["id"]]
    assert ids(kl_cli(tmp_path, "decisions", "--held")) == [held["id"]]
    assert ids(kl_cli(tmp_path, "decisions", "--unreported")) == [first["id"], held["id"]]
    assert cursor() is None, "--unreported alone moves nothing"

    reported = kl_cli(tmp_path, "decisions", "--unreported", "--mark")
    assert ids(reported) == [first["id"], held["id"]]
    assert cursor() == held["id"]
    assert kl_cli(tmp_path, "decisions", "--unreported") == []

    third = decide("--answer", "three")
    # Answering keeps the entry's place in write order: it was reported already.
    kl_cli(tmp_path, "answer", "--decision", held["id"], "--answer", "two")
    assert ids(kl_cli(tmp_path, "decisions")) == [first["id"], held["id"], third["id"]]
    assert ids(kl_cli(tmp_path, "decisions", "--unreported", "--mark")) == [third["id"]]
    assert kl_cli(tmp_path, "decisions", "--unreported", "--mark") == []
    assert cursor() == third["id"], "an empty report leaves the cursor where it was"

    # The cursor lives on the run record; the epic is never written after `start`.
    epic_writes = [
        line for line in store.read_text().splitlines() if json.loads(line)["id"] == chain_id
    ]
    assert len(epic_writes) == 1


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
    chain_id = (walk.slice if sliced else walk.start)(tmp_path)
    kl_cli(tmp_path, "decide", "--question", "q", "--why", "w", "--answer", "a")
    kl_cli(tmp_path, "decide", "--question", "q", "--why", "w", "--held")

    refused = kl_cli(tmp_path, "decisions", *flags, ok=False)

    assert named in refused.stderr and "Traceback" not in refused.stderr
    run = kl.run_record(kl.read_jsonl(tmp_path / ".kraft-lite" / "chain.jsonl"), chain_id)
    assert kl.label_value(run, kl.REPORTED_LABEL) is None
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
    # The report cursor is a pointer to a decision, and is allowed; the decision is not.
    pointers = [x for r in sent for x in r["labels"] if x.startswith(kl.REPORTED_LABEL)]
    assert pointers == [f"{kl.REPORTED_LABEL}{marked[-1]['id']}"]
    for record in sent:
        rest = dict(record, labels=[x for x in record["labels"] if x not in pointers])
        assert "kd-" not in json.dumps(rest)
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
    while kl_cli(tmp_path, "state")["status"] != "done":
        kl_cli(tmp_path, "close")

    assert kl_cli(tmp_path, "answer", "--decision", held["id"], "--answer", "a")["held"] is False
    late = kl_cli(tmp_path, "decide", "--question", "q2", "--why", "w", "--answer", "b")
    assert ids(kl_cli(tmp_path, "decisions")) == [held["id"], late["id"]]
    kl_cli(tmp_path, "export", "--to", str(tmp_path / "state"))
    assert (tmp_path / "state" / "decisions.jsonl").is_file()
