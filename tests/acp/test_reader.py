import pytest

from kraft import usage
from kraft.acp import log


def _turn(id_=3, **usage_fields):
    return log.message(
        "from_agent",
        {"jsonrpc": "2.0", "id": id_, "result": {"stopReason": "end_turn", "usage": usage_fields}},
    )


def _update(sid="s", **fields):
    return log.message(
        "from_agent",
        {
            "jsonrpc": "2.0",
            "method": "session/update",
            "params": {"sessionId": sid, "update": {"sessionUpdate": "usage_update", **fields}},
        },
    )


def _write(tmp_path, *lines):
    path = tmp_path / "log"
    path.write_text("".join(x + "\n" for x in lines))
    return path


READER = usage.READERS["acp"]
FULL = dict(
    inputTokens=10, outputTokens=5, thoughtTokens=3, cachedReadTokens=2, cachedWriteTokens=1
)
#: Measured, not made up: OpenCode 2.2.5 (thought tokens are an addend of the total) and
#: Claude's bridge.
OPENCODE = {
    "inputTokens": 7305,
    "outputTokens": 103,
    "totalTokens": 15809,
    "thoughtTokens": 209,
    "cachedReadTokens": 8192,
}
CLAUDE = {
    "inputTokens": 4,
    "outputTokens": 887,
    "cachedReadTokens": 73907,
    "cachedWriteTokens": 38635,
    "totalTokens": 113433,
}


def test_a_turns_tokens_and_the_cumulative_cost_are_read(tmp_path):
    path = _write(
        tmp_path,
        log.event("session", session_id="sess-1", model="big", resumed=None),
        _update(used=123456, size=200000, cost={"amount": 0.01, "currency": "USD"}),
        _update(used=130000, size=200000, cost={"amount": 0.0123, "currency": "USD"}),
        _turn(**FULL),
    )
    u = usage.from_envelope(READER.envelope(path))
    assert (u.tokens_in, u.tokens_out, u.tokens_cache_read, u.tokens_cache_write) == (10, 8, 2, 1)
    assert (u.cost_usd, u.model) == (0.0123, "big")
    assert READER.session_id(path) == "sess-1"


@pytest.mark.parametrize("block", [OPENCODE, CLAUDE], ids=["opencode-2.2.5", "claude-bridge"])
def test_a_measured_usage_block_totals_exactly_what_the_agent_reported(tmp_path, block):
    u = usage.from_envelope(READER.envelope(_write(tmp_path, _turn(**block))))
    assert u.total == block["totalTokens"]
    assert u.tokens_out == block["outputTokens"] + block.get("thoughtTokens", 0)


def test_turns_of_two_launches_in_one_log_both_count_though_their_ids_repeat(tmp_path):
    path = _write(
        tmp_path,
        log.event("starting", agent=["a"]),
        _turn(id_=3, **FULL),
        log.event("starting", agent=["a"]),
        _turn(id_=3, **FULL),
    )
    u = usage.from_envelope(READER.envelope(path))
    assert (u.tokens_in, u.tokens_out) == (20, 16)


def _usd(amount):
    return {"amount": amount, "currency": "USD"}


@pytest.mark.parametrize(
    ("lines", "cost"),
    [
        (
            [log.event("starting"), _update("a", cost=_usd(0.01)), log.event("starting")]
            + [_update("a", cost=_usd(0.03))],
            0.03,
        ),
        (
            [log.event("starting"), _update("a", cost=_usd(0.01)), log.event("starting")]
            + [_update("b", cost=_usd(0.03))],
            0.04,
        ),
    ],
    ids=["a-resumed-session-is-cumulative", "a-fresh-session-on-a-second-launch-adds"],
)
def test_the_cost_of_two_launches_in_one_log_is_summed_per_agent_session(tmp_path, lines, cost):
    u = usage.from_envelope(READER.envelope(_write(tmp_path, *lines, _turn(**FULL))))
    assert u.cost_usd == pytest.approx(cost)


@pytest.mark.parametrize(
    "lines",
    [
        [_update(cost={"amount": 0.04, "currency": "USD"})],
        [_update(cost={"amount": 0.04, "currency": "USD"}), _turn()],
    ],
    ids=["cost-only", "cost-and-an-empty-usage-block"],
)
def test_a_cancelled_turn_with_a_cost_and_no_prompt_usage_keeps_its_cost(tmp_path, lines):
    u = usage.from_envelope(READER.envelope(_write(tmp_path, *lines)))
    assert (u.cost_usd, u.total) == (0.04, 0)


@pytest.mark.parametrize(
    "envelope",
    [
        {"total_cost_usd": 0.5, "usage": {"input_tokens": 0}},
        {"cost_usd": 0.5, "usage": {}},
        {"total_cost_usd": 0.5, "usage": {}, "modelUsage": {}},
    ],
    ids=["cli-result-line", "result-file", "cli-interrupted-envelope"],
)
def test_a_known_cost_beside_no_tokens_is_no_invocation_unless_it_is_an_acp_logs(envelope):
    """Only the ACP log's own envelope keeps a cost that has no tokens (a cancelled turn): a CLI
    result line or a result file that reports a cost and nothing else is still no usage."""
    assert usage.from_envelope(envelope) is None
    assert usage.from_envelope({**envelope, usage.ACP_ENVELOPE: True}).cost_usd == 0.5


def test_context_size_is_never_counted_as_tokens_or_cost(tmp_path):
    path = _write(tmp_path, _update(used=999999, size=200000), _turn(**FULL))
    u = usage.from_envelope(READER.envelope(path))
    assert u.total == 21 and u.cost_usd is None


@pytest.mark.parametrize(
    ("lines", "expect"),
    [
        (
            [
                log.message(
                    "from_agent",
                    {
                        "jsonrpc": "2.0",
                        "id": 9,
                        "result": {"stopReason": "end_turn", "usage": FULL},
                    },
                    replay=True,
                )
            ],
            None,
        ),
        ([_update(cost={"amount": 5.0, "currency": "EUR"}), _turn(**FULL)], None),
    ],
    ids=["replayed-history-skipped", "non-usd-cost-unknown"],
)
def test_replay_and_foreign_currency_are_not_counted(tmp_path, lines, expect):
    path = _write(tmp_path, *lines)
    u = usage.from_envelope(READER.envelope(path))
    assert (u.cost_usd if u else None) is expect


def test_the_live_stream_folds_new_lines_into_seen_across_ticks():
    seen: dict = {}
    assert READER.stream([_update(cost={"amount": 0.5, "currency": "USD"})], seen).cost_usd == 0.5
    u = READER.stream([_turn(**FULL)], seen)
    assert (u.total, u.cost_usd) == (21, 0.5)
    assert READER.stream([], {}) is None


def test_the_live_stream_keeps_the_launch_ordinal_across_ticks():
    seen: dict = {}
    READER.stream([log.event("starting"), _turn(**FULL)], seen)
    u = READER.stream([log.event("starting"), _turn(**FULL)], seen)  # the second tick, a new launch
    assert u.tokens_in == 20


def test_the_reader_declares_per_turn_usage_and_no_rate_limit(tmp_path):
    assert READER.running_totals is False
    assert READER.rate_limit(_write(tmp_path, _turn(**FULL))) is None


def test_a_log_with_no_acp_lines_reads_as_no_usage(tmp_path):
    assert READER.envelope(_write(tmp_path, "plain text")) is None
