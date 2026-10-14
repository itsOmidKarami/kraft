"""Kraft-5x45w: an unrecognised stream-json event kind summarises to its bare
`type`, not the raw JSON line."""

import json

from kraft import logs
from kraft.logs import classify, summary


def test_known_kind_still_summarises_normally():
    obj = {"type": "result", "is_error": False}
    assert summary(obj, json.dumps(obj)) == "result: success"


def test_unknown_kind_falls_back_to_bare_type_not_raw_json():
    obj = {"type": "stream_event", "event": {"index": 0, "delta": {}}}
    line = json.dumps(obj)
    assert summary(obj, line) == "stream_event"


def test_non_json_line_falls_back_to_the_line_itself():
    assert summary(None, "plain stdout, not JSON") == "plain stdout, not JSON"


def test_a_line_nested_past_the_parser_reads_as_plain_stdout(monkeypatch):
    """The worker can print to the log: a line deeper than `json.loads`
    recurses must not break the log view. Raised directly, since how deep the
    parser goes before giving up differs between Python versions."""

    def too_deep(_text):
        raise RecursionError

    monkeypatch.setattr(logs.json, "loads", too_deep)
    assert classify('{"type": "assistant"}') == ("stdout", None)
