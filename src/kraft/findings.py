"""The shared finding schema (`03_plugin_adapters.md` §4) and its identity.

Every reader here is best-effort: a plugin that writes a malformed result file
loses its findings, never the session. That matches `read_summary_ref` in
`adapters/subprocess.py`, which swallows a broken file for the same reason.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, replace
from pathlib import Path

from kraft.render import strip_ansi

SEVERITIES: tuple[str, ...] = ("critical", "important", "minor")

_WS = re.compile(r"\s+")

#: Bytes read from the tail of a failed task's log -- enough to reach a
#: failure summary without loading a possibly large log in full.
_TAIL_BYTES = 20_000

#: Cap on the extracted failure text, before the retrieval line is appended.
#: Keeps a synthesized Finding's message the same order of size as a real
#: review finding's (~200-600 chars observed), not a log dump.
_MESSAGE_CAP = 400

#: Lines worth keeping from a failed task's output.
_MARKER = re.compile(r"✘|FAILED|Error:|Traceback")

#: Wall-clock noise that must not affect a finding's fingerprint: an
#: identical failure at a different duration or timestamp is still the same
#: failure. `# ponytail: heuristic, not framework-aware; upgrade to
#: structured test-name diffing if a real framework ever needs it.`
_NOISE = re.compile(r"\(\d+(?:\.\d+)?\s*(?:ms|s|m)\)|\b\d{2}:\d{2}:\d{2}\b")

#: What a `same_as` must look like to be believed: the shape `fingerprint`
#: itself produces. A model asked to echo a tag will sometimes write a sentence
#: instead, and a sentence accepted as an identity is worse than no identity.
_TAG = re.compile(r"^[0-9a-f]{16}$")


@dataclass(frozen=True)
class Finding:
    severity: str
    message: str
    file: str | None
    line: int | None
    source_plugin: str
    #: The tag of a finding from an earlier round that this one restates, as the
    #: reviewer that was shown both says so. Optional and last, so every
    #: positional construction keeps working.
    same_as: str | None = None

    @property
    def fingerprint(self) -> str:
        """Stable identity across fix cycles.

        `line` is excluded deliberately: the fix task edits the file, so every
        line below the edit shifts, and including it would make every finding
        look new after any fix — exactly the blindness this exists to remove.
        Severity is excluded too: the same defect re-reported at a different
        severity is the same defect.

        Those two exclusions are right and stay. What the original design did
        not anticipate is **rewording** — `message` is LLM prose, so the same
        defect restated in new words hashed differently, `stuck_fingerprint`'s
        streak never exceeded 1, and the stuck detector could never fire
        (Kraft-s7c04.2: one reattach.py defect reported in five consecutive
        measurements under five distinct fingerprints, then shipped).

        No hash can see that two sentences describe one defect. The reviewer
        that read both can, so it is asked, and `same_as` is its answer —
        honoured over the hash. It is only ever believed for a tag the reviewer
        was actually shown; see `resolve_identity`.
        """
        if self.same_as:
            return self.same_as
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
    same_as = raw.get("same_as")
    return Finding(
        severity=severity,
        message=message,
        file=file if isinstance(file, str) and file else None,
        line=line if isinstance(line, int) and not isinstance(line, bool) else None,
        source_plugin=source_plugin,
        same_as=same_as if isinstance(same_as, str) and _TAG.match(same_as) else None,
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
        # No shape check here, unlike `_one`: a payload was written by `asdict`
        # over a Finding that already passed one.
        same_as=raw.get("same_as"),
    )


def resolve_identity(found: list[Finding], known: frozenset[str] | set[str]) -> list[Finding]:
    """`found` with every `same_as` that names a tag nobody was shown stripped.

    A reviewer claims identity with a previous finding by echoing its tag
    (Kraft-s7c04.2). Trusting that unchecked lets one hallucinated or stale tag
    collapse two distinct defects onto a single identity, or resurrect one from
    an episode that is over -- and the stuck detector would then fire on a
    fiction, stopping a loop that is converging.

    `known` is the set of tags this round's reviewer was actually handed, which
    is exactly what `prompts.carried_findings_note` listed for it. A finding
    whose claim fails falls back to its own prose hash, which is what it would
    have had before it claimed anything.
    """
    return [f if not f.same_as or f.same_as in known else replace(f, same_as=None) for f in found]


def _tail_text(log_path: str | Path) -> str | None:
    """The last `_TAIL_BYTES` of a log file, or None if it can't be read.
    Best-effort, matching this module's own stated philosophy."""
    try:
        with open(log_path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - _TAIL_BYTES))
            data = f.read()
    except OSError:
        return None
    return data.decode("utf-8", errors="replace")


def _extract_message(text: str) -> str:
    text = _NOISE.sub("", strip_ansi(text))
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    marked = [ln for ln in lines if _MARKER.search(ln)]
    chosen = marked[:5] if marked else lines[-5:]
    return "\n".join(chosen)[:_MESSAGE_CAP]


def from_blind_failure(
    hook: str,
    log_path: str | Path | None,
    work_item_id: str,
    session_id: str,
    reproduce: str | None = None,
) -> Finding:
    """A `Finding` for a task that failed without writing its own findings
    file -- most commonly a `kind: subprocess` measuring task like
    `on.test.run`. Without this, the task's failure never reaches the fix
    prompt's content, the judge, or the fingerprint-based stuck-detector; it
    is only ever named, never shown (traced live on a work item that spun
    for 7 cycles on the identical test failure because of exactly that gap).

    `reproduce`, when given, must be a fixed string that does not vary
    between rounds (e.g. the hook's own registry-bound command) -- it goes
    into `message`, which `Finding.fingerprint` hashes whole, so anything
    round-specific here (a session id, a log path) would fingerprint an
    unchanged failure differently every cycle and defeat the point of this
    function.
    """
    if reproduce:
        pointer = f"Reproduce with: {reproduce}"
    else:
        pointer = f"Full log: kraft view logs {work_item_id} --session {session_id}"
    text = _tail_text(log_path) if log_path else None
    if not text or not text.strip():
        message = f"{hook} failed; no output captured.\n\n{pointer}"
    else:
        message = f"{_extract_message(text)}\n\n{pointer}"
    return Finding(severity="critical", message=message, file=None, line=None, source_plugin=hook)
