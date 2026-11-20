"""The one gate agent text passes before Kraft posts it: what could render, run a
forge command, link out or leak is refused (`check`), a quote must be verbatim and
the prose is capped, then Kraft builds the note round the text it let through
(`render`). Pure apart from `RepoQuotes`, which asks git whether a quote is in the
tracked files at a commit.

A reader of the bots' threads writes this text, so it is free text that read
untrusted input; the gate narrows what can leave rather than trusting it."""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol

from kraft import redact
from kraft.adapters.forge import git
from kraft.adapters.forge import mr as mr_ops
from kraft.adapters.forge.models import ForgeError
from kraft.worker.env import BASELINE, worker_env

if TYPE_CHECKING:
    from kraft.config import RepoEntry

#: Goes before a character the forge would read as a command, mention or reference: it
#: renders as nothing and breaks the sequence.
WORD_JOINER = "\u2060"
PROSE_MAX = 1_500  # bytes of agent text that is not a verbatim quote
#: A quote of any form (span, fence, indented block, block quote) shorter than this is checked
#: verbatim but counts as prose: a one-character quote is in every file or note, so a run of
#: them would carry any text past `PROSE_MAX`.
QUOTE_MIN = 20
NOTE_MAX = 60_000  # bytes of the whole note, footer and marker included

_INVISIBLE = {"Cc", "Cf", "Co", "Cs", "Zl", "Zp"}
_IMAGE = re.compile(r"!\[|<img\b", re.I)
_MARKER = re.compile(r"<!--|kraft-(?:reply|summary):", re.I)
#: URL text: refused everywhere, code included (the aim: no URL leaves).
_URL = re.compile(
    r"[A-Za-z][A-Za-z0-9+.-]*://|\b(?:mailto|xmpp):\S|\bwww\.|(?<![\w:/])//[A-Za-z0-9-]+\.",
    re.I,
)
#: Markdown link syntax: renders only outside code, where `a[i][j]` and
#: `Vec<std::io::Error>` are ordinary.
_LINK_SYNTAX = re.compile(
    r"\]\(|\]\[|^ {0,3}\[[^\]]+\]:|<[A-Za-z][A-Za-z0-9+.-]{1,31}:[^\s<>]*>", re.M
)
_ESCAPED_TICK = re.compile(r"\\`")
_HTML = re.compile(r"<[A-Za-z/!?]")
_ENCODED = re.compile(r"&#\d+;|&#x[0-9A-Fa-f]+;|&[A-Za-z][A-Za-z0-9]*;|\\[!-/:-@\[-`{-~]")
_TOKEN = re.compile(
    r"\b(?:gh[posu]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|glpat-[A-Za-z0-9_-]{20,}"
    r"|sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16})\b|-----BEGIN [A-Z ]*-----"
)
_FENCE_OPEN = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_INDENTED = re.compile(r"^(?: {4}|\t)")
_QUOTE_LINE = re.compile(r"^ {0,3}>")


@dataclass(frozen=True)
class Refused:
    why: str  # a short phrase: "a link", "a quote not found verbatim", ...


class Quotes(Protocol):
    #: The bodies of the thread's notes.
    notes: Sequence[str]

    async def in_a_line(self, text: str) -> bool: ...
    async def in_whole_lines(self, lines: Sequence[str]) -> bool: ...


#: A file's worth of candidates `RepoQuotes` reads for a block, at most.
_MAX_FILES = 200


class RepoQuotes:
    """`Quotes` over the tracked files at `head` (`git grep -F`) and `notes`."""

    def __init__(self, repo: Path, head: str, notes: Sequence[str] = ()):
        self.repo, self.head, self.notes = repo, head, tuple(notes)

    async def in_a_line(self, text: str) -> bool:
        if not text:
            return True
        # `-F`: a quote is never a pattern.
        done = await git.run_cli(
            self.repo, ["git", "grep", "-F", "-q", "-e", text, self.head, "--"], errors="replace"
        )
        if done.returncode not in (0, 1):
            raise ForgeError(f"git grep failed ({done.returncode}): {done.stderr.strip()[:200]}")
        return done.returncode == 0

    async def in_whole_lines(self, lines: Sequence[str]) -> bool:
        anchor = max((ln for ln in lines if ln.strip()), key=len, default=None)
        if anchor is None:
            return True
        # Files, not hits: a common anchor has hundreds of hits and the real place
        # need not be among the first few. Not `-x`: a CRLF file's line ends in `\r`.
        done = await git.run_cli(
            self.repo,
            ["git", "grep", "-l", "-z", "-F", "-e", anchor, self.head, "--"],
            errors="replace",
        )
        if done.returncode not in (0, 1):
            raise ForgeError(f"git grep failed ({done.returncode}): {done.stderr.strip()[:200]}")
        prefix = f"{self.head}:"
        paths = [p.removeprefix(prefix) for p in done.stdout.split("\0") if p][:_MAX_FILES]
        want = list(lines)
        for path in paths:
            shown = await git.run_cli(
                self.repo, ["git", "show", f"{self.head}:{path}"], errors="replace"
            )
            if shown.returncode != 0:
                continue
            have = shown.stdout.splitlines()  # so a CRLF file compares equal
            if any(have[i : i + len(want)] == want for i in range(len(have) - len(want) + 1)):
                return True
        return False


@dataclass(frozen=True)
class _Piece:
    text: str
    code: bool
    quote: Literal["span", "block"] | None = None
    #: A prose piece that starts at the beginning of a line (`^` anchors read it).
    bol: bool = True


def _spans(line: str) -> list[_Piece]:
    """One prose line as prose and inline code spans. A run of N backticks closed by a
    run of exactly N later on the line is a span; one the line does not close is prose,
    which is checked as prose (misreading code as prose can only refuse more)."""
    out: list[_Piece] = []
    pos = start = 0
    while pos < len(line):
        if line[pos] != "`":
            pos += 1
            continue
        end = pos
        while end < len(line) and line[end] == "`":
            end += 1
        n = end - pos
        close = None
        scan = end
        while scan < len(line):
            if line[scan] != "`":
                scan += 1
                continue
            run_end = scan
            while run_end < len(line) and line[run_end] == "`":
                run_end += 1
            if run_end - scan == n:
                close = scan
                break
            scan = run_end
        if close is None:
            pos = end
            continue
        inner = line[end:close]
        if len(inner) > 1 and inner[0] == inner[-1] == " " and inner.strip():
            inner = inner[1:-1]
        if line[start:pos]:
            out.append(_Piece(line[start:pos], False, bol=start == 0))
        out.append(_Piece(inner, True, "span"))
        pos = start = close + n
    if start < len(line) or not out:
        out.append(_Piece(line[start:], False, bol=start == 0))
    return out


def _pieces(text: str) -> list[_Piece]:
    """The text as prose, code (fenced, indented, inline) and block quotes."""
    lines = text.split("\n")
    out: list[_Piece] = []
    i, blank = 0, True
    while i < len(lines):
        line = lines[i]
        opened = _FENCE_OPEN.match(line)
        # A backtick fence whose info string holds a backtick is not a fence (CommonMark).
        if opened and not (opened[1][0] == "`" and "`" in opened[2]):
            mark = opened[1]
            close = re.compile(rf"^ {{0,3}}{re.escape(mark[0])}{{{len(mark)},}}\s*$")
            j = i + 1
            while j < len(lines) and not close.match(lines[j]):
                j += 1
            out.append(_Piece("\n".join(lines[i + 1 : j]), True, "block"))
            out.append(_Piece("\n".join(lines[i : i + 1] + lines[j : j + 1]), False))
            i, blank = j + 1, True
        elif blank and _INDENTED.match(line) and line.strip():
            j = i
            while j < len(lines) and _INDENTED.match(lines[j]) and lines[j].strip():
                j += 1
            body = [re.sub(r"^(?: {4}|\t)", "", ln) for ln in lines[i:j]]
            out.append(_Piece("\n".join(body), True, "block"))
            i, blank = j, False
        elif _QUOTE_LINE.match(line):
            j = i
            while j < len(lines) and _QUOTE_LINE.match(lines[j]):
                j += 1
            body = [re.sub(r"^ {0,3}> ?", "", ln) for ln in lines[i:j]]
            out.append(_Piece("\n".join(body), False, "block"))
            i, blank = j, False
        else:
            out += _spans(line)
            blank = not line.strip()
            i += 1
    return out


_FLOOR = redact.MIN_LEN  # 8: the floor log redaction already uses


def _squeeze(s: str) -> str:
    return re.sub(r"[\W_]+", "", s)


def _credential_like(s: str) -> bool:
    # A letter and a digit, or long: a key. `production-eu` is configuration, and
    # squeezed it would match "the production eu stack" in ordinary prose.
    return len(s) >= 16 or (bool(re.search(r"[A-Za-z]", s)) and bool(re.search(r"\d", s)))


def _holds_secret(text: str, secrets: Collection[str]) -> bool:
    flat = _squeeze(text)
    for s in secrets:
        if len(s) < _FLOOR:
            continue  # the one floor: "", "k", "dummy" never refuse anything
        if s in text or s[::-1] in text:
            return True
        sq = _squeeze(s)
        if _credential_like(s) and len(sq) >= _FLOOR and (sq in flat or sq[::-1] in flat):
            return True
    return False


def _outside(pieces: Iterable[_Piece], rx: re.Pattern[str]) -> bool:
    for p in pieces:
        if p.code:
            continue
        # A piece that does not start a line must not satisfy a `^` anchor.
        if rx.search(p.text if p.bol else f"\x01{p.text}"):
            return True
    return False


async def check(text: str, *, quotes: Quotes, secrets: Collection[str]) -> Refused | None:
    """The first reason agent `text` may not be posted, or None. `quotes` says whether a
    quote is verbatim; `secrets` are the values the text must not hold (`known_secrets`)."""
    # A lone `\r` hides a line from `re.M`, and `render` turns it into a `\n` the forge sees.
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if any(c not in "\t\n" and unicodedata.category(c) in _INVISIBLE for c in text):
        return Refused("an invisible or control character")
    if _IMAGE.search(text):
        return Refused("an image")
    if _MARKER.search(text):
        return Refused("a marker or HTML comment")
    if _URL.search(text):
        return Refused("a link")
    if _ESCAPED_TICK.search(text):
        # An escaped backtick opens no span on the forge, though `_pieces` would see one.
        return Refused("an encoded character")
    if _TOKEN.search(text):
        return Refused("a token")
    if _holds_secret(text, secrets):
        return Refused("a secret Kraft knows")
    pieces = _pieces(text)
    if _outside(pieces, _LINK_SYNTAX):
        return Refused("a link")
    if _outside(pieces, _HTML):
        return Refused("raw HTML")
    if _outside(pieces, _ENCODED):
        return Refused("an encoded character")
    verbatim = 0
    for p in pieces:
        if p.quote is None:
            continue
        in_note = any(p.text in n for n in quotes.notes)
        if p.quote == "span":
            found = in_note or await quotes.in_a_line(p.text)
        else:
            found = in_note or await quotes.in_whole_lines(p.text.split("\n"))
        if not found:
            return Refused("a quote not found verbatim")
        size = len(p.text.encode())
        if size >= QUOTE_MIN:
            verbatim += size
    if len(text.encode()) - verbatim > PROSE_MAX:
        return Refused("more than 1,500 bytes of prose")
    return None


#: Secrets whatever their shape once 16 characters long; a placeholder below 8 never.
_NAMED = ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN")
_PLAIN = re.compile(r"[a-z]+|\d+(?:\.\d+)?|(?i:true|false|yes|no)")


def _counts(value: str, *, named: bool) -> bool:
    value = value.strip()
    if len(value) < _FLOOR:
        return False  # placeholder: "k", "x", "dummy"
    if named and len(value) >= 16:
        return True  # a real key; skip the plain-word test
    return not _PLAIN.fullmatch(value)  # "placeholder", "12345678", "true"


def known_secrets(repo_entry: RepoEntry | None, *, extra: Iterable[str] = ()) -> frozenset[str]:
    """The values a reply must not hold: the worker environment `worker_env` builds for
    the repository (less the baseline names and Kraft's session names), every value
    `kraft.redact` has registered, and `extra` (the forge token, when the caller has
    one). A value under 8 characters never counts.

    A step on a remote worker builds its environment on that machine
    (`remote/jobs.py:142`): its `env_passthrough` values are not known here, so only
    the named credentials, registered tokens and literal `env:` values are matched for
    it. Residual risk, as the design says."""
    env = worker_env(repo_entry)
    named = [env.get(k, "") for k in _NAMED] + list(extra)
    rest = [
        v
        for k, v in env.items()
        if k not in BASELINE and not k.startswith("KRAFT_") and k not in _NAMED
    ] + list(redact.known())
    return frozenset(
        [v for v in named if _counts(v, named=True)] + [v for v in rest if _counts(v, named=False)]
    )


_SLASH_LINE = re.compile(r"^([ \t]*)/", re.M)
_RUN = re.compile(r"[A-Za-z0-9+/=_-]{20,}")
_SIGIL = re.compile(r"([@#!&%$~])(?=[A-Za-z0-9'\"\[])|(@)(?=[_.])")  # GitLab paths may start _ or .
_ITERATION = re.compile(r"\*(?=iteration:)")
_VULN = re.compile(r"\[(?=vulnerability:)")
_KEY = re.compile(r"\b([A-Z][A-Z0-9_]+-)(?=\d)")
_CUT = "\n\n_(cut to fit)_"


def _entropy(s: str) -> float:
    n = len(s)
    return -sum(c / n * math.log2(c / n) for c in Counter(s).values())


def _random_looking(run: str) -> bool:
    # A sha, a word, an identifier or a path is not a secret; a token mixes all three
    # classes, and base64's `/` sits inside a long segment.
    if "/" in run and all(len(seg) < 20 for seg in run.split("/")):
        return False
    return bool(
        not re.fullmatch(r"[0-9a-fA-F]+", run)
        and re.search(r"[a-z]", run)
        and re.search(r"[A-Z]", run)
        and re.search(r"\d", run)
        and _entropy(run) >= 4.0
    )


def _open_fence(text: str) -> str | None:
    """The opening fence a cut text leaves unclosed, or None."""
    mark: str | None = None
    for line in text.split("\n"):
        if mark is None:
            m = _FENCE_OPEN.match(line)
            if m and not (m[1][0] == "`" and "`" in m[2]):
                mark = m[1]
        elif re.match(rf"^ {{0,3}}{re.escape(mark[0])}{{{len(mark)},}}\s*$", line):
            mark = None
    return mark


def _fit(body: str, room: int) -> str:
    """`body` cut to `room` bytes at a character boundary, closing a code fence the cut
    leaves open, and marked as cut."""
    if len(body.encode()) <= room:
        return body
    cut = room - len(_CUT)
    while True:
        head = body.encode()[: max(cut, 0)].decode(errors="ignore")
        fence = _open_fence(head)
        out = head + (f"\n{fence}" if fence else "") + _CUT
        excess = len(out.encode()) - room
        if excess <= 0 or cut <= 0:
            return out
        cut -= excess


def render(text: str, *, lead: str, work_item_id: str, marker: str) -> str:
    """The note Kraft posts for agent `text` that passed `check`: its line breaks
    normalised, every `/` line and every mention or reference sigil broken with a word
    joiner (so no command, mention or cross-reference fires, inside code too), long
    random-looking strings redacted, Kraft's `lead` first, the whole cut to
    `NOTE_MAX` bytes, then the footer and the hidden `marker`.

    Redaction runs before the sigils: a joiner inside a token would split the run and
    hide it from the entropy test."""
    body = text.replace("\r\n", "\n").replace("\r", "\n")
    body = _SLASH_LINE.sub(lambda m: f"{m[1]}{WORD_JOINER}/", body)
    body = _RUN.sub(lambda m: "[redacted]" if _random_looking(m[0]) else m[0], body)
    body = _SIGIL.sub(lambda m: (m[1] or m[2]) + WORD_JOINER, body)
    for rx in (_ITERATION, _VULN):
        body = rx.sub(lambda m: m[0] + WORD_JOINER, body)
    body = _KEY.sub(lambda m: m[1] + WORD_JOINER, body)
    head = f"{lead}\n\n"
    footer = (
        f"\n\n---\n_Posted by [Kraft]({mr_ops.KRAFT_URL}) for work item `{work_item_id}`._"
        f"\n\n{marker}"
    )
    return head + _fit(body, NOTE_MAX - len(head.encode()) - len(footer.encode())) + footer
