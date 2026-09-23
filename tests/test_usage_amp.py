"""The amp-stream-json usage reader (Kraft-gvrke).

DOCS-DERIVED, NOT CAPTURED: nobody running these has an Amp account, so the
log below is the "Tool usage example" from ampcode.com/docs/cli/streaming-json
(read 2026-09-23), with the doc's line-wrap artefacts ("end_tu rn", "4 97f")
removed and the long `tools` list cut. Replace it with a real capture once
someone has run `amp -x --stream-json`."""

from __future__ import annotations

from kraft import usage
from kraft.adapters import agent
from kraft.usage import Usage

_THREAD = "T-d2fc4acc-dd1d-497f-9609-ed0da22a7c95"
_AMP_RUN = [
    '{"type":"system","subtype":"init","cwd":"/Users/orb/project","session_id":"' + _THREAD + '",'
    '"tools":["Bash","Read"],"mcp_servers":[]}',
    '{"type":"user","message":{"role":"user","content":[{"type":"text",'
    '"text":"list files using a tool"}]},"parent_tool_use_id":null,"session_id":"' + _THREAD + '"}',
    '{"type":"assistant","message":{"type":"message","role":"assistant","content":[{"type":'
    '"tool_use","id":"toolu_019cyniPYrSgaJitUSMyxyNV","name":"read","input":{"path":'
    '"/Users/orb/project"}}],"stop_reason":"tool_use","usage":{"input_tokens":10,'
    '"cache_creation_input_tokens":13150,"cache_read_input_tokens":0,"output_tokens":111,'
    '"max_tokens":968000,"service_tier":"standard"}},"parent_tool_use_id":null,'
    '"session_id":"' + _THREAD + '"}',
    '{"type":"user","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":'
    '"toolu_019cyniPYrSgaJitUSMyxyNV","content":"[\\"index.js\\",\\"README.md\\"]",'
    '"is_error":false}]},"parent_tool_use_id":null,"session_id":"' + _THREAD + '"}',
    '{"type":"assistant","message":{"type":"message","role":"assistant","content":[{"type":'
    '"text","text":"Two files: index.js and README.md"}],"stop_reason":"end_turn","usage":'
    '{"input_tokens":7,"cache_creation_input_tokens":133,"cache_read_input_tokens":13150,'
    '"output_tokens":13,"max_tokens":968000,"service_tier":"standard"}},'
    '"parent_tool_use_id":null,"session_id":"' + _THREAD + '"}',
    '{"type":"result","subtype":"success","duration_ms":7363,"is_error":false,"num_turns":2,'
    '"result":"Two files: index.js and README.md","session_id":"' + _THREAD + '"}',
]

#: Both requests' usage, summed.
_SPENT = Usage(tokens_in=17, tokens_out=124, tokens_cache_write=13283, tokens_cache_read=13150)


def _log(tmp_path, lines):
    log = tmp_path / "amp.log"
    log.write_text("\n".join(lines) + "\n")
    return log


def test_the_claude_reader_records_nothing_for_an_amp_log(tmp_path):
    """Why Amp has its own reader: its `result` line has no `modelUsage`,
    no cost and (in every published example) no `usage`, so the claude
    envelope read finds nothing to count."""
    assert (
        usage.read(_log(tmp_path, _AMP_RUN), tmp_path / "none.json", "claude-stream-json") is None
    )


def test_amp_reader_sums_each_request_and_reports_no_cost(tmp_path):
    u = usage.read(_log(tmp_path, _AMP_RUN), tmp_path / "none.json", "amp-stream-json")
    assert u == _SPENT
    assert u.cost_usd is None


def test_amp_reader_counts_a_second_invocation_in_the_same_log(tmp_path):
    """A resume appends a new run to the log. Amp reports no running totals,
    so each run's own requests add."""
    log = _log(tmp_path, [*_AMP_RUN, *_AMP_RUN[1:]])
    u = usage.read(log, tmp_path / "none.json", "amp-stream-json")
    assert u.tokens_out == 2 * _SPENT.tokens_out


def test_amp_reader_live_progress_matches_the_envelope():
    u = usage.READERS["amp-stream-json"].stream(_AMP_RUN, {})
    assert u == _SPENT


def test_amp_reader_takes_the_thread_id_resume_needs_off_the_init_line(tmp_path):
    assert usage.READERS["amp-stream-json"].session_id(_log(tmp_path, _AMP_RUN)) == _THREAD


def test_amp_reader_survives_a_missing_log(tmp_path):
    assert usage.READERS["amp-stream-json"].envelope(tmp_path / "gone.log") is None


def test_an_amp_error_result_fails_the_session(tmp_path):
    """Amp ends its stream with Claude Code's `result` line, so the agent's
    envelope check reads its `is_error` unchanged. The `error_during_execution`
    shape from the same docs page. Here rather than in adapters/test_agent.py,
    which is at its line budget."""
    log = _log(
        tmp_path,
        [
            _AMP_RUN[0],
            '{"type":"result","subtype":"error_during_execution","duration_ms":90,'
            '"is_error":true,"num_turns":1,"error":"boom","session_id":"' + _THREAD + '"}',
        ],
    )
    assert agent._envelope_is_error("done", log, 0, "amp-stream-json") == "failed"
