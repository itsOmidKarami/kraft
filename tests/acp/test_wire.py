import asyncio

import pytest

from kraft.acp import wire


async def _read(data: bytes, limit: int = wire.LINE_LIMIT):
    stream = asyncio.StreamReader(limit=limit)
    stream.feed_data(data)
    stream.feed_eof()
    out = []
    while True:
        kind, _value = await wire.read_line(stream)
        out.append(kind)
        if kind == "eof":
            return out


@pytest.mark.parametrize(
    ("data", "kinds"),
    [
        (b'{"jsonrpc":"2.0","id":1,"result":{}}\n', ["json", "eof"]),
        (b"Usage: opencode acp [options]\n", ["stray", "eof"]),
        (b'{"not":"jsonrpc"}\n', ["stray", "eof"]),
        (b"[1,2]\n", ["stray", "eof"]),
        (b"", ["eof"]),
    ],
    ids=["request", "help-text", "other-json", "array", "empty"],
)
def test_read_line_never_raises_on_what_an_agent_prints(data, kinds):
    assert asyncio.run(_read(data)) == kinds


def test_a_line_past_the_limit_is_skipped_not_read_whole():
    big = b'{"jsonrpc":"2.0","x":"' + b"a" * 200 + b'"}\n'
    kinds = asyncio.run(_read(big + b'{"jsonrpc":"2.0","id":2}\n', limit=64))
    assert kinds[0] == "long" and "json" in kinds


def test_initialize_advertises_no_fs_and_no_terminal():
    assert wire.initialize_params("1")["clientCapabilities"] == {}


def test_system_prompt_is_an_object_never_a_string():
    assert wire.system_prompt("hi") == {
        "mode": "append",
        "content": [{"type": "text", "text": "hi"}],
    }


@pytest.mark.parametrize(
    ("options", "picked"),
    [
        ([{"optionId": "a", "kind": "allow_always"}, {"optionId": "b", "kind": "allow_once"}], "b"),
        (
            [{"optionId": "a", "kind": "allow_always"}, {"optionId": "r", "kind": "reject_once"}],
            None,
        ),
        ([], None),
    ],
    ids=["skips-always", "no-allow-once", "none"],
)
def test_select_option_takes_only_allow_once_by_kind(options, picked):
    assert wire.select_option(options) == picked
