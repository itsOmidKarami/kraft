"""Add-ons (spec §7.4): each collection, what it publishes and the add-ons
installed from it, with their version, whether they load, their auto-update
and an update waiting where `/health` says one is. `i` and `u` install and
update here, after the review in the console's own dialog; `e`, `d` and the `m`
menu run their `kraft admin addon` verb in the output panel."""

from __future__ import annotations

import asyncio
import threading
from functools import partial
from typing import ClassVar

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.content import Content
from textual.message import Message
from textual.widgets import OptionList
from textual.widgets.option_list import Option

from kraft import client, render
from kraft.addons import flow, review
from kraft.console.dialogs import Confirm, Menu, Note
from kraft.console.screens import View
from kraft.console.screens.server import held

#: The list with no collection added: what add-ons are, in the reference
#: page's words (`12.addons.md`), and the key that adds one.
EMPTY = Content.assemble(
    (
        "An add-on is a directory of chains, library components, skills and agent "
        "profiles. A collection is a git repository or a directory that lists add-ons.",
        "$kraft-muted",
    ),
    "\n\n",
    ("m adds a collection.", "$kraft-muted"),
)
#: What a collection never fetched says.
NOT_FETCHED = "not fetched yet · m fetches it"
#: The list before its first read answers.
READING = "Reading the add-ons…"
#: What the menu's "Add a collection" asks for.
SOURCE = "owner/repo, a git URL, or a directory. Nothing is installed yet."


def in_place(collection: dict) -> bool:
    """Whether the collection is a directory read in place (its source is the
    absolute path `addons.yaml` validates; a git URL never starts with `/`):
    it has no auto-update and nothing to fetch."""
    return (collection["source"] or "").startswith("/")


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
            what = "collection.json unreadable" if in_place(collection) else NOT_FETCHED
            said = Content.styled(f"  {what}", "$kraft-muted")
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


class Waiting:
    """One review a thread waits on (spec §7.4): the first answer stands, the
    dialog's or the no a quitting console gives."""

    def __init__(self) -> None:
        self.done = threading.Event()
        self.yes = False

    def give(self, yes: bool | None) -> None:
        if not self.done.is_set():
            self.yes = bool(yes)
            self.done.set()


class ReviewAsked(Message):
    """A group of reviews from `update`'s thread, for the console's dialog."""

    def __init__(self, group: review.Group, waiting: Waiting) -> None:
        super().__init__()
        self.group = group
        self.waiting = waiting


def take(addon_id: str, install: bool, accept) -> list:
    """`kraft admin addon install` or `update` of one add-on, without the
    CLI's printing or asking. Blocking: run it on a thread."""
    entries = flow.install_entries(addon_id) if install else None
    return flow.update([addon_id], accept, entries=entries)


class Addons(View):
    label = "Add-ons"
    BINDINGS: ClassVar[list[BindingType]] = [
        # The view keeps the focus, so its letters act; the list only draws.
        Binding("down,j", "rows('cursor_down')", "down", show=False),
        Binding("up,k", "rows('cursor_up')", "up", show=False),
        Binding("i", "install", "install"),
        Binding("u", "update", "update"),
        Binding("e", "enable", "enable/disable"),
        Binding("d", "uninstall", "uninstall"),
        Binding("m", "menu", "menu"),
    ]

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        #: None until a read answers: an empty list is a read that found none.
        self.collections: list[dict] | None = None
        self.addons: list[dict] = []
        #: The review a thread waits on now; answered no if the console quits.
        self.waiting: Waiting | None = None
        #: An install or update is on its way: one at a time.
        self.taking = False
        #: Why the last read was refused, drawn until a read succeeds.
        self.refused: str | None = None

    def on_mount(self) -> None:
        self.add_class("panel")
        self.border_title = "Add-ons"

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
        return next((row for row in self.collections or [] if row["name"] == name), None)

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
            # Written out, never obeyed: a key of addons.yaml may hold a control character.
            self.refused = "\n".join(map(render.printable, str(exc).splitlines()))
            self.app.notify(self.refused, severity="error", markup=False)
            self.draw()
            return
        self.refused = None
        self.draw()

    def draw(self) -> None:
        """The list again; the cursor stays on its row, else the first."""
        rows = self.query_one("#addon-rows", OptionList)
        keep = rows.highlighted_option.id if rows.highlighted_option is not None else None
        found = options(self.collections or [], self.addons, held(self.app.health))
        # The sentence for no collection only after a read that found none.
        if self.refused is not None:
            said = Content.styled(self.refused, "$error")
        elif self.collections is None:
            said = Content.styled(READING, "$kraft-muted")
        else:
            said = EMPTY
        said_only = self.refused is not None or not found
        if said_only:
            found = [Option(said, disabled=True)]
        rows.set_options(found)
        ids = [] if said_only else [option.id for option in found]
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
        if action in ("install", "update"):
            # Closed once an update installed another Kraft: they import add-on
            # modules lazily, which would come from the new install (spec §3).
            if self.taking or self.app.updated or kind != "addon":
                return False
            return (self.installed(name) is None) == (action == "install")
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

    def action_install(self) -> None:
        self._take_selected(install=True)

    def action_update(self) -> None:
        self._take_selected(install=False)

    def _take_selected(self, *, install: bool) -> None:
        kind, name = self.selected or ("", "")
        if kind == "addon":
            # Set by the key, before the worker starts: a second press is closed now.
            self.taking = True
            self.refresh_bindings()
            # Not cancelled on hide: an accepted install must still reload the server.
            self.run_worker(partial(self._take, name, install), group="take")

    async def _take(self, addon_id: str, install: bool) -> None:
        """`i` and `u` (spec §7.4): the CLI's flow on a thread, its review
        asked in the console's dialog; on apply the server reloads as the
        CLI asks it."""
        try:
            results = await asyncio.to_thread(take, addon_id, install, self._accept)
        except flow.Refusal as exc:
            results = []
            # Written out, never obeyed, as the list's own refusal is.
            said = "\n".join(map(render.printable, str(exc).splitlines()))
            self.app.notify(said, severity="error", markup=False)
        finally:
            self.taking = False  # a quit's cancel too
        self.refresh_bindings()
        if not results:
            return
        bad = {r.outcome for r in results} & {"refused", "failed", "unpublished"}
        said = flow.render_results([flow.result_view(r) for r in results])
        self.app.notify(said, severity="error" if bad else "information", markup=False)
        if flow.applied(results):
            try:
                answer, error = await client.reload_addons(), None
            except client.ClientError as exc:
                answer, error = None, exc
            for line in flow.reload_warnings(answer, error):
                self.app.notify(line, severity="warning", markup=False)
        self._read()

    def _accept(self, group: review.Group) -> bool:
        """`update`'s `accept`, called on its thread: the review goes to the
        console's dialog and the thread waits for the answer. Stored before
        it is posted, so a quit either refuses the post or answers it."""
        waiting = self.waiting = Waiting()
        # Thread-safe, and False once the view is closing: the console is quitting.
        if not self.post_message(ReviewAsked(group, waiting)):
            return False
        waiting.done.wait()
        return waiting.yes

    def on_review_asked(self, message: ReviewAsked) -> None:
        group = message.group
        lines = review.render_group(group).splitlines()
        # As the CLI asks it ("Apply X? [y/N]"); Back has the focus.
        dialog = Confirm(f"Apply {group.title}?", lines, yes="Apply")
        self.app.push_screen(dialog, message.waiting.give)

    def on_unmount(self) -> None:
        """Quitting with a review open answers it no (spec §7.4, §8): its
        thread then ends, and the console can exit."""
        if self.waiting is not None:
            self.waiting.give(False)

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
        git = found is not None and not in_place(found)
        verbs: dict[str, list[str]] = {}
        # A collection that auto-updates holds every add-on's: the CLI refuses their own.
        if (
            git
            and kind == "addon"
            and (row := self.installed(name))
            and (row["auto_update"] != "collection")
        ):
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
