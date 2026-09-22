import dataclasses
import json
import uuid

import pytest

from kraft.findings import (
    BlindJob,
    Finding,
    JobRef,
    _extract_message,
    from_blind_failure,
    from_payload,
    parse,
    resolve_identity,
)


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


@pytest.mark.parametrize(
    "raw",
    [
        b'{"status": "done"}',
        b'{"findings": "nope"}',
        None,
        b"{not json",
        b"\xff\xfe\x00binary",
        b"[1, 2, 3]",
    ],
    ids=[
        "missing-key",
        "non-list-findings",
        "missing-file",
        "broken-json",
        "non-utf8",
        "top-level-list",
    ],
)
def test_parse_an_unusable_result_file_yields_nothing(tmp_path, raw):
    """No findings, never an error: a result file with nothing usable in it."""
    p = tmp_path / "r.json"
    if raw is not None:
        p.write_bytes(raw)
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


@pytest.mark.parametrize(
    ("a", "b"),
    [
        (("important", "same message", "a.py", 10), ("important", "same message", "a.py", 400)),
        # the same defect re-reported at a different severity is the same defect
        (("important", "m", "a.py", 1), ("critical", "m", "a.py", 1)),
        (
            ("important", "Swallowed   exception", "a.py", 1),
            ("important", "swallowed exception", "a.py", 1),
        ),
    ],
    ids=["ignores-line", "ignores-severity", "normalizes-whitespace-and-case"],
)
def test_fingerprint_is_the_same_for_the_same_defect(a, b):
    assert Finding(*a, "p").fingerprint == Finding(*b, "p").fingerprint


def test_jobs_field_never_affects_fingerprint(tmp_path):
    a = Finding(
        severity="critical",
        message="m",
        file=None,
        line=None,
        source_plugin="on.test.run",
        jobs=(JobRef(label="just e2e-ci", log_ref="kraft view logs w1 --session s1"),),
    )
    b = Finding(
        severity="critical",
        message="m",
        file=None,
        line=None,
        source_plugin="on.test.run",
        jobs=(JobRef(label="just e2e-ci", log_ref="kraft view logs w1 --session s2"),),
    )
    assert a.fingerprint == b.fingerprint


def test_same_as_overrides_the_prose_hash():
    """The same defect reworded is the same defect. The hash cannot see that
    -- `message` is LLM prose -- but the reviewer that read both can, and this
    is how it says so (Kraft-s7c04.2). On 49c0cefd one reattach.py defect was
    reported in FIVE consecutive measurements under five distinct
    fingerprints, so `stuck_fingerprint`'s streak never exceeded 1."""
    first = Finding("important", "Swallows the OSError", "a.py", 10, "p")
    reworded = Finding(
        "important",
        "the OSError is caught and dropped",
        "a.py",
        41,
        "p",
        same_as=first.fingerprint,
    )
    assert reworded.fingerprint == first.fingerprint


def test_a_new_defect_in_the_same_file_still_gets_its_own_identity():
    a = Finding("important", "Swallows the OSError", "a.py", 10, "p")
    b = Finding("important", "off-by-one in the retry cap", "a.py", 10, "p")
    assert a.fingerprint != b.fingerprint


def test_same_as_is_dropped_unless_it_looks_like_a_fingerprint(tmp_path):
    """A model asked for a tag will sometimes write a sentence."""
    p = _write(
        tmp_path,
        {
            "findings": [
                {
                    "severity": "minor",
                    "message": "m",
                    "source_plugin": "p",
                    "same_as": "the one about the OSError",
                }
            ]
        },
    )
    (f,) = parse(p)
    assert f.same_as is None


def test_same_as_survives_the_result_file(tmp_path):
    tag = "0123456789abcdef"
    p = _write(
        tmp_path,
        {"findings": [{"severity": "minor", "message": "m", "source_plugin": "p", "same_as": tag}]},
    )
    (f,) = parse(p)
    assert f.fingerprint == tag


def test_same_as_round_trips_through_the_event_payload():
    """walk.py writes `asdict(f)` into findings_measured and judge_history reads
    it back with from_payload; identity has to survive that or every cross-round
    comparison reads the wrong thing."""
    f = Finding("important", "m", "a.py", 1, "p", same_as="0123456789abcdef")
    assert from_payload(dataclasses.asdict(f)).fingerprint == "0123456789abcdef"


def test_resolve_identity_strips_a_tag_that_was_never_shown():
    """A hallucinated or stale tag must not mint an identity: it would collapse
    two distinct defects onto one, or resurrect one from five rounds back, and
    the stuck detector would then fire on a fiction (Kraft-s7c04.2)."""
    shown = Finding("important", "real one", "a.py", 1, "p")
    invented = Finding("minor", "m", "b.py", 2, "p", same_as="ffffffffffffffff")
    (out,) = resolve_identity([invented], known={shown.fingerprint})
    assert out.same_as is None
    assert out.fingerprint == Finding("minor", "m", "b.py", 2, "p").fingerprint


def test_resolve_identity_keeps_a_tag_that_was_shown():
    shown = Finding("important", "real one", "a.py", 1, "p")
    repeat = Finding("important", "reworded", "a.py", 9, "p", same_as=shown.fingerprint)
    (out,) = resolve_identity([repeat], known={shown.fingerprint})
    assert out.fingerprint == shown.fingerprint


def test_resolve_identity_leaves_an_untagged_finding_alone():
    plain = Finding("minor", "m", "a.py", 1, "p")
    assert resolve_identity([plain], known=set()) == [plain]


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
    # A fixed filename here would let a second call in the same test silently
    # overwrite the first's file -- every subsequent read of "the first log"
    # would actually see the second's content (caught by
    # test_from_blind_failure_different_content_is_a_different_fingerprint,
    # which needs two calls in one test to genuinely stay distinct).
    p = tmp_path / f"s{uuid.uuid4().hex}.log"
    p.write_text(text)
    return p


def test_ordinal_after_any_marker_is_stripped_not_just_after_x():
    """The evidence was Playwright's ✘, but `_extract_message` already
    treats ✘|FAILED|Error:|Traceback as the same kind of marker -- the fix
    must not only cover the one tool that happened to produce the evidence."""
    assert "3" not in _extract_message("FAILED 3 tests::test_thing - AssertionError\n")
    assert "AssertionError" in _extract_message("FAILED 3 tests::test_thing - AssertionError\n")


def test_ordinal_at_end_of_line_is_still_stripped():
    """Regression guard for the lookahead itself: `_extract_message` already
    strips trailing whitespace off every line before this regex runs, so an
    ordinal with nothing after it has no trailing `\\s` for a bare
    `(?=\\s)` to match -- needs `(?=\\s|$)`. (The marker character itself
    is consumed by the same match as the ordinal, since the pattern starts
    at the marker -- asserting on "27" rather than exact equality avoids
    coupling this test to that incidental detail.)"""
    assert "27" not in _extract_message("✘ 27\n")


def test_pipeline_url_noise_is_stripped():
    """on.ci.poll's own log opens with a pipeline URL carrying a numeric id
    that changes every rerun (adapters/forge/ci.py:111) -- confirmed live,
    not theoretical, on Kraft-s7c04.34."""
    a = _extract_message("pipeline failed: https://gitlab.example.com/x/-/pipelines/111\n")
    b = _extract_message("pipeline failed: https://gitlab.example.com/x/-/pipelines/222\n")
    assert a == b


def test_marker_selection_survives_noise_stripping_its_own_marker():
    """Regression guard for the ordering bug: stripping an ordinal that sits
    right after ✘ must not remove the ✘ before marker-selection ever runs,
    or the line silently vanishes from the message instead of being cleaned."""
    text = _extract_message(
        "some setup noise\n  ✘  27 e2e/regression.spec.ts:65:1 › a flaky test (2.0m)\n"
    )
    assert "e2e/regression.spec.ts:65:1" in text
    assert "27" not in text


def test_from_blind_failure_extracts_marker_lines(tmp_path):
    log = _log(
        tmp_path,
        "some setup noise\n"
        "  ✘  27 e2e/regression.spec.ts:65:1 › a flaky test (2.0m)\n"
        "    Error: locator.click: Test timeout of 120000ms exceeded.\n",
    )
    f = from_blind_failure("on.test.run", "wid1", [BlindJob("sess1", log, None)])
    assert f.severity == "critical"
    assert f.source_plugin == "on.test.run"
    assert f.file is None
    assert "e2e/regression.spec.ts:65:1" in f.message
    assert "Test timeout" in f.message


@pytest.mark.parametrize(
    ("hook", "command", "log_a", "log_b"),
    [
        # the same failure at two different wall-clock costs -- the entire point
        # of this feature
        (
            "on.test.run",
            None,
            "✘ 1 e2e/x.spec.ts:1:1 › t (2.0m)\n14:03:11 done\n",
            "✘ 1 e2e/x.spec.ts:1:1 › t (2.3m)\n14:09:58 done\n",
        ),
        # G1: a different Playwright run ordinal (the evidence's own shape)
        (
            "on.test.run",
            "just e2e-ci",
            "  ✘  27 e2e/regression.spec.ts:65:1 › a flaky test (2.0m)\n",
            "  ✘  25 e2e/regression.spec.ts:65:1 › a flaky test (2.3m)\n",
        ),
        # G1: on.ci.poll's own log (adapters/forge/ci.py:111) opens with a
        # pipeline URL whose numeric id changes every rerun. No command here
        # (forge has none), so this is also the no-reproduce message shape.
        (
            "on.ci.poll",
            None,
            "pipeline failed: https://gitlab.example.com/x/-/pipelines/111\n  job lint: failed\n",
            "pipeline failed: https://gitlab.example.com/x/-/pipelines/222\n  job lint: failed\n",
        ),
    ],
    ids=["duration-and-timestamp-noise", "playwright-ordinal-shift", "ci-poll-pipeline-url-shift"],
)
def test_from_blind_failure_fingerprints_the_same_failure_the_same(
    tmp_path, hook, command, log_a, log_b
):
    fa = from_blind_failure(hook, "wid1", [BlindJob("sess1", _log(tmp_path, log_a), command)])
    fb = from_blind_failure(hook, "wid1", [BlindJob("sess2", _log(tmp_path, log_b), command)])
    assert fa.fingerprint == fb.fingerprint


def test_from_blind_failure_falls_back_to_tail_when_no_marker_matches(tmp_path):
    log = _log(tmp_path, "line one\nline two\nline three\n")
    f = from_blind_failure("on.test.run", "wid1", [BlindJob("sess1", log, None)])
    assert "line three" in f.message


def test_from_blind_failure_caps_message_length(tmp_path):
    log = _log(tmp_path, "Error: " + ("x" * 5000) + "\n")
    f = from_blind_failure("on.test.run", "wid1", [BlindJob("sess1", log, None)])
    assert len(f.message) <= 500


def test_from_blind_failure_missing_log_is_not_an_error(tmp_path):
    f = from_blind_failure(
        "on.test.run", "wid1", [BlindJob("sess1", tmp_path / "absent.log", None)]
    )
    assert "no output captured" in f.message
    assert f.severity == "critical"


def test_from_blind_failure_missing_log_fingerprints_the_same_every_time(tmp_path):
    f1 = from_blind_failure(
        "on.test.run", "wid1", [BlindJob("sess1", tmp_path / "absent.log", None)]
    )
    f2 = from_blind_failure(
        "on.test.run", "wid1", [BlindJob("sess2", tmp_path / "still-absent.log", None)]
    )
    assert f1.fingerprint == f2.fingerprint


def test_from_blind_failure_names_the_command_that_ran(tmp_path):
    """G2 (Kraft-s7c04.35): the command in the message is the one that
    actually ran (BlindJob.command), never a generic reproduce string
    plugged in from elsewhere."""
    log = _log(tmp_path, "✘ 1 e2e/x.spec.ts:1:1 › t\n")
    f = from_blind_failure("on.test.run", "wid1", [BlindJob("sess1", log, "just e2e-ci")])
    assert "just e2e-ci" in f.message
    assert "sess1" not in f.message


def test_from_blind_failure_without_command_names_only_the_hook(tmp_path):
    log = _log(tmp_path, "pipeline failed: https://gitlab.example.com/x/-/pipelines/1\n")
    f = from_blind_failure("on.ci.poll", "wid1", [BlindJob("sess1", log, None)])
    assert "on.ci.poll" in f.message
    assert "sess1" not in f.message


def test_from_blind_failure_carries_a_log_ref_per_job(tmp_path):
    log = _log(tmp_path, "✘ 1 e2e/x.spec.ts:1:1 › t\n")
    f = from_blind_failure("on.test.run", "wid1", [BlindJob("sess1", log, "just e2e-ci")])
    assert f.jobs == (JobRef(label="just e2e-ci", log_ref="kraft view logs wid1 --session sess1"),)


def test_from_blind_failure_aggregates_multiple_jobs_into_one_finding(tmp_path):
    """G1 brainstorm: test_scopes fans on.test.run out to several commands in
    one round (C2 runs every scope, not just the first failure) -- all of a
    hook's failing rows in one round become one Finding, not several."""
    a = _log(tmp_path, "✘ 1 e2e/a.spec.ts:1:1 › t\n")
    b = _log(tmp_path, "✘ 1 e2e/b.spec.ts:9:1 › u\n")
    f = from_blind_failure(
        "on.test.run",
        "wid1",
        [
            BlindJob("sess1", a, "just test-ui"),
            BlindJob("sess2", b, "just e2e-ci"),
        ],
    )
    assert "just test-ui" in f.message
    assert "just e2e-ci" in f.message
    assert len(f.jobs) == 2
    assert {j.label for j in f.jobs} == {"just test-ui", "just e2e-ci"}


def test_from_blind_failure_confirm_line_lists_every_job_command(tmp_path):
    a = _log(tmp_path, "x\n")
    b = _log(tmp_path, "y\n")
    f = from_blind_failure(
        "on.test.run",
        "wid1",
        [BlindJob("sess1", a, "just test-ui"), BlindJob("sess2", b, "just e2e-ci")],
    )
    assert "just test-ui" in f.message.rsplit("\n\n", 1)[-1]
    assert "just e2e-ci" in f.message.rsplit("\n\n", 1)[-1]


def test_from_blind_failure_no_confirm_line_without_any_command(tmp_path):
    log = _log(tmp_path, "pipeline failed\n")
    f = from_blind_failure("on.ci.poll", "wid1", [BlindJob("sess1", log, None)])
    assert "Confirm the fix with" not in f.message


def test_from_blind_failure_different_content_is_a_different_fingerprint(tmp_path):
    """Granularity check: this bundle must not collapse two genuinely
    different failures under the same command into one identity."""
    a = _log(tmp_path, "✘ 1 e2e/a.spec.ts:1:1 › test A\n")
    b = _log(tmp_path, "✘ 1 e2e/b.spec.ts:9:1 › test B\n")
    fa = from_blind_failure("on.test.run", "wid1", [BlindJob("sess1", a, "just e2e-ci")])
    fb = from_blind_failure("on.test.run", "wid1", [BlindJob("sess2", b, "just e2e-ci")])
    assert fa.fingerprint != fb.fingerprint
