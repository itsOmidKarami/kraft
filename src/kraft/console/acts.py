"""What a key does to one item (spec §7.1, §7.2): the dialog it asks through,
the call it sends, and the CLI's sentence for the answer. The Board and the
Item both act through here, so the two can never word or guard one apart.

Each flow is awaited from a worker (`push_screen_wait` needs one) and takes
the row the key was pressed on, never the selection when the dialog closes."""

from __future__ import annotations

import time

from textual.app import App
from textual.binding import Binding

from kraft import answers, client
from kraft.console import moments, words
from kraft.console.dialogs import Dialog, Note, reject_asks
from kraft.vocab import ThreadLabel

#: The keys that act on an item, and the doors (`actions`) that open each: a
#: key is bound only while the server lists one (spec §6).
DOORS = {
    "approve": ("approve",),
    "reject": ("reject",),
    "pause_or_resume": ("pause", "resume"),
    "retry": ("retry",),
}
#: What a steer's dialog says: the retry and resume routes take one or none.
STEER = "A steer for the agent, or Enter for none."


async def approve_waits(work_item_id: str) -> str | None:
    """Why approve is closed at a pending gate, when open must-fix threads
    keep it so (spec §7.1, §7.2), in the sentence the peek and the Item say."""
    threads = await client.threads(work_item_id, open_only=True)
    must = sum(thread.get("label") == ThreadLabel.MUST_FIX for thread in threads)
    if not must:
        return None
    return f"Approve waits on {must} open must-fix thread{'s' if must != 1 else ''}."


def opens(action: str, row: dict | None, parameters: tuple[object, ...] = ()) -> bool:
    """Whether the server lists a door that `action`'s key goes through. `p`
    is bound once per word it can have (`P_KEYS`): only the binding for what
    it does now is open, so the footer names that."""
    if action == "pause_or_resume" and parameters:
        return p_word(row) == parameters[0]
    return bool(row) and any(door in (row.get("actions") or []) for door in DOORS[action])


def p_word(row: dict | None) -> str | None:
    """What `p` does for `row` now, as the footer says it: an agent's question
    takes an answer, a paused item resumes, a running one pauses."""
    doors = (row or {}).get("actions") or []
    if "resume" in doors:
        return "answer" if ((row or {}).get("stop") or {}).get("kind") == "question" else "resume"
    return "pause" if "pause" in doors else None


#: `p` on the Board and the Item, one binding per word: one action, so one key
#: still does one thing.
P_KEYS = [
    Binding("p", f"pause_or_resume('{word}')", word) for word in ("pause", "resume", "answer")
]


def way_on(item: dict, *, peek: bool = False) -> list[str]:
    """The keys that move a stopped or paused item on, a sentence each: the
    peek's and the Item's Overview's, from one place. When the server offers
    none, a line saying so, and where `m` shows what it does allow (`peek`:
    on the Board, `m` is on the Item)."""
    doors = item.get("actions") or []
    lines = {"answer": ["p answers it."], "resume": ["p resumes it."]}.get(p_word(item) or "", [])
    if "retry" in doors:
        lines.append("t retries it.")
    if "raise_budget" in doors:
        lines.append(f"/item raise-budget {item['id']} --usd N raises its budget.")
    if not lines and item.get("display_status") in ("paused", "failed", "needs_you"):
        lines.append(
            "Kraft offers no action for it now; "
            + ("Enter opens it; m there" if peek else "m")
            + " shows what it allows."
        )
    return lines


async def reject(app: App, row: dict, note_dialog: Dialog | None = None) -> None:
    """Reject the pending gate with a required note. `note_dialog` asks
    instead of the plain note (the Item's, which also picks the node)."""
    gate = row.get("pending_gate") or "the gate"
    named = words.gate_name(words.nodes_of(row), gate) if row.get("pending_gate") else gate
    asked = note_dialog or Note(
        f"Request changes: {named} · {row['id'][:8]}",
        [reject_asks((row.get("fix_target") or {}).get("node"))],
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
        decided=True,
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


async def act(app: App, call, said, *, decided: bool = False) -> bool:
    """Send one action and toast what the server answered, in the CLI's
    sentence (`kraft.answers`); a gate `decided` leads it with the mark
    (`moments.marked`). A refusal is toasted in the server's words and the
    board read again (spec §8), which every view hears as a reread. True
    when the server took it."""
    try:
        result = await call
    except client.ClientError as exc:
        app.notify_refusal(exc)
        app.live.reread()
        return False
    if decided:
        app.notify(moments.marked(said(result)))
    else:
        app.notify(said(result), markup=False)
    return True
