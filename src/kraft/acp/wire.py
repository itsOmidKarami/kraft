"""ACP over a child's pipes: bounded line reading and the JSON-RPC 2.0 envelope (spec §4, §8)."""

from __future__ import annotations

import asyncio
import json

# One JSON-RPC line may be a large tool result; a longer line is skipped and logged, never
# read whole. The number lives in `spec` (which imports nothing of Kraft); re-exported here.
from kraft.acp.spec import LINE_LIMIT as LINE_LIMIT

PROTOCOL_VERSION = 1
METHOD_NOT_FOUND = -32601
INVALID_REQUEST = -32600


def encode(msg: dict) -> bytes:
    return (json.dumps(msg, separators=(",", ":")) + "\n").encode()


async def read_line(stream: asyncio.StreamReader) -> tuple[str, object]:
    """The next line from the agent: `("eof", None)`, `("json", dict)` (a JSON-RPC 2.0
    object), `("stray", str)` (anything else) or `("long", None)` (past the stream's
    limit)."""
    try:
        raw = await stream.readline()
    except ValueError:  # a line past the stream's `limit`; its tail arrives as the next "line"
        return "long", None
    if not raw:
        return "eof", None
    text = raw.decode("utf-8", "replace").rstrip("\r\n")
    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, RecursionError):
        return "stray", text
    if isinstance(obj, dict) and obj.get("jsonrpc") == "2.0":
        return "json", obj
    return "stray", text


def initialize_params(version: str) -> dict:
    # No `fs`, no `terminal`: an agent must not read or write host files through a process
    # that sits outside its sandbox (spec §8, "Client safety").
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "clientCapabilities": {},
        "clientInfo": {"name": "kraft", "title": "Kraft", "version": version},
    }


def prompt_blocks(text: str) -> list[dict]:
    return [{"type": "text", "text": text}]


def system_prompt(text: str) -> dict:
    return {"mode": "append", "content": [{"type": "text", "text": text}]}


def result(id_, value: dict) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "result": value}


def error(id_, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


def select_option(options: list, kinds: tuple[str, ...] = ("allow_once",)) -> str | None:
    """The `optionId` of the first option whose `kind` is in `kinds`: the client never
    picks by a literal id, and never an `*_always`."""
    for option in options:
        if isinstance(option, dict) and option.get("kind") in kinds:
            return option.get("optionId")
    return None
