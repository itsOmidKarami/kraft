"""The antigravity harness's argv and its antigravity-stream-json reader
(Kraft-fjcpr).

CAPTURED, not docs-derived: lines from real runs of agy 1.2.14 on 2026-10-01,
signed in -- a one-word reply, then `--conversation` of the same
conversation appended into the same log, as a re-launch does; and a run that
called a tool. The `init` event's long `tools` list is cut; every other line
kept is verbatim. The rate-limit line is not captured: no run was limited.
"""

from __future__ import annotations

import json
import os
import pwd
import subprocess

import pytest

from kraft import harness, usage
from kraft.usage import Usage

_SID = "f5d01137-fcda-4594-a3fb-61e2d5c90acf"
_INIT = (
    f'{{"event":"init","conversation_id":"{_SID}","init":{{"model":"gemini-3.8-flash-low",'
    '"cwd":"/private/tmp/claude-501/agyprobe/w1","permission_mode":"request-review"}}'
)


def _step(i: int, rest: str, sid: str = _SID) -> str:
    head = f'"conversation_id":"{sid}","step_index":{i}'
    return f'{{"event":"step_update","step_update":{{{head},{rest}}}}}'


_FIRST = [
    _INIT,
    _step(0, '"state":"DONE","step_type":"user_input"'),
    _step(
        1,
        '"state":"DONE","step_type":"agent_response","text_delta":"pong\\n",'
        '"duration_seconds":1.404241,"usage":{"input_tokens":12198,"output_tokens":1,'
        '"thinking_tokens":0,"cache_read_tokens":0,"total_tokens":12199}',
    ),
    f'{{"event":"result","result":{{"conversation_id":"{_SID}","status":"SUCCESS",'
    '"response":"pong\\n","duration_seconds":1.451068,"num_turns":1,"usage":{"input_tokens":'
    '12198,"output_tokens":1,"thinking_tokens":0,"cache_read_tokens":0,"total_tokens":12199}}}',
]
# The resumed turn's `result` is the conversation's running total, 24603.
_RESUMED = [
    _INIT,
    _step(2, '"state":"DONE","step_type":"user_input"'),
    _step(3, '"state":"DONE","step_type":"system_message","duration_seconds":0.000118'),
    _step(4, '"state":"ACTIVE","step_type":"agent_response","text_delta":"ping"'),
    _step(
        4,
        '"state":"DONE","step_type":"agent_response","text_delta":"\\n",'
        '"duration_seconds":1.485306,"usage":{"input_tokens":12405,"output_tokens":1,'
        '"thinking_tokens":0,"cache_read_tokens":0,"total_tokens":12406}',
    ),
    f'{{"event":"result","result":{{"conversation_id":"{_SID}","status":"SUCCESS",'
    '"response":"ping\\n","duration_seconds":252.244168,"num_turns":2,"usage":{"input_tokens":'
    '24603,"output_tokens":2,"thinking_tokens":0,"cache_read_tokens":0,"total_tokens":24605}}}',
]
_TOOL_SID = "b78a5be2-1a97-4104-88a0-d186188986af"
# No `--model`, so `init` names none.
_TOOL_RUN = [
    f'{{"event":"init","conversation_id":"{_TOOL_SID}","init":{{'
    '"cwd":"/private/tmp/claude-501/agyprobe/w1","permission_mode":"request-review"}}',
    _step(
        1,
        '"state":"DONE","step_type":"agent_response","duration_seconds":2.832687,'
        '"usage":{"input_tokens":12225,"output_tokens":407,"thinking_tokens":322,'
        '"cache_read_tokens":0,"total_tokens":12632}',
        _TOOL_SID,
    ),
    _step(
        2,
        '"state":"DONE","step_type":"tool","tool_name":"write_to_file","duration_seconds":'
        '0.097926,"tool_info":{"name":"write_to_file","parameters":{"TargetFile":'
        '"/private/tmp/claude-501/agyprobe/w1/hello.txt"}}',
        _TOOL_SID,
    ),
]
# An unknown model: exit 1, no `init`, an empty conversation id.
_BAD_MODEL = (
    '{"event":"result","result":{"conversation_id":"","status":"ERROR","response":"",'
    '"error":"invalid model selection (--model \\"gemini-nope\\" --effort \\"\\"): model '
    'gemini-nope is not recognized as a known model or custom model in settings"}}'
)
_READER = "antigravity-stream-json"


def _log(tmp_path, lines):
    log = tmp_path / "agy.log"
    log.write_text("\n".join(lines) + "\n")
    return log


def test_tokens_are_each_finished_steps_own_summed_never_the_running_result(tmp_path):
    """Both invocations' steps, 12198 + 12405: the resumed `result` already
    holds the first turn's 12198, so adding results would count it twice.
    Thinking is a share of output (407 of which 322 thinking), not added."""
    u = usage.read(_log(tmp_path, _FIRST + _RESUMED), tmp_path / "none.json", _READER)
    assert u == Usage(tokens_in=24603, tokens_out=2, model="gemini-3.8-flash-low")
    tool = usage.read(_log(tmp_path, _TOOL_RUN), tmp_path / "none.json", _READER)
    assert tool == Usage(tokens_in=12225, tokens_out=407)


def test_live_progress_over_a_growing_log_counts_each_step_once():
    reader, seen = usage.READERS[_READER], {}
    reader.stream(_FIRST, seen)
    assert reader.stream(_FIRST[1:] + _RESUMED, seen) == Usage(
        tokens_in=24603, tokens_out=2, model="gemini-3.8-flash-low"
    )


@pytest.mark.parametrize("total", [12000 + 500, 12000 + 500 + 4000], ids=["share", "beside"])
def test_cache_reads_come_off_uncached_input_whichever_way_agy_counts_them(total):
    """No captured run had a cache read, so `input_tokens` is not trusted to
    say whether it includes them: `total_tokens` does."""
    block = {"input_tokens": 12000, "output_tokens": 500, "cache_read_tokens": 4000}
    line = _step(1, '"state":"DONE","usage":' + json.dumps({**block, "total_tokens": total}))
    got = usage.READERS[_READER].stream([line], {})
    assert (got.tokens_in, got.tokens_cache_read) == (total - 500 - 4000, 4000)


def test_the_conversation_id_is_read_off_init(tmp_path):
    reader = usage.READERS[_READER]
    assert reader.session_id(_log(tmp_path, _RESUMED)) == _SID
    assert not reader.reports_cost


def test_a_resource_exhausted_error_is_a_rate_limit_and_a_bad_model_is_not(tmp_path):
    reader = usage.READERS[_READER]
    limited = 'AGY_ERROR: {"status":"RESOURCE_EXHAUSTED","code":429,"retryable":false}'
    info = reader.rate_limit(_log(tmp_path, [*_FIRST[:2], limited]))
    assert info is not None and info.message == limited and info.resets_at is None
    assert reader.rate_limit(_log(tmp_path, [_BAD_MODEL])) is None


def test_argv_skips_permissions_and_resumes_by_conversation():
    """`--dangerously-skip-permissions` on every launch, unasked: without it
    headless agy soft-denies writes and commands and still exits 0. Context
    is folded into `-p`, which takes a dash-led prompt as its value."""
    h = harness.load(None).valid["antigravity"]
    argv = harness.build_argv(
        h, prompt="-x", context="CTX", options={"effort": "high"}, resume="conv-1"
    )
    assert argv[0] == "agy"
    assert "--dangerously-skip-permissions" in argv
    assert argv[argv.index("--conversation") + 1] == "conv-1"
    assert argv[argv.index("--effort") + 1] == "high"
    assert argv[-2:] == ["-p", "CTX\n\n-x"]
    for absent in ("deny_tools", "allowed_tools", "approval_channel"):
        assert not h.supports(absent), absent


@pytest.mark.e2e("agy")
def test_real_agy_stream_is_what_the_reader_reads(tmp_path, monkeypatch):
    """The contract: a tiny prompt on the shipped argv, then a resume of its
    conversation. Two real turns of the signed-in account's quota."""
    # agy's sign-in lives under the operator's real HOME (~/.gemini); the
    # suite's throwaway one makes agy ask for a fresh OAuth login, which a test
    # cannot answer. KRAFT_HOME still isolates Kraft.
    monkeypatch.setenv("HOME", pwd.getpwuid(os.getuid()).pw_dir)
    h = harness.load(None).valid["antigravity"]
    log = tmp_path / "agy.log"

    def turn(resume=None):
        argv = harness.build_argv(
            h,
            prompt="Reply with the single word OK and nothing else. Use no tools.",
            context="This is a smoke test of the command line.",
            options={"model": "gemini-3.8-flash", "effort": "low"},
            resume=resume,
        )
        with log.open("a") as out:
            done = subprocess.run(
                argv,
                cwd=tmp_path,
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=subprocess.STDOUT,
                timeout=300,
            )
        assert done.returncode == 0, log.read_text()[-2000:]

    reader = usage.READERS[_READER]
    turn()
    sid = reader.session_id(log)
    first = usage.read(log, tmp_path / "none.json", _READER)
    assert sid and first.tokens_in > 0 and first.model == "gemini-3.8-flash"
    turn(resume=sid)
    assert reader.session_id(log) == sid
    assert usage.read(log, tmp_path / "none.json", _READER).tokens_in > first.tokens_in
