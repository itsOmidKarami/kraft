"""Add-ons (spec §7.4): each collection, what it publishes and the add-ons
installed from it, with their version, whether they load, their auto-update
and an update waiting where `/health` says one is. `e`, `d` and the `m` menu
run their `kraft admin addon` verb in the output panel."""

from __future__ import annotations

import asyncio
from functools import partial
from typing import ClassVar

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.content import Content
from textual.widgets import OptionList
from textual.widgets.option_list import Option

from kraft import render
from kraft.addons import flow
from kraft.console.dialogs import Menu, Note
from kraft.console.screens import View
from kraft.console.screens.server import held

#: The list with no collection added.
EMPTY = "No collection is added. m adds one."
#: What the menu's "Add a collection" asks for.
SOURCE = "owner/repo, a git URL, or a directory. Nothing is installed yet."


def collection_line(row: dict) -> Content:
    """A collection's heading: its name, where it comes from, its ref and
    whether it auto-updates. Its source is written out, never obeyed."""
    at = f" @ {row['ref']}" if row["ref"] else ""
    auto = " · auto-update" if row["auto_update"] else ""
    return Content.assemble(
        (row["name"], "bold"),
        (f"  {render.printable(row['source'] or '')}{at}{auto}", "$kraft-muted"),
    )


def addon_line(name: str, row: dict | None, waiting: bool) -> Content:
    """One add-on under its collection: not installed; or its version,
    enabled or disabled, its auto-update, anything pending, why Kraft left
    an enabled one out, and an update waiting in the colour that needs you."""
    if row is None:
        return Content.assemble(f"  {name}  ", ("not installed", "$kraft-muted"))
    notes = [
        row["version"] or "-",
        "enabled" if row["enabled"] else "disabled",
        *([f"auto-update ({row['auto_update']})"] if row["auto_update"] else []),
        *row["pending"],
        *([render.printable(row["left_out"])] if row["enabled"] and row["left_out"] else []),
    ]
    return Content.assemble(
        (f"  {name}  ", "bold"),
        (" · ".join(notes), "$kraft-muted"),
        *([(" · update waiting", "$warning")] if waiting else []),
    )


def options(collections: list[dict], addons: list[dict], waiting: set[str]) -> list[Option]:
    """Each collection, then what it published when last read and any add-on
    installed from it that it no longer publishes (spec §7.4). Ids:
    `collection:NAME`, `addon:ID`."""
    installed = {row["id"]: row for row in addons}
    found: list[Option] = []
    for collection in collections:
        name = collection["name"]
        found.append(Option(collection_line(collection), id=f"collection:{name}"))
        if collection["addons"] is None:
            said = Content.styled("  not fetched yet · m fetches it", "$kraft-muted")
            found.append(Option(said, disabled=True))
        listed = [entry["name"] for entry in collection["addons"] or []]
        mine = [i.split("@", 1)[0] for i in installed if i.split("@", 1)[1] == name]
        for addon in [*listed, *(n for n in mine if n not in listed)]:
            addon_id = f"{addon}@{name}"
            line = addon_line(addon, installed.get(addon_id), addon_id in waiting)
            found.append(Option(line, id=f"addon:{addon_id}"))
    return found


def _listing() -> tuple[list[dict], list[dict]]:
    """`kraft admin addon collection list` and `list`, as their rows."""
    return flow.collection_rows(), flow.addon_rows()


class Addons(View):
    label = "Add-ons"
    BINDINGS: ClassVar[list[BindingType]] = [
        # The view keeps the focus, so its letters act; the list only draws.
        Binding("down,j", "rows('cursor_down')", "down", show=False),
        Binding("up,k", "rows('cursor_up')", "up", show=False),
        Binding("e", "enable", "enable/disable"),
        Binding("d", "uninstall", "uninstall"),
        Binding("m", "menu", "menu"),
    ]

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.collections: list[dict] = []
        self.addons: list[dict] = []

    def compose(self) -> ComposeResult:
        rows = OptionList(id="addon-rows", markup=False)
        rows.can_focus = False
        yield rows

    @property
    def selected(self) -> tuple[str, str] | None:
        """What the cursor is on: `("collection", name)` or `("addon", id)`."""
        option = self.query_one("#addon-rows", OptionList).highlighted_option
        if option is None or option.id is None:
            return None
        kind, _, name = option.id.partition(":")
        return kind, name

    def installed(self, addon_id: str) -> dict | None:
        return next((row for row in self.addons if row["id"] == addon_id), None)

    def _collection(self, name: str) -> dict | None:
        return next((row for row in self.collections if row["name"] == name), None)

    def on_show(self) -> None:
        self._read()

    def on_hide(self) -> None:
        """Another view is shown (this one stays mounted): read nothing."""
        self.workers.cancel_group(self, "read")

    def rows_changed(self, rows: list[dict], events: list[dict]) -> None:
        if not events:  # reconnected, or read again on purpose: so is the list
            self._read()

    def health_changed(self, health: dict | None) -> None:
        if self.display:  # "update waiting" is `/health`'s
            self.draw()

    def _read(self) -> None:
        # A hidden view reads nothing: it reads again when shown.
        if self.display:
            self.run_worker(self._read_now, group="read", exclusive=True)

    async def _read_now(self) -> None:
        # Files, and git for a fetched collection's catalogue: off the event loop.
        try:
            self.collections, self.addons = await asyncio.to_thread(_listing)
        except flow.Refusal as exc:
            self.app.notify(str(exc), severity="error", markup=False)
            return
        self.draw()

    def draw(self) -> None:
        """The list again; the cursor stays on its row, else the first."""
        rows = self.query_one("#addon-rows", OptionList)
        keep = rows.highlighted_option.id if rows.highlighted_option is not None else None
        found = options(self.collections, self.addons, held(self.app.health))
        rows.set_options(found or [Option(Content.styled(EMPTY, "$kraft-muted"), disabled=True)])
        ids = [option.id for option in found]
        rows.highlighted = (
            ids.index(keep) if keep is not None and keep in ids else (0 if ids else None)
        )
        self.refresh_bindings()

    @on(OptionList.OptionHighlighted, "#addon-rows")
    def _moved(self) -> None:
        self.refresh_bindings()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        kind, name = self.selected or ("", "")
        if action in ("enable", "uninstall"):
            return kind == "addon" and self.installed(name) is not None
        return True

    def action_rows(self, name: str) -> None:
        getattr(self.query_one("#addon-rows", OptionList), f"action_{name}")()

    def action_enable(self) -> None:
        kind, name = self.selected or ("", "")
        if kind == "addon" and (row := self.installed(name)):
            verb = "disable" if row["enabled"] else "enable"
            self.run_worker(partial(self._run, ["admin", "addon", verb, name]), group="act")

    def action_uninstall(self) -> None:
        kind, name = self.selected or ("", "")
        if kind == "addon" and self.installed(name):
            self.run_worker(partial(self._run, ["admin", "addon", "uninstall", name]), group="act")

    async def _run(self, argv: list[str]) -> None:
        """The CLI verb in the output panel, after the console's confirm where
        it cannot be undone (`uninstall`, spec §7.8); then the list again."""
        await self.app.run_asking(argv)
        self._read()

    def action_menu(self) -> None:
        if not self.app.busy():  # before the menu: a verb refused after it throws the choice away
            self.run_worker(partial(self._menu, self.selected), group="act")

    async def _menu(self, selected: tuple[str, str] | None) -> None:
        """Auto-update, or add, fetch or remove a collection (spec §6), for
        the row `m` was pressed on, never the one under the cursor after."""
        kind, name = selected or ("", "")
        collection = name.rpartition("@")[2] if kind == "addon" else name
        found = self._collection(collection)
        # One read in place from a directory has no auto-update (addons.yaml
        # refuses it) and nothing to fetch. Its source is the path, validated
        # absolute; a git URL never starts with `/`.
        git = found is not None and not (found["source"] or "").startswith("/")
        verbs: dict[str, list[str]] = {}
        if git and kind == "addon" and (row := self.installed(name)):
            verbs["auto"] = ["auto-update", name, "off" if row["auto_update"] == "addon" else "on"]
        elif git and kind == "collection":
            state = "off" if found["auto_update"] else "on"
            verbs["auto"] = ["collection", "auto-update", name, state]
        entries = [("auto", f"Auto-update {name} {verbs['auto'][-1]}")] if "auto" in verbs else []
        entries.append(("add", "Add a collection…"))
        if git:
            verbs["fetch"] = ["collection", "update", collection]
            entries.append(("fetch", f"Fetch {collection}'s catalogue"))
        if found is not None:
            verbs["remove"] = ["collection", "remove", collection]
            entries.append(("remove", f"Remove {collection}"))
        picked = await self.app.push_screen_wait(Menu("Add-ons", entries))
        if picked == "add":
            source = await self.app.push_screen_wait(
                Note("Add a collection", [SOURCE], required=True)
            )
            if not source:
                return
            verbs["add"] = ["collection", "add", "--", source]
        if picked in verbs:
            await self._run(["admin", "addon", *verbs[picked]])
