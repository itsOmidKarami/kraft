"""What Kraft reads off one note of a bot thread: whose it is, and the markers and
hashes that tie a note Kraft posted to its record.

The collection (`settle._collected`), the reconcile (`send`) and, in the next item,
the posting gate all ask here, so triage and the gate can never disagree about who
wrote a note."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Literal

from kraft.adapters.forge.models import ForgeNote
from kraft.adapters.forge.mr import same_login

By = Literal["bot", "kraft", "person"]

_REPLY = re.compile(r"<!-- kraft-reply:(?P<intent>[0-9a-z]+-[0-9a-f]{12}) -->")
_SUMMARY = re.compile(r"<!-- kraft-summary:(?P<item>[0-9a-z]+) -->")


def reply_marker(intent: str) -> str:
    return f"<!-- kraft-reply:{intent} -->"


def summary_marker(work_item_id: str) -> str:
    return f"<!-- kraft-summary:{work_item_id} -->"


def _last_line(body: str) -> str:
    return body.rstrip("\n").rsplit("\n", 1)[-1].strip()


def marker_of(body: str) -> str | None:
    """The intent a reply's last line names, or None."""
    m = _REPLY.fullmatch(_last_line(body))
    return m["intent"] if m else None


def has_summary_marker(body: str, work_item_id: str) -> bool:
    m = _SUMMARY.fullmatch(_last_line(body))
    return bool(m) and m["item"] == work_item_id


def without_marker(body: str) -> str:
    """The body less a trailing marker line, either kind."""
    text = body.rstrip("\n")
    last = _last_line(text)
    if _REPLY.fullmatch(last) or _SUMMARY.fullmatch(last):
        text = text[: text.rfind("\n") + 1] if "\n" in text else ""
    return text.rstrip("\n")


def body_hash(body: str) -> str:
    """SHA-256 of the body with its marker line removed."""
    return hashlib.sha256(without_marker(body).encode()).hexdigest()


def is_bot_note(note: ForgeNote, bots: Iterable[str]) -> bool:
    """A configured bot wrote it. A GitHub app is a `Bot`; a person who registered
    the bare login is a `User`. GitLab reports no type, so the login decides."""
    return note.author_type in (None, "Bot") and any(same_login(note.author, b) for b in bots)


def is_kraft_note(
    note: ForgeNote, *, viewer: str | None, work_item_id: str, posted: Mapping[str, str]
) -> bool:
    """Kraft wrote it: the record holds it as posted and its body still hashes to
    what the forge returned (an edited one is a person's), or, for a note the record
    does not hold, the viewer wrote it and it carries a reply marker of this item."""
    recorded = posted.get(note.id) or posted.get(note.rest_id)
    if recorded is not None:
        return body_hash(note.body) == recorded
    intent = marker_of(note.body)
    return bool(
        viewer
        and intent
        and intent.startswith(f"{work_item_id}-")
        and same_login(note.author, viewer)
    )


def note_by(
    note: ForgeNote,
    *,
    bots: Iterable[str],
    viewer: str | None = None,
    work_item_id: str = "",
    posted: Mapping[str, str] | None = None,
) -> By:
    if is_kraft_note(note, viewer=viewer, work_item_id=work_item_id, posted=posted or {}):
        return "kraft"
    return "bot" if is_bot_note(note, bots) else "person"


@dataclass(frozen=True)
class Kraft:
    """What tells Kraft's notes from everyone else's on one work item: the login it
    writes as (None when unknown), the reply record's posted hashes and its threads. The
    collection, the head wait and the posting gate all ask it, so they never
    disagree."""

    viewer: str | None
    work_item_id: str
    posted: Mapping[str, str]
    #: The ids of the threads the record holds a reply in (`record.thread_ids`): Kraft's
    #: conversation whether or not a Kraft note still shows there, so a note a person
    #: deleted, or a thread Kraft first posted in between two checks, is still its own.
    threads: frozenset[str] = frozenset()

    def wrote(self, note: ForgeNote) -> bool:
        return is_kraft_note(
            note, viewer=self.viewer, work_item_id=self.work_item_id, posted=self.posted
        )

    def talks_in(self, notes: Iterable[ForgeNote]) -> bool:
        """Whether Kraft has a note among `notes`: the thread is its conversation."""
        return any(self.wrote(n) for n in notes)
