"""Review-bot threads for tests, built once from the same notes as the
collection reads them (`ReviewerFeedback.threads`) and as the posting gate reads
them (`ForgeThread`), so the two never disagree.

    raw, thread = bot_thread("T1", note("N1", "a", "rename x"))
    fake = FakeForge(feedback_script=[[ReviewerFeedback(bot, at, threads=(raw,))]],
                     threads={"T1": thread})
"""

from kraft.adapters.forge.models import ForgeNote, ForgeThread
from kraft.adapters.forge.replies.facts import reply_marker

VIEWER = "kraft-user"  # FakeForge's default viewer_login


def note(nid, author, body, *, at="2026-01-01T00:00:10Z", type_="Bot") -> ForgeNote:
    return ForgeNote(nid, f"9{nid}", author, type_, body, at, at)


def kraft_note(
    nid, work_item_id, *, body="Declined:\n\nno", at="2026-01-01T00:00:20Z"
) -> ForgeNote:
    """A note the viewer wrote, marked as one of `work_item_id`'s replies."""
    marked = f"{body}\n\n{reply_marker(f'{work_item_id}-0123456789ab')}"
    return ForgeNote(nid, f"9{nid}", VIEWER, "User", marked, at, at)


def bot_thread(tid, *notes, file="calc.py", line=1) -> tuple[dict, ForgeThread]:
    raw = {"id": tid, "file": file, "line": line, "body": notes[0].body, "notes": notes}
    return raw, ForgeThread(tid, 1, False, False, True, True, file, line, notes)
