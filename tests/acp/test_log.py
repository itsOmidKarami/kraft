import json

from kraft.acp import log


def test_records_keeps_only_this_formats_lines(tmp_path):
    lines = [
        log.event("starting", agent=["a"]),
        "plain text a CLI printed",
        json.dumps({"type": "result"}),
        log.message("from_agent", {"jsonrpc": "2.0", "id": 1, "result": {}}),
        log.stray("help text"),
    ]
    got = list(log.records(lines))
    assert [(r["dir"], r.get("event")) for r in got] == [
        ("client", "starting"),
        ("from_agent", None),
        ("stray", None),
    ]


def test_a_replayed_message_is_marked():
    rec = next(log.records([log.message("from_agent", {"jsonrpc": "2.0"}, replay=True)]))
    assert rec["replay"] is True


def test_config_error_names_the_first_one(tmp_path):
    path = tmp_path / "log"
    path.write_text(
        log.event("starting") + "\n" + log.event("config_error", message="bad model") + "\n"
    )
    assert log.config_error(path) == "bad model"
    assert log.config_error(tmp_path / "missing") is None
