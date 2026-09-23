"""The cursor-stream-json reader (Kraft-bosip).

CAPTURED, not docs-derived: lines from two real runs of cursor-agent
2026.09.18-9a7762b on 2026-09-23, launched with the argv and env Kraft builds
-- a work item that committed a file, then `--resume` of the same chat
appended into the same log, as a re-launch does. Thinking deltas and tool
calls are cut; every line kept is verbatim.
"""

from __future__ import annotations

from kraft import usage
from kraft.usage import Usage

_SID = "65f43d5b-3d4f-4025-903f-6caf23bfcdaf"
_INIT = (
    '{"type":"system","subtype":"init","apiKeySource":"login",'
    '"cwd":"/Users/omidkarami/kraft-bosip-probe-7f3a/wt",'
    f'"session_id":"{_SID}","model":"Auto","permissionMode":"default"}}'
)
_CURSOR_RUN = [
    _INIT,
    '{"type":"thinking","subtype":"delta","text":" item. I will create",'
    f'"session_id":"{_SID}","timestamp_ms":1790154073288}}',
    f'{{"type":"thinking","subtype":"completed","session_id":"{_SID}","timestamp_ms":1790154076363}}',
    '{"type":"result","subtype":"success","duration_ms":21631,"duration_api_ms":21631,'
    '"is_error":false,"result":"Using the Kraft implement instructions: create `hello.txt`, '
    "commit it, then write the session summary and result file. Checking the environment and "
    "repo state first.Done. Created `hello.txt`, committed as `add hello` (`b559d84`), wrote "
    'the session summary, and set the result to `done`.",'
    f'"session_id":"{_SID}","request_id":"088aae5f-7123-4d4e-962b-6e7a3c4506b3",'
    '"usage":{"inputTokens":18393,"outputTokens":872,"cacheReadTokens":50432,'
    '"cacheWriteTokens":0}}',
]
_RESUMED = [
    _INIT,
    '{"type":"result","subtype":"success","duration_ms":8008,"duration_api_ms":8008,'
    f'"is_error":false,"result":"add hello","session_id":"{_SID}",'
    '"request_id":"2efff1ac-7fa1-41bc-8634-23ffea2616a9","usage":{"inputTokens":745,'
    '"outputTokens":485,"cacheReadTokens":23552,"cacheWriteTokens":0}}',
]


def _log(tmp_path, lines):
    log = tmp_path / "cursor.log"
    log.write_text("\n".join(lines) + "\n")
    return log


def test_cursor_tokens_come_off_the_result_line(tmp_path):
    """Uncached input is `inputTokens` as given: cache reads (50432) exceed it,
    so they are not a share of it. No cost and no model: Cursor reports no
    price, and "Auto" is a display name."""
    u = usage.read(_log(tmp_path, _CURSOR_RUN), tmp_path / "none.json", "cursor-stream-json")
    assert u == Usage(tokens_in=18393, tokens_out=872, tokens_cache_read=50432)


def test_a_relaunch_into_the_same_log_adds_its_own_tokens(tmp_path):
    """Each `result` is its own invocation's: the resumed run reported 745
    input, not a running total, so the two are summed."""
    u = usage.read(
        _log(tmp_path, _CURSOR_RUN + _RESUMED), tmp_path / "none.json", "cursor-stream-json"
    )
    assert u == Usage(tokens_in=18393 + 745, tokens_out=872 + 485, tokens_cache_read=50432 + 23552)


def test_a_cursor_log_with_no_result_records_nothing(tmp_path):
    """Killed before its `result` line: no tokens are known, so none, not zero."""
    assert (
        usage.read(_log(tmp_path, _CURSOR_RUN[:-1]), tmp_path / "x", "cursor-stream-json") is None
    )
    assert usage.READERS["cursor-stream-json"].stream(_CURSOR_RUN, {}) is None


def test_cursor_reader_session_id_is_the_init_lines(tmp_path):
    reader = usage.READERS["cursor-stream-json"]
    assert reader.session_id(_log(tmp_path, _CURSOR_RUN)) == _SID
    assert reader.session_id(tmp_path / "nope.log") is None
