"""What a key does to one item (spec §7.1, §7.2): the dialog it asks through,
the call it sends, and the CLI's sentence for the answer. The Board and the
Item both act through here, so the two can never word or guard one apart.

Each flow is awaited from a worker (`push_screen_wait` needs one) and takes
the row the key was pressed on, never the selection when the dialog closes."""

from __future__ import annotations

import time

from textual.app import App

from kraft import answers, client
from kraft.console import words
from kraft.console.dialogs import Note

#: The keys that act on an item, and the doors (`actions`) that open each: a
#: key is bound only while the server lists one (spec §6).
DOORS = {
    "reject": ("reject",),
    "pause_or_resume": ("pause", "resume"),
    "retry": ("retry",),
}
#: What a steer's dialog says: the retry and resume routes take one or none.
STEER = "A steer for the agent, or Enter for none."


def opens(action: str, row: dict | None) -> bool:
    """Whether the server lists a door that `action`'s key goes through."""
    return bool(row) and any(door in (row.get("actions") or []) for door in DOORS[action])


async def reject(app: App, row: dict, note_dialog: Note | None = None) -> None:
    """Reject the pending gate with a required note. `note_dialog` asks
    instead of the plain note (the Item's, which also picks the node)."""
    gate = row.get("pending_gate") or "the gate"
    asked = note_dialog or Note(
        f"Reject {gate} on {row['id'][:8]}",
        ["Say what is wrong: the note goes to the agent that redoes it."],
        required=True,
    )
    answer = await app.push_screen_wait(asked)
    if not answer:
        return
    note, node = answer if isinstance(answer, tuple) else (answer, None)
    await act(
        app,
        client.reject_gate(
            note, row.get("pending_gate"), row["id"], **({"node": node} if node else {})
        ),
        answers.item_action(f"rejected {gate} on {{id}}"),
    )


async def pause_or_resume(app: App, row: dict) -> None:
    if "resume" in (row.get("actions") or []):
        await resume(app, row)
    else:
        await act(
            app, client.pause(row["id"]), answers.item_action("paused {id}", small=answers.paused)
        )


async def resume(app: App, row: dict) -> None:
    """An agent's question takes an answer; a paused item a steer, only where
    the server offers one (a steer it refuses is a 409)."""
    asked = None
    if (row.get("stop") or {}).get("kind") == "question":
        asked = Note("Answer the agent", [words.reason_tail(row, time.time())], required=True)
    elif "steer" in (row.get("actions") or []):
        may = await may_steer(app, row)
        if may is None:  # the read failed and was said: send nothing
            return
        if may:
            asked = Note(f"Resume {row['id'][:8]}", [STEER], required=False)
    steer = await app.push_screen_wait(asked) if asked else ""
    if steer is not None:
        said = answers.item_action("resumed {id}", small=answers.resumed)
        await act(app, client.resume(steer or None, row["id"]), said)


async def retry(app: App, row: dict) -> None:
    steer = ""
    may = await may_steer(app, row)
    if may is None:  # the read failed and was said: send nothing
        return
    if may:
        steer = await app.push_screen_wait(Note(f"Retry {row['id'][:8]}", [STEER], required=False))
    if steer is not None:
        said = answers.item_action("retried {id}", small=answers.retried)
        await act(app, client.retry(steer or None, row["id"]), said)


async def may_steer(app: App, row: dict) -> bool | None:
    """Whether the server would place a steer for this item. It answers a
    steer with nowhere to go with a 409, and says so on the detail's
    `steerable`: where it does not, no dialog is offered. None when the
    detail could not be read (said in a toast): nothing is sent then."""
    try:
        item = await client.get_work_item(row["id"], full=True)
    except client.ClientError as exc:
        app.notify_refusal(exc)
        return None
    return item.get("steerable") is not False


async def act(app: App, call, said) -> bool:
    """Send one action and toast what the server answered, in the CLI's
    sentence (`kraft.answers`). A refusal is toasted in the server's words
    and the board read again (spec §8), which every view hears as a reread.
    True when the server took it."""
    try:
        result = await call
    except client.ClientError as exc:
        app.notify_refusal(exc)
        app.live.reread()
        return False
    app.notify(said(result), markup=False)
    return True
