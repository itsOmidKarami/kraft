"""The codex-json usage reader, against real `codex exec --json` output from
codex-cli 0.155.0 (Kraft-w3kot). Split from test_usage.py for its line budget."""

from __future__ import annotations

import json

from kraft import usage
from kraft.usage import Usage

#: A fresh run, then `codex exec resume` of the same thread, as captured
#: (Kraft-w3kot). The resumed turn's usage is the thread's running total:
#: output 28 = the first turn's 23 + its own 5.
_CODEX_RUN = [
    '{"type":"thread.started","thread_id":"01a0cb2d-88c2-7631-b8b3-45debaca3bf6"}',
    '{"type":"turn.started"}',
    '{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"ok"}}',
    '{"type":"turn.completed","usage":{"input_tokens":20458,"cached_input_tokens":12032,'
    '"cache_write_input_tokens":0,"output_tokens":23,"reasoning_output_tokens":0}}',
]
_CODEX_RESUMED_TURN = (
    '{"type":"turn.completed","usage":{"input_tokens":46200,"cached_input_tokens":18944,'
    '"cache_write_input_tokens":0,"output_tokens":28,"reasoning_output_tokens":0}}'
)
#: A real failed launch (an unknown model): `error`, then `turn.failed` with
#: the same message.
_CODEX_400 = json.dumps(
    {
        "type": "error",
        "status": 400,
        "error": {
            "type": "invalid_request_error",
            "message": "The 'gpt-nonexistent-xyz' model is not supported when using "
            "Codex with a ChatGPT account.",
        },
    },
    separators=(",", ":"),
)
_CODEX_FAILED = [
    '{"type":"thread.started","thread_id":"01a0cb2d-993a-7722-9ad1-ae2e7eea7555"}',
    '{"type":"turn.started"}',
    json.dumps({"type": "error", "message": _CODEX_400}),
    json.dumps({"type": "turn.failed", "error": {"message": _CODEX_400}}),
]


def _codex_log(tmp_path, lines):
    log = tmp_path / "codex.log"
    log.write_text("\n".join(lines) + "\n")
    return log


def test_codex_reader_splits_cached_input_off_uncached_and_reports_no_cost(tmp_path):
    """`cached_input_tokens` is a share of `input_tokens` (the rollout's
    total_tokens 20481 = input 20458 + output 23), so counting both would
    over-report by the cached share. No cost: the stream has none."""
    u = usage.read(_codex_log(tmp_path, _CODEX_RUN), tmp_path / "none.json", "codex-json")
    assert u == Usage(tokens_in=20458 - 12032, tokens_out=23, tokens_cache_read=12032)
    assert u.total == 20458 + 23
    assert u.cost_usd is None


def test_codex_reader_takes_the_last_turn_not_the_sum(tmp_path):
    """`turn.completed.usage` is the thread's running total, so a second turn
    on the same thread replaces the first rather than adding to it."""
    log = _codex_log(tmp_path, [*_CODEX_RUN, '{"type":"turn.started"}', _CODEX_RESUMED_TURN])
    u = usage.read(log, tmp_path / "none.json", "codex-json")
    assert (u.tokens_in, u.tokens_cache_read, u.tokens_out) == (46200 - 18944, 18944, 28)


def test_codex_reader_sums_distinct_threads_in_one_log(tmp_path):
    other = [
        '{"type":"thread.started","thread_id":"t2"}',
        '{"type":"turn.completed","usage":{"input_tokens":100,"output_tokens":7}}',
    ]
    u = usage.read(_codex_log(tmp_path, [*_CODEX_RUN, *other]), tmp_path / "n.json", "codex-json")
    assert u.tokens_out == 23 + 7


def test_codex_reader_live_progress_keeps_the_newest_running_total():
    reader, seen = usage.READERS["codex-json"], {}
    assert reader.stream(_CODEX_RUN[:3], seen) is None
    assert reader.stream(_CODEX_RUN[3:], seen).tokens_out == 23
    assert reader.stream([_CODEX_RESUMED_TURN], seen).tokens_out == 28


def test_codex_reader_session_id_is_the_thread_id(tmp_path):
    reader = usage.READERS["codex-json"]
    log = _codex_log(tmp_path, ["noise", *_CODEX_RUN])
    assert reader.session_id(log) == "01a0cb2d-88c2-7631-b8b3-45debaca3bf6"
    assert reader.session_id(tmp_path / "nope.log") is None


def test_codex_reader_sees_a_usage_limit_in_turn_failed_only(tmp_path):
    """The ChatGPT-plan message and the give-up line after retried 429s, both
    strings from the codex-cli 0.155.0 binary. The real 400 failure is not a
    rate limit, and a bare `error` event alone is not a failed turn."""
    reader = usage.READERS["codex-json"]
    assert reader.rate_limit(_codex_log(tmp_path, _CODEX_FAILED)) is None
    for message in (
        "You’ve hit your usage limit. Upgrade to Plus to continue using Codex",
        "exceeded retry limit, last status: 429 Too Many Requests",
    ):
        failed = json.dumps({"type": "turn.failed", "error": {"message": message}})
        hit = reader.rate_limit(_codex_log(tmp_path, [*_CODEX_RUN[:2], failed]))
        assert hit is not None and hit["message"] == message
        assert hit["resets_at"] is None
        bare = json.dumps({"type": "error", "message": message})
        assert reader.rate_limit(_codex_log(tmp_path, [*_CODEX_RUN[:2], bare])) is None
