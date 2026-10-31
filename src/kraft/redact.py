"""Forge tokens must never reach text Kraft writes (logs, events, errors)."""

from __future__ import annotations

import json
import logging
import threading

PLACEHOLDER = "<redacted>"
MIN_LEN = 8  # shorter secrets would eat ordinary words

# Longest first, so a secret that contains another is replaced whole. A tuple
# swapped whole on register, so a reader on another thread never sees it change.
_secrets: tuple[str, ...] = ()
_lock = threading.Lock()  # two registers must not drop each other's secret


def register(secret: str) -> None:
    global _secrets
    if len(secret) < MIN_LEN:
        raise ValueError(f"a secret must be at least {MIN_LEN} characters")
    # Its JSON-escaped form too: an event payload is redacted after json.dumps.
    forms = {secret, json.dumps(secret)[1:-1]}
    with _lock:
        _secrets = tuple(sorted({*_secrets, *forms}, key=len, reverse=True))


def redact(text: str) -> str:
    for secret in _secrets:
        text = text.replace(secret, PLACEHOLDER)
    return text


_make_record = logging.getLogRecordFactory()


def _arg(value):
    """`value` as is, unless its text holds a secret: then that text, redacted."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, int | float | type(None)):
        return value
    try:
        text = str(value)
    except Exception:  # noqa: BLE001 -- logging must never raise into its caller
        return value
    shown = redact(text)
    return value if shown == text else shown


def _redacted_record(*args, **kwargs) -> logging.LogRecord:
    """Every log record Kraft or a library makes, whatever handler writes it
    (Rich, uvicorn's, lastResort): its message and traceback, redacted."""
    record = _make_record(*args, **kwargs)
    if _secrets:
        # Args keep their shape: uvicorn's access formatter unpacks them.
        record.msg = _arg(record.msg)
        if isinstance(record.args, dict):
            record.args = {k: _arg(v) for k, v in record.args.items()}
        elif record.args:
            record.args = tuple(_arg(a) for a in record.args)
        if record.exc_info:
            # Dropped once rendered: Rich draws a traceback from exc_info itself.
            exc_info, record.exc_info = record.exc_info, None
            record.exc_text = redact(logging.Formatter().formatException(exc_info))
        if record.stack_info:
            record.stack_info = redact(record.stack_info)
    return record


logging.setLogRecordFactory(_redacted_record)
