"""The cursor-stream-json reader (Kraft-bosip).

The fixture is DOCS-DERIVED, not captured: it is the example sequence from
cursor.com/docs/cli/reference/output-format, trimmed. Nobody here has a
Cursor account, so no real run exists to capture.
"""

from __future__ import annotations

from kraft import usage

_SID = "c6b62c6f-7ead-4fd6-9922-e952131177ff"
_CURSOR_RUN = [
    '{"type":"system","subtype":"init","apiKeySource":"login","cwd":"/Users/user/project",'
    f'"session_id":"{_SID}","model":"Claude 4 Sonnet","permissionMode":"default"}}',
    '{"type":"user","message":{"role":"user","content":[{"type":"text","text":"Read README.md"}]},'
    f'"session_id":"{_SID}"}}',
    '{"type":"assistant","message":{"role":"assistant","content":[{"type":"text",'
    f'"text":"I\'ll read the README.md file"}}]}},"session_id":"{_SID}"}}',
    '{"type":"tool_call","subtype":"started","call_id":"toolu_1","tool_call":{"readToolCall":'
    f'{{"args":{{"path":"README.md"}}}}}},"session_id":"{_SID}"}}',
    '{"type":"tool_call","subtype":"completed","call_id":"toolu_1","tool_call":{"readToolCall":'
    '{"args":{"path":"README.md"},"result":{"success":{"content":"# Project","isEmpty":false,'
    f'"exceededLimit":false,"totalLines":1,"totalChars":9}}}}}}}},"session_id":"{_SID}"}}',
    '{"type":"result","subtype":"success","duration_ms":5234,"duration_api_ms":5234,'
    f'"is_error":false,"result":"Done","session_id":"{_SID}","request_id":"10e11780"}}',
]


def _log(tmp_path):
    log = tmp_path / "cursor.log"
    log.write_text("\n".join(_CURSOR_RUN) + "\n")
    return log


def test_cursor_reader_records_no_usage_rather_than_zero():
    """The stream carries no tokens, and `init.model` is a display name. The
    claude reader would still fold the init line into a live row of zero
    tokens under "Claude 4 Sonnet"; this one reports nothing, so the row stays
    NULL -- not reported."""
    assert usage.READERS["claude-stream-json"].stream(_CURSOR_RUN, {}) is not None
    assert usage.READERS["cursor-stream-json"].stream(_CURSOR_RUN, {}) is None


def test_cursor_reader_session_id_is_the_init_lines(tmp_path):
    reader = usage.READERS["cursor-stream-json"]
    assert reader.session_id(_log(tmp_path)) == _SID
    assert reader.session_id(tmp_path / "nope.log") is None
