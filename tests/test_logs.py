"""Kraft-5x45w: an unrecognised stream-json event kind summarises to its bare
`type`, not the raw JSON line."""

import json

from kraft.logs import summary


def test_known_kind_still_summarises_normally():
    obj = {"type": "result", "is_error": False}
    assert summary(obj, json.dumps(obj)) == "result: success"


def test_unknown_kind_falls_back_to_bare_type_not_raw_json():
    obj = {"type": "stream_event", "event": {"index": 0, "delta": {}}}
    line = json.dumps(obj)
    assert summary(obj, line) == "stream_event"


def test_non_json_line_falls_back_to_the_line_itself():
    assert summary(None, "plain stdout, not JSON") == "plain stdout, not JSON"
