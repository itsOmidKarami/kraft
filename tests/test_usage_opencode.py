"""The opencode-json usage reader (Kraft-nv1f1). Split from test_usage.py for
its line budget, like test_usage_codex.py.

Two kinds of fixture, and each says which it is. *Captured*: real
`opencode run --format json` output from opencode-ai 1.18.32 on 2026-09-23,
on the free `opencode/big-pickle` model with no account, which is why every
cost is 0 and no step reports reasoning or cache writes. *Source-derived*:
shaped from the v1.18.32 source for what no free run shows -- a priced step,
reasoning tokens, a rate limit."""

from __future__ import annotations

import json
import subprocess

import pytest

from kraft import usage
from kraft.usage import Usage

#: The real one, taken before tests/conftest.py's guard replaces it.
REAL_EXPORT = usage._export_opencode


@pytest.fixture(autouse=True)
def _no_export(monkeypatch):
    """The session export is opencode's own store, not the log under test: a
    test that wants it says what it returns."""
    monkeypatch.setattr(usage, "_export_opencode", lambda session_id: None)


_SID = "ses_f3458fdd8ffeLKVkq60MrRPGGt"


def _line(event):
    """One event as opencode writes it: compact JSON on one line."""
    return json.dumps(event, separators=(",", ":"))


#: Captured: one run that called bash, so two steps, each with its own usage.
_RUN = [
    _line(
        {
            "type": "step_start",
            "timestamp": 1790123116975,
            "sessionID": "ses_f3458fdd8ffeLKVkq60MrRPGGt",
            "part": {
                "id": "prt_0cba7099f001agjKCQ26AHOvph",
                "messageID": "msg_0cba702db0016BJqKMBR7vi4pn",
                "sessionID": "ses_f3458fdd8ffeLKVkq60MrRPGGt",
                "type": "step-start",
            },
        }
    ),
    _line(
        {
            "type": "tool_use",
            "timestamp": 1790123117217,
            "sessionID": "ses_f3458fdd8ffeLKVkq60MrRPGGt",
            "part": {
                "type": "tool",
                "tool": "bash",
                "callID": "call_fd66be7cc43049d98a5fb339",
                "state": {
                    "status": "completed",
                    "input": {"command": "echo kraft-probe"},
                    "output": "kraft-probe\n",
                    "metadata": {"output": "kraft-probe\n", "exit": 0, "truncated": False},
                    "title": "echo kraft-probe",
                    "time": {"start": 1790123117166, "end": 1790123117216},
                },
                "id": "prt_0cba709ae0013ZH9271jWDM5jQ",
                "sessionID": "ses_f3458fdd8ffeLKVkq60MrRPGGt",
                "messageID": "msg_0cba702db0016BJqKMBR7vi4pn",
            },
        }
    ),
    _line(
        {
            "type": "step_finish",
            "timestamp": 1790123117349,
            "sessionID": "ses_f3458fdd8ffeLKVkq60MrRPGGt",
            "part": {
                "id": "prt_0cba70b1b001T4aNnLenG0rvY2",
                "reason": "tool-calls",
                "messageID": "msg_0cba702db0016BJqKMBR7vi4pn",
                "sessionID": "ses_f3458fdd8ffeLKVkq60MrRPGGt",
                "type": "step-finish",
                "tokens": {
                    "total": 7996,
                    "input": 238,
                    "output": 78,
                    "reasoning": 0,
                    "cache": {"write": 0, "read": 7680},
                },
                "cost": 0,
            },
        }
    ),
    _line(
        {
            "type": "step_start",
            "timestamp": 1790123117894,
            "sessionID": "ses_f3458fdd8ffeLKVkq60MrRPGGt",
            "part": {
                "id": "prt_0cba70d3f0015kxeV0hSp9JNl6",
                "messageID": "msg_0cba70b23001i6BCcNFXkdyGAf",
                "sessionID": "ses_f3458fdd8ffeLKVkq60MrRPGGt",
                "type": "step-start",
            },
        }
    ),
    _line(
        {
            "type": "text",
            "timestamp": 1790123117946,
            "sessionID": "ses_f3458fdd8ffeLKVkq60MrRPGGt",
            "part": {
                "id": "prt_0cba70d4f001BnEQFSbw8eskye",
                "messageID": "msg_0cba70b23001i6BCcNFXkdyGAf",
                "sessionID": "ses_f3458fdd8ffeLKVkq60MrRPGGt",
                "type": "text",
                "text": "done",
                "time": {"start": 1790123117903, "end": 1790123117924},
            },
        }
    ),
    _line(
        {
            "type": "step_finish",
            "timestamp": 1790123117946,
            "sessionID": "ses_f3458fdd8ffeLKVkq60MrRPGGt",
            "part": {
                "id": "prt_0cba70d66001mDCeZ3Ws8ZFjc0",
                "reason": "stop",
                "messageID": "msg_0cba70b23001i6BCcNFXkdyGAf",
                "sessionID": "ses_f3458fdd8ffeLKVkq60MrRPGGt",
                "type": "step-finish",
                "tokens": {
                    "total": 8014,
                    "input": 75,
                    "output": 3,
                    "reasoning": 0,
                    "cache": {"write": 0, "read": 7936},
                },
                "cost": 0,
            },
        }
    ),
]

#: Captured: `--session` of an earlier one-step session (input 6113, cache
#: read 1792). Input 240 here, so a step is its own request, not a running total.
_RESUMED_STEP = _line(
    {
        "type": "step_finish",
        "timestamp": 1790123107731,
        "sessionID": "ses_f345a4c2dffeAZ7R7NAx3lScpk",
        "part": {
            "id": "prt_0cba6e58c001ONCIls3zW0kG0C",
            "reason": "stop",
            "messageID": "msg_0cba6de24001Fcq3frYLusmu1k",
            "sessionID": "ses_f345a4c2dffeAZ7R7NAx3lScpk",
            "type": "step-finish",
            "tokens": {
                "total": 7957,
                "input": 240,
                "output": 37,
                "reasoning": 0,
                "cache": {"write": 0, "read": 7680},
            },
            "cost": 0,
        },
    }
)

#: Captured: `-m anthropic/claude-sonnet-4-5` with no key. Exit 1, this one line.
_NO_KEY = _line(
    {
        "type": "error",
        "timestamp": 1790123053785,
        "sessionID": "ses_f3459edd7ffekJ1dd0xTOvfdj9",
        "error": {
            "name": "UnknownError",
            "data": {
                "message": "Unexpected server error. Check server logs for details.",
                "ref": "err_b435b51c",
            },
        },
    }
)


def _step(part_id, cost, **tokens):
    """Source-derived: a `step_finish` event as run.ts `emit` writes it, its
    part as sdk/js/src/v2/gen/types.gen.ts `StepFinishPart` types it."""
    t = {"input": 0, "output": 0, "reasoning": 0, "cache": {"read": 0, "write": 0}, **tokens}
    part = {"id": part_id, "type": "step-finish", "reason": "stop", "tokens": t, "cost": cost}
    return json.dumps({"type": "step_finish", "timestamp": 1, "sessionID": _SID, "part": part})


def _limited(**data):
    """Source-derived: an `APIError` as session.error carries it once retry.ts
    gives up -- `{name, data}` per core/src/util/error.ts, `data` per
    core/src/v1/session.ts."""
    err = {"name": "APIError", "data": {"message": "request failed", "isRetryable": True, **data}}
    return json.dumps(
        {"type": "error", "timestamp": 1790000000000, "sessionID": _SID, "error": err}
    )


def _log(tmp_path, lines):
    log = tmp_path / "opencode.log"
    log.write_text("\n".join(lines) + "\n")
    return log


def test_opencode_reader_sums_every_step_of_a_run(tmp_path):
    u = usage.read(_log(tmp_path, _RUN), tmp_path / "none.json", "opencode-json")
    assert u == Usage(
        tokens_in=238 + 75, tokens_out=78 + 3, tokens_cache_read=7680 + 7936, cost_usd=0.0
    )


def test_opencode_reader_adds_reasoning_to_output_and_sums_cost(tmp_path):
    """`output` is already less `reasoning` (session.ts `getUsage`), and both
    were billed as output. Cache writes are their own kind."""
    lines = [
        _step("p1", 0.25, input=10, output=5, reasoning=7, cache={"read": 3, "write": 11}),
        _step("p2", 0.5, input=1, output=1),
    ]
    u = usage.read(_log(tmp_path, lines), tmp_path / "none.json", "opencode-json")
    assert u == Usage(
        tokens_in=11, tokens_out=13, tokens_cache_read=3, tokens_cache_write=11, cost_usd=0.75
    )


def test_opencode_reader_a_step_with_no_cost_makes_the_cost_unknown(tmp_path):
    lines = [_step("p1", 0.25, input=10), _step("p2", None, input=1)]
    u = usage.read(_log(tmp_path, lines), tmp_path / "none.json", "opencode-json")
    assert (u.tokens_in, u.cost_usd) == (11, None)


def test_opencode_reader_live_progress_counts_a_repeated_step_once():
    reader, seen = usage.READERS["opencode-json"], {}
    assert reader.stream(_RUN[:2], seen) is None
    assert reader.stream(_RUN[2:3], seen).tokens_out == 78
    assert reader.stream([*_RUN[3:], _RUN[2]], seen).tokens_out == 78 + 3
    assert reader.stream([_RESUMED_STEP], seen).tokens_out == 78 + 3 + 37


def test_opencode_reader_session_id_and_no_usage_on_a_failed_launch(tmp_path):
    reader = usage.READERS["opencode-json"]
    assert reader.session_id(_log(tmp_path, ["noise", *_RUN])) == _SID
    assert reader.session_id(tmp_path / "nope.log") is None
    failed = _log(tmp_path, [_NO_KEY])
    assert usage.read(failed, tmp_path / "none.json", "opencode-json") is None
    assert reader.rate_limit(failed) is None


def test_opencode_reader_sees_a_rate_limit_and_its_retry_after(tmp_path):
    reader = usage.READERS["opencode-json"]
    hit = reader.rate_limit(
        _log(tmp_path, [*_RUN[:2], _limited(statusCode=429, responseHeaders={"retry-after": "60"})])
    )
    assert hit.rate_limit_type == "APIError"
    assert hit.resets_at == 1790000000 + 60
    assert hit.resets_at_iso == "2026-09-21T14:14:20+00:00"
    free = _limited(statusCode=402, responseBody='{"type":"FreeUsageLimitError"}')
    hit = reader.rate_limit(_log(tmp_path, [free]))
    assert hit is not None and hit.resets_at is None and hit.resets_at_iso is None
    assert reader.rate_limit(_log(tmp_path, [_limited(statusCode=500)])) is None


#: Captured from opencode 2.0.15 (`opencode session export`, 2026-09-23), the
#: `info` of a run whose log carried 10 of its 11 `step_finish` events.
_EXPORT_INFO = {
    "cost": 0,
    "tokens": {
        "input": 32898,
        "output": 1529,
        "reasoning": 317,
        "cache": {"read": 117248, "write": 0},
    },
}


def test_opencode_reader_prefers_the_session_export_to_the_log(tmp_path, monkeypatch):
    """Kraft-ihoen: 2.x drops the last step's `step_finish` from the log, so
    the export's session total wins, asked of the log's own session id."""
    asked = []
    total = Usage(tokens_in=32898, tokens_out=1529 + 317, tokens_cache_read=117248, cost_usd=0.0)
    monkeypatch.setattr(usage, "_export_opencode", lambda sid: asked.append(sid) or total)
    assert usage.read(_log(tmp_path, _RUN), tmp_path / "none.json", "opencode-json") == total
    assert asked == [_SID]


def test_opencode_reader_falls_back_to_the_log_when_the_export_cannot_answer(tmp_path):
    u = usage.read(_log(tmp_path, _RUN), tmp_path / "none.json", "opencode-json")
    assert u.tokens_out == 78 + 3


def test_opencode_export_reads_info_as_a_step_and_refuses_what_is_not_one(monkeypatch):
    def fake(stdout, returncode=0):
        def run(argv, **kwargs):
            assert argv == ["opencode", "session", "export", _SID]
            return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr="")

        return run

    monkeypatch.setattr(subprocess, "run", fake(json.dumps({"info": _EXPORT_INFO})))
    assert REAL_EXPORT(_SID) == Usage(
        tokens_in=32898, tokens_out=1529 + 317, tokens_cache_read=117248, cost_usd=0.0
    )
    for stdout, rc in (("not json", 0), (json.dumps({"info": _EXPORT_INFO}), 1), ("{}", 0)):
        monkeypatch.setattr(subprocess, "run", fake(stdout, rc))
        assert REAL_EXPORT(_SID) is None
