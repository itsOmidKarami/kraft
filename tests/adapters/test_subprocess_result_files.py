"""`kraft.adapters.subprocess`: reading a session's result, exit and log
files -- no child process. `run_task` itself is test_subprocess.py."""

import json
import subprocess
import sys

import pytest

from kraft.adapters import subprocess as sp
from kraft.usage import RateLimitInfo

# --- the result and exit files -------------------------------------------------------


def test_result_path_for_matches_the_convention_run_task_uses(run_dirs):
    assert sp.result_path_for(run_dirs, "abc123") == run_dirs.results / "abc123.json"


@pytest.mark.parametrize(
    "content, rc, expected",
    [
        (None, 0, "done"),
        (None, 3, "failed"),
        # The result file wins over the exit code, either way round.
        ('{"status": "failed"}', 0, "failed"),
        ('{"status": "done"}', 1, "done"),
        ('{"status": "done_with_concerns", "concerns": "untested path"}', 0, "done_with_concerns"),
        ('{"status": "needs_context", "question": "which branch?"}', 0, "needs_context"),
        # Empty is absent: fall through to the exit code.
        ("", 0, "done"),
        # Chunk C: a non-empty file that is not a dict with a known status is
        # failed, whatever the exit code.
        ("not json", 0, "failed"),
        ('{"status": "made_up_status"}', 0, "failed"),
        ("42", 0, "failed"),
        ("[]", 0, "failed"),
    ],
    ids=[
        "absent-rc0",
        "absent-rc3",
        "failed-over-rc0",
        "done-over-rc1",
        "done-with-concerns",
        "needs-context",
        "empty-is-absent",
        "not-json",
        "unknown-status",
        "a-number",
        "a-list",
    ],
)
def test_resolve_reads_the_result_file_over_the_exit_code(tmp_path, content, rc, expected):
    path = tmp_path / "r.json"
    if content is not None:
        path.write_text(content)
    assert sp._resolve(path, rc) == expected


def test_resolve_result_file_survives_non_utf8_bytes(tmp_path):
    """A plugin that died mid-write leaves bytes that are not valid UTF-8.
    UnicodeDecodeError is a ValueError, so `except OSError` alone misses it and
    the session's own status read takes the process down."""
    p = tmp_path / "r.json"
    p.write_bytes(b"\xff\xfe\x00binary")
    assert sp._resolve_result_file(p) == "failed"


def test_read_result_fields_is_the_one_place_the_field_list_lives(tmp_path):
    """`run_task` and `reattach._exit_from_file` are two independent readers of
    the same result file; a field spelled out in only one of them would reach
    the DB on one exit path and silently drop on the other after a restart
    (Kraft-k3d). Both call this instead of enumerating the fields."""
    path = tmp_path / "result.json"
    path.write_text(
        json.dumps(
            {
                "status": "done_with_concerns",
                "session_summary_ref": ".engineering/sessions/s1.md",
                "concerns": "the retry path is untested",
                "question": "which branch?",
            }
        )
    )
    assert sp.read_result_fields(path) == {
        "summary_ref": ".engineering/sessions/s1.md",
        "concerns": "the retry path is untested",
        "question": "which branch?",
    }
    assert sp.read_result_fields(tmp_path / "missing.json") == dict.fromkeys(
        ("summary_ref", "concerns", "question")
    )


@pytest.mark.parametrize(
    "payload, concerns, question",
    [
        ({"status": "done_with_concerns", "concerns": "untested"}, "untested", None),
        ({"status": "needs_context", "question": "which branch?"}, None, "which branch?"),
    ],
    ids=["concerns", "question"],
)
def test_read_concerns_and_read_question_read_their_own_field(
    tmp_path, payload, concerns, question
):
    path = tmp_path / "r.json"
    path.write_text(json.dumps(payload))
    assert (sp.read_concerns(path), sp.read_question(path)) == (concerns, question)


@pytest.mark.parametrize(
    "raw",
    [None, b"not json", b"[1, 2, 3]", b'{"status": "done"}', b"\xff\xfe\x00\x01"],
    ids=["missing", "not-json", "not-a-mapping", "key-absent", "not-utf8"],
)
@pytest.mark.parametrize(
    "reader", [sp.read_summary_ref, sp.read_concerns, sp.read_question, sp.read_verdict]
)
def test_the_field_readers_tolerate_a_broken_file(tmp_path, reader, raw):
    """UnicodeDecodeError is a ValueError, so `except OSError` does not catch
    it. That exact clause has been wrong three times in this codebase."""
    path = tmp_path / "r.json"
    if raw is not None:
        path.write_bytes(raw)
    assert reader(path) is None


def test_read_verdict(tmp_path):
    p = tmp_path / "r.json"
    p.write_text(json.dumps({"status": "done", "verdict": "approve"}))
    assert sp.read_verdict(p) == "approve"


_REJECTED = (
    '{"type":"rate_limit_event","rate_limit_info":{"status":"rejected",'
    '"resetsAt":1788968400,"rateLimitType":"five_hour"}}\n'
)


@pytest.mark.parametrize(
    "log, reader, expected",
    [
        (
            '{"type":"assistant","message":{}}\n'
            + _REJECTED
            + '{"type":"result","is_error":true}\n',
            "claude-stream-json",
            RateLimitInfo(
                rate_limit_type="five_hour",
                resets_at=1788968400,
                resets_at_iso="2026-09-09T15:40:00+00:00",
            ),
        ),
        # `overageStatus` can read "rejected" while the turn itself was
        # allowed: only a top-level `status: "rejected"` refused the launch.
        (
            '{"type":"rate_limit_event","rate_limit_info":{"status":"allowed",'
            '"overageStatus":"rejected","resetsAt":1788986400,"rateLimitType":"five_hour"}}\n',
            "claude-stream-json",
            None,
        ),
        ('{"type":"result","is_error":true}\n', "claude-stream-json", None),
        (None, "claude-stream-json", None),
        # A harness that never declared rate_limit_signal must not have its log
        # parsed against claude's schema -- a check that SKIPS reads as one
        # that PASSED.
        (_REJECTED, None, None),
    ],
    ids=[
        "rejected",
        "allowed-with-overage-rejected",
        "no-event",
        "unreadable-log",
        "no-capability",
    ],
)
def test_rate_limit_rejection(tmp_path, log, reader, expected):
    path = tmp_path / "s.log"
    if log is not None:
        path.write_text(log)
    assert sp._rate_limit_rejection(path, reader=reader) == expected


def test_exit_wrapper_preserves_argv_exactly(tmp_path):
    """The wrapper must replay argv byte-for-byte, spaces and quotes included."""
    out = tmp_path / "argv.txt"
    weird = ["a b", "c'd", 'e"f', "g*h", "--flag=i j"]
    cmd = [
        sys.executable,
        "-c",
        "import sys,pathlib; pathlib.Path(sys.argv[1]).write_text(repr(sys.argv[2:]))",
        str(out),
        *weird,
    ]
    subprocess.run(sp._wrap_with_exit_file(cmd, tmp_path / "s.exit"), check=True)
    assert eval(out.read_text()) == weird


@pytest.mark.parametrize("code, expected", [(0, "done"), (7, "failed")])
def test_exit_wrapper_records_the_code_and_propagates_it(tmp_path, code, expected):
    exit_path = tmp_path / "s.exit"
    wrapped = sp._wrap_with_exit_file(
        [sys.executable, "-c", f"raise SystemExit({code})"], exit_path
    )
    assert subprocess.run(wrapped).returncode == code, "the wrapper must not swallow the code"
    assert sp._resolve_exit_file(exit_path) == expected


@pytest.mark.parametrize("content", [None, "not a number"], ids=["missing", "garbage"])
def test_resolve_exit_file_missing_or_garbage_is_none(tmp_path, content):
    path = tmp_path / "s.exit"
    if content is not None:
        path.write_text(content)
    assert sp._resolve_exit_file(path) is None
