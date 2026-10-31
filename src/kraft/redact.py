"""Forge tokens must never reach text Kraft writes (logs, events, errors)."""

from __future__ import annotations

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
    with _lock:
        _secrets = tuple(sorted({*_secrets, secret}, key=len, reverse=True))


def redact(text: str) -> str:
    for secret in _secrets:
        text = text.replace(secret, PLACEHOLDER)
    return text
