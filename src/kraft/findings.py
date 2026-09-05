"""The shared finding schema (`03_plugin_adapters.md` §4) and its identity.

Every reader here is best-effort: a plugin that writes a malformed result file
loses its findings, never the session. That matches `read_summary_ref` in
`adapters/subprocess.py`, which swallows a broken file for the same reason.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

SEVERITIES: tuple[str, ...] = ("critical", "important", "minor")

_WS = re.compile(r"\s+")


@dataclass(frozen=True)
class Finding:
    severity: str
    message: str
    file: str | None
    line: int | None
    source_plugin: str

    @property
    def fingerprint(self) -> str:
        """Stable identity across fix cycles.

        `line` is excluded deliberately: the fix task edits the file, so every
        line below the edit shifts, and including it would make every finding
        look new after any fix — exactly the blindness this exists to remove.
        Severity is excluded too: the same defect re-reported at a different
        severity is the same defect.
        """
        norm = _WS.sub(" ", self.message).strip().lower()
        raw = "\0".join((self.source_plugin, self.file or "", norm))
        return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _one(raw: object) -> Finding | None:
    if not isinstance(raw, dict):
        return None
    severity = raw.get("severity")
    message = raw.get("message")
    source_plugin = raw.get("source_plugin")
    if severity not in SEVERITIES:
        return None
    if not isinstance(message, str) or not message:
        return None
    if not isinstance(source_plugin, str) or not source_plugin:
        return None
    file = raw.get("file")
    line = raw.get("line")
    return Finding(
        severity=severity,
        message=message,
        file=file if isinstance(file, str) and file else None,
        line=line if isinstance(line, int) and not isinstance(line, bool) else None,
        source_plugin=source_plugin,
    )


def parse(result_path: str | Path) -> list[Finding]:
    """Findings from a result file. Never raises; a bad file yields none."""
    try:
        data = json.loads(Path(result_path).read_text())
    except OSError, json.JSONDecodeError, UnicodeDecodeError:
        return []
    if not isinstance(data, dict):
        return []
    raw = data.get("findings")
    if not isinstance(raw, list):
        return []
    return [f for f in (_one(r) for r in raw) if f is not None]


def from_payload(raw: dict) -> Finding:
    """Rebuild a Finding from a `findings_measured` event payload.

    Key-by-key rather than `Finding(**raw)`: a payload written by a later schema
    with an extra key must not raise in a read path.
    """
    return Finding(
        severity=raw.get("severity", ""),
        message=raw.get("message", ""),
        file=raw.get("file"),
        line=raw.get("line"),
        source_plugin=raw.get("source_plugin", ""),
    )
