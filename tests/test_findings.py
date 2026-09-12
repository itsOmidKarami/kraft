import json

from kraft.findings import Finding, from_payload, parse


def _write(tmp_path, data):
    p = tmp_path / "r.json"
    p.write_text(json.dumps(data))
    return p


def test_parse_reads_a_finding(tmp_path):
    p = _write(
        tmp_path,
        {
            "status": "failed",
            "findings": [
                {
                    "severity": "important",
                    "message": "swallowed exception",
                    "file": "a.py",
                    "line": 12,
                    "source_plugin": "ponytail",
                },
            ],
        },
    )
    (f,) = parse(p)
    assert (f.severity, f.file, f.line, f.source_plugin) == ("important", "a.py", 12, "ponytail")


def test_parse_missing_key_yields_nothing(tmp_path):
    assert parse(_write(tmp_path, {"status": "done"})) == []


def test_parse_non_list_findings_yields_nothing(tmp_path):
    assert parse(_write(tmp_path, {"findings": "nope"})) == []


def test_parse_missing_file_is_not_an_error(tmp_path):
    assert parse(tmp_path / "absent.json") == []


def test_parse_broken_json_is_not_an_error(tmp_path):
    p = tmp_path / "r.json"
    p.write_text("{not json")
    assert parse(p) == []


def test_parse_drops_bad_entries_individually(tmp_path):
    p = _write(
        tmp_path,
        {
            "findings": [
                {"severity": "nonsense", "message": "m", "source_plugin": "p"},
                {"severity": "minor", "source_plugin": "p"},
                {"severity": "minor", "message": "m"},
                "not a mapping",
                {"severity": "critical", "message": "keep me", "source_plugin": "p"},
            ]
        },
    )
    (f,) = parse(p)
    assert f.message == "keep me"


def test_optional_file_and_line(tmp_path):
    p = _write(
        tmp_path,
        {
            "findings": [
                {"severity": "minor", "message": "m", "source_plugin": "p"},
            ]
        },
    )
    (f,) = parse(p)
    assert f.file is None and f.line is None


def test_fingerprint_ignores_line(tmp_path):
    a = Finding("important", "same message", "a.py", 10, "p")
    b = Finding("important", "same message", "a.py", 400, "p")
    assert a.fingerprint == b.fingerprint


def test_fingerprint_ignores_severity(tmp_path):
    """The same defect re-reported at a different severity is the same defect."""
    a = Finding("important", "m", "a.py", 1, "p")
    b = Finding("critical", "m", "a.py", 1, "p")
    assert a.fingerprint == b.fingerprint


def test_fingerprint_normalizes_whitespace_and_case(tmp_path):
    a = Finding("important", "Swallowed   exception", "a.py", 1, "p")
    b = Finding("important", "swallowed exception", "a.py", 1, "p")
    assert a.fingerprint == b.fingerprint


def test_fingerprint_separates_file_plugin_and_message(tmp_path):
    base = Finding("important", "m", "a.py", 1, "p")
    assert base.fingerprint != Finding("important", "m", "b.py", 1, "p").fingerprint
    assert base.fingerprint != Finding("important", "other", "a.py", 1, "p").fingerprint
    assert base.fingerprint != Finding("important", "m", "a.py", 1, "q").fingerprint


def test_fingerprint_differs_when_the_traced_error_changes_and_matches_when_it_does_not():
    """A code-red `on.ci.poll` finding's message carries the failing job's own
    trace tail (Kraft-cbr §3): two cycles of the same unresolved failure must
    fingerprint identically (walk_node's no-progress stop reads that as
    "stuck"), while a fix that changed *what* broke must fingerprint
    differently (that reads as progress, however the job itself still
    failed)."""
    before = Finding(
        "important",
        "job test: failed\n  AssertionError: expected 3, got 4",
        None,
        None,
        "on.ci.poll",
    )
    after_same_error = Finding(
        "important",
        "job test: failed\n  AssertionError: expected 3, got 4",
        None,
        None,
        "on.ci.poll",
    )
    after_different_error = Finding(
        "important",
        "job test: failed\n  AssertionError: expected 3, got 5",
        None,
        None,
        "on.ci.poll",
    )
    assert before.fingerprint == after_same_error.fingerprint  # nothing moved
    assert before.fingerprint != after_different_error.fingerprint  # the fix changed the error


def test_parse_non_utf8_bytes_is_not_an_error(tmp_path):
    p = tmp_path / "r.json"
    p.write_bytes(b"\xff\xfe\x00binary")
    assert parse(p) == []


def test_parse_top_level_list_yields_nothing(tmp_path):
    p = tmp_path / "r.json"
    p.write_text(json.dumps([1, 2, 3]))
    assert parse(p) == []


def test_from_payload_normal_round_trip(tmp_path):
    payload = {
        "severity": "critical",
        "message": "error found",
        "file": "test.py",
        "line": 42,
        "source_plugin": "checker",
    }
    f = from_payload(payload)
    assert (
        f.severity,
        f.message,
        f.file,
        f.line,
        f.source_plugin,
    ) == ("critical", "error found", "test.py", 42, "checker")


def test_from_payload_extra_keys_does_not_raise(tmp_path):
    payload = {
        "severity": "minor",
        "message": "warning",
        "source_plugin": "linter",
        "extra_key": "unexpected",
        "another_field": 123,
    }
    f = from_payload(payload)
    assert f.severity == "minor"
    assert f.message == "warning"
    assert f.source_plugin == "linter"


def test_parse_line_bool_yields_none(tmp_path):
    p = _write(
        tmp_path,
        {
            "findings": [
                {"severity": "minor", "message": "m", "source_plugin": "p", "line": True},
            ]
        },
    )
    (f,) = parse(p)
    assert f.line is None
