"""The Item's `m` menu (spec §6): the doors the header's menu offers on the
web, each asking what it needs in its own dialog and sent as the CLI says it."""

from __future__ import annotations

from functools import partial

from kraft import answers, client
from kraft.console import acts, words
from kraft.console.dialogs import Menu, Note
from kraft.console.words import usd

#: The `m` menu (spec §6): each entry's door (`actions`), in the web's order
#: of the header's menu, and its label. Only open doors are listed.
MENU = (
    ("skip", "Skip"),
    ("unblock", "Unblock"),
    ("raise_budget", "Raise budget…"),
    ("escalate", "Escalate…"),
    ("complete", "Complete…"),
    ("cancel", "Cancel item…"),
    ("archive", "Archive…"),
)
#: What each of the menu's dialogs asks.
SKIP = "A note for the record, or Enter for none."
ESCALATE = "What the agent should look into: it gets the item's stop and this."
REASON = "Why: the reason goes in the run log."
RAISE = "The new cap in dollars, or none for no cap. It must be above what the item has spent."


def cancel_lines(preview: dict) -> list[str]:
    """What cancelling does, in the web's Cancel card words (`CancelCard`,
    `stopsNow`), from `client.cancel_preview`."""
    run = preview.get("running")
    if run:
        name = words.task_name(run["task"]) if run.get("task") else run.get("node")
        attempt = f"attempt {run['attempt']}" if run.get("attempt") else ""
        stops = f"{', '.join(p for p in (name, attempt) if p)}. That attempt's work is lost."
    else:
        stops = "Nothing is running."
    kept = preview.get("kept") or {}
    findings = f"{kept['findings']} findings" if kept.get("findings") else "findings"
    threads = f"{kept['threads']} threads" if kept.get("threads") else "threads"
    spent = (preview.get("spend") or {}).get("spent_usd") or 0
    return [
        f"Stops now: {stops}",
        f"Keeps: branch {kept.get('branch')}, the worktree until it is archived, "
        f"{findings}, {threads} and the run log.",
        f"Spend: {usd(spent)} stays on the ledger and in today's total.",
    ]


class MenuActions:
    """The Item's menu and its dialogs, for the Item view (`self.item`,
    `self.work_item_id`, `self._dead`)."""

    def _menu_entries(self) -> list[tuple[str, str]]:
        doors = (self.item or {}).get("actions") or []
        return [(door, label) for door, label in MENU if door in doors]

    def action_menu(self) -> None:
        if item := self.item:
            self.run_worker(partial(self._menu, item), group="act")

    async def _menu(self, item: dict) -> None:
        """Pick an entry, then ask what it needs and send it, each in the
        CLI's own sentence: the same `acts.act` the keys use."""
        title = " ".join((item.get("title") or "").split())
        name = await self.app.push_screen_wait(
            Menu(f"{item['id'][:8]}  {title}", self._menu_entries())
        )
        # The menu was modal: the Item may have moved on or gone meanwhile.
        if name is not None and self.work_item_id == item["id"] and not self._dead:
            await getattr(self, f"_{name}")(item)

    async def _ask(self, question: str, lines: list[str], *, required: bool) -> str | None:
        return await self.app.push_screen_wait(Note(question, lines, required=required))

    async def _skip(self, item: dict) -> None:
        what = item.get("pending_gate") or item.get("current_node_id") or "the current node"
        note = await self._ask(f"Skip {what} on {item['id'][:8]}", [SKIP], required=False)
        if note is not None:
            said = answers.item_action(answers.skipped(what))
            await acts.act(self.app, client.skip(note or None, item["id"]), said)

    async def _unblock(self, item: dict) -> None:
        await acts.act(self.app, client.unblock(item["id"]), answers.unblocked)

    async def _escalate(self, item: dict) -> None:
        message = await self._ask(f"Escalate {item['id'][:8]}", [ESCALATE], required=True)
        if message:
            said = answers.item_action(answers.ESCALATED, small=answers.escalated)
            await acts.act(self.app, client.escalate(message, item["id"]), said)

    async def _complete(self, item: dict) -> None:
        reason = await self._ask(f"Complete {item['id'][:8]}", [REASON], required=True)
        if reason:
            said = answers.item_action(answers.COMPLETED, status=False)
            await acts.act(self.app, client.complete(reason, item["id"]), said)

    async def _cancel(self, item: dict) -> None:
        """The confirm is the reason's dialog (spec §7.8): what cancelling stops
        and keeps, from `cancel_preview`, above the required reason."""
        try:
            preview = await client.cancel_preview(item["id"])
        except client.ClientError as exc:
            self.app.notify_refusal(exc)
            self.app.live.reread()
            return
        lines = [*cancel_lines(preview), "", REASON]
        reason = await self._ask(f"Cancel {item['id'][:8]}?", lines, required=True)
        if reason:
            said = answers.item_action(answers.CANCELLED, status=False)
            await acts.act(self.app, client.cancel(reason, item["id"]), said)

    async def _archive(self, item: dict) -> None:
        """Through the CLI (spec §4.2): its preview, its refusals and its
        result in the output panel, after the console's confirm (spec §7.8)."""
        await self.app.run_asking(["item", "archive", item["id"], "--yes"])

    async def _raise_budget(self, item: dict) -> None:
        """Spec §7.2: a cap at or below what was spent is the server's 422,
        said in the dialog, which asks again; a cap raised whose retry was
        refused is said in the server's words, which say both."""
        error = ""
        while True:
            typed = await self.app.push_screen_wait(
                Note(f"Raise the budget of {item['id'][:8]}", [RAISE], required=True, error=error)
            )
            if typed is None:
                return
            try:
                usd_ = answers.dollars(typed)
            except ValueError as exc:
                error = str(exc)
                continue
            try:
                result = await client.raise_budget(usd_, item["id"])
            except client.ClientError as exc:
                if exc.status == 422:
                    error = client.reason_of(str(exc))
                    continue
                self.app.notify_refusal(exc)
                self.app.live.reread()
                return
            self.app.notify(answers.raised_budget(usd_)(result), markup=False)
            return
