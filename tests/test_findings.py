import json

from kraft.findings import Finding, from_blind_failure, from_payload, parse


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


def _log(tmp_path, text):
    p = tmp_path / "s.log"
    p.write_text(text)
    return p


def test_from_blind_failure_extracts_marker_lines(tmp_path):
    log = _log(
        tmp_path,
        "some setup noise\n"
        "  ✘  27 e2e/regression.spec.ts:65:1 › a flaky test (2.0m)\n"
        "    Error: locator.click: Test timeout of 120000ms exceeded.\n",
    )
    f = from_blind_failure("on.test.run", log, "wid1", "sess1")
    assert f.severity == "critical"
    assert f.source_plugin == "on.test.run"
    assert f.file is None
    assert "e2e/regression.spec.ts:65:1" in f.message
    assert "Test timeout" in f.message


def test_from_blind_failure_strips_duration_and_timestamp_noise(tmp_path):
    """The same failure at two different wall-clock costs must fingerprint
    the same -- that's the entire point of this feature."""
    a = _log(tmp_path, "✘ 1 e2e/x.spec.ts:1:1 › t (2.0m)\n14:03:11 done\n")
    b = _log(tmp_path, "✘ 1 e2e/x.spec.ts:1:1 › t (2.3m)\n14:09:58 done\n")
    # Same session id on purpose: isolating noise-stripping in the extracted
    # content from the (separately tested, and by design volatile) log
    # pointer's own session id.
    fa = from_blind_failure("on.test.run", a, "wid1", "sess1")
    fb = from_blind_failure("on.test.run", b, "wid1", "sess1")
    assert fa.fingerprint == fb.fingerprint


def test_from_blind_failure_falls_back_to_tail_when_no_marker_matches(tmp_path):
    log = _log(tmp_path, "line one\nline two\nline three\n")
    f = from_blind_failure("on.test.run", log, "wid1", "sess1")
    assert "line three" in f.message


def test_from_blind_failure_caps_message_length(tmp_path):
    log = _log(tmp_path, "Error: " + ("x" * 5000) + "\n")
    f = from_blind_failure("on.test.run", log, "wid1", "sess1")
    assert len(f.message) <= 500  # 400 cap + short pointer line


def test_from_blind_failure_missing_log_is_not_an_error(tmp_path):
    f = from_blind_failure("on.test.run", tmp_path / "absent.log", "wid1", "sess1")
    assert "no output captured" in f.message
    assert f.severity == "critical"


def test_from_blind_failure_missing_log_fingerprints_the_same_every_time(tmp_path):
    # Same session id: isolating the fixed "no output captured" fallback
    # string from the (separately tested) log pointer's own session id.
    f1 = from_blind_failure("on.test.run", tmp_path / "absent.log", "wid1", "sess1")
    f2 = from_blind_failure("on.test.run", tmp_path / "still-absent.log", "wid1", "sess1")
    assert f1.fingerprint == f2.fingerprint


def test_from_blind_failure_prefers_a_reproduce_command_over_a_log_pointer(tmp_path):
    """`reproduce` (Task 2 passes it for kind: subprocess hooks) must not
    embed anything round-specific -- it's what keeps a subprocess hook's
    fingerprint stable, which the plain log-pointer fallback cannot."""
    log = _log(tmp_path, "✘ 1 e2e/x.spec.ts:1:1 › t (2.0m)\n")
    f = from_blind_failure("on.test.run", log, "wid1", "sess1", reproduce="uv run pytest -q")
    assert "Reproduce with: uv run pytest -q" in f.message
    assert "sess1" not in f.message


def test_from_blind_failure_without_reproduce_points_at_the_session_log(tmp_path):
    log = _log(tmp_path, "✘ 1 e2e/x.spec.ts:1:1 › t\n")
    f = from_blind_failure("on.test.run", log, "wid1", "sess1")
    assert "kraft view logs wid1 --session sess1" in f.message
