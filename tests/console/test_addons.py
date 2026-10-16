"""Add-ons (spec §7.4): each collection with what it publishes and what is
installed from it; `e`, `d` and the `m` menu run their `kraft admin addon`
verb in the output panel. The collection is read from a directory in this
test's Kraft home, so nothing is fetched, and every `kraft` child is a
`python -c` echo (`kraft_is`)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from support.addons import AGENT, chain, edit_addons_yaml, make_collection
from support.console_board import _settled
from textual.widgets import Input, OptionList

from kraft import client
from kraft.addons import fetch, flow
from kraft.addons import update as addon_update
from kraft.config import write_yaml
from kraft.console.dialogs import Confirm, Menu, Note
from kraft.console.screens import addons as addons_view

ECHO = "import sys; print(' '.join(sys.argv[1:]))"
ID = "release@acme"
GOOD = {"library": {"tasks": {"base": AGENT}}, "chains": {"ship": chain("base")}}


def _never(what: str):
    def called(*args, **kwargs):
        raise AssertionError(f"{what} in a console add-on test")

    return called


@pytest.fixture
def acme(console, tmp_path, monkeypatch):
    """This test's Kraft home with the collection `acme` added from a
    directory, `[/]` in its path, publishing `release` and `tools`, nothing
    installed. Read from its directory, it is never fetched: a fetch, or a
    question at the terminal, fails the test. The running server's reload is
    recorded in `reloads`."""
    config_dir, addons_dir = flow.home()
    assert tmp_path in config_dir.parents  # never a real Kraft home
    repo = make_collection(tmp_path / "x[/]y", {"release": GOOD, "tools": GOOD})
    write_yaml(
        config_dir / "addons.yaml", {"collections": {"acme": {"path": str(repo)}}, "addons": {}}
    )
    monkeypatch.setattr(fetch, "fetch", _never("a fetch"))
    monkeypatch.setattr("builtins.input", _never("a question at the terminal"))
    reloads: list[None] = []

    async def reload_addons():
        reloads.append(None)
        return {}

    monkeypatch.setattr(client, "reload_addons", reload_addons)

    def install(entry=True) -> None:
        """`release` 1.0.0, as an accepted install leaves it; `entry` is its
        `addons.yaml` entry after."""
        results = addon_update.update(
            [ID],
            accept=lambda _: True,
            entries={ID: True},
            config_dir=config_dir,
            addons_root=addons_dir,
        )
        assert [r.outcome for r in results] == ["applied"]
        if entry is not True:
            edit_addons_yaml(config_dir, lambda written: written["addons"].__setitem__(ID, entry))

    def written() -> tuple[str, str]:
        lock = config_dir / "addons.lock"
        return (config_dir / "addons.yaml").read_text(), lock.read_text() if lock.exists() else ""

    return SimpleNamespace(
        repo=repo, config=config_dir, reloads=reloads, install=install, written=written
    )


def _rows(app) -> list[str]:
    rows = app.default_screen.query_one("#addon-rows", OptionList)
    return [option.prompt.plain for option in rows.options]


async def _shown(console, app, pilot) -> list[str]:
    """Add-ons, once its list is drawn (all at once, by `set_options`)."""
    await _settled(console, app, pilot)
    await pilot.press("3")
    await console.until(pilot, lambda: _rows(app))
    return _rows(app)


ROW = {
    "id": ID,
    "version": "1.0.0",
    "enabled": True,
    "auto_update": None,
    "pending": [],
    "left_out": None,
}


@pytest.mark.parametrize(
    ("row", "waiting", "line"),
    [
        (None, False, "  release  not installed"),
        (ROW, True, "  release  1.0.0 · enabled · update waiting"),
        (
            {**ROW, "auto_update": "collection", "pending": ["ref change pending"]},
            False,
            "  release  1.0.0 · enabled · auto-update (collection) · ref change pending",
        ),
        (
            {**ROW, "left_out": "its store is gone \x1b[2K"},
            False,
            "  release  1.0.0 · enabled · its store is gone \\x1b[2K",
        ),
        (
            {**ROW, "version": None, "enabled": False, "left_out": "is disabled"},
            False,
            "  release  - · disabled",
        ),
    ],
    ids=["not-installed", "update-waiting", "auto-update-and-pending", "left-out", "disabled"],
)
def test_an_addon_line_says_how_it_stands(row, waiting, line):
    """Spec §7.4: version, enabled or disabled, auto-update and by whom,
    anything pending, and why Kraft left an enabled one out, written out,
    never obeyed. A disabled one is left out for that reason alone."""
    assert addons_view.addon_line("release", row, waiting).plain == line


@pytest.mark.parametrize(
    ("added", "entry", "waiting", "lines"),
    [
        (True, None, False, ["  release  not installed", "  tools  not installed"]),
        (
            True,
            True,
            True,
            ["  release  1.0.0 · enabled · update waiting", "  tools  not installed"],
        ),
        (
            True,
            {"enabled": False},
            False,
            ["  release  1.0.0 · disabled", "  tools  not installed"],
        ),
        (False, None, False, None),
    ],
    ids=["nothing-installed", "installed-with-an-update-waiting", "disabled", "none-added"],
)
def test_the_list_shows_each_collection_and_what_it_publishes(
    board, acme, monkeypatch, added, entry, waiting, lines
):
    """Spec §7.4: the collection and where it comes from, `[/]` drawn as
    typed; then each add-on it publishes, not installed or how it stands;
    "update waiting" only where `/health` says one is."""
    if not added:
        write_yaml(acme.config / "addons.yaml", {"collections": {}, "addons": {}})
    if entry is not None:
        acme.install(entry)

    async def health():
        await asyncio.Event().wait()  # the test hands the console its health

    monkeypatch.setattr(client, "health", health)
    console = board([])
    held = {"addon_updates": {ID: {"outcome": "held"}}} if waiting else {}

    async def scenario(app, pilot):
        app._live_health({"status": "ok", **held})
        return await _shown(console, app, pilot)

    want = [f"acme  {acme.repo}", *lines] if lines else [addons_view.EMPTY]
    assert console.run(scenario) == want


@pytest.mark.parametrize(
    ("entry", "keys", "confirm", "ran"),
    [
        (True, ["j", "e"], None, "admin addon disable release@acme"),
        ({"enabled": False}, ["j", "e"], None, "admin addon enable release@acme"),
        (True, ["j", "j", "e"], None, None),
        (True, ["j", "d"], ["shift+tab", "enter"], "admin addon uninstall release@acme"),
        (True, ["j", "d"], ["escape"], None),
    ],
    ids=["e-disables", "e-enables", "e-on-one-not-installed", "d-after-the-confirm", "d-cancelled"],
)
def test_e_and_d_run_their_verbs(board, acme, kraft_is, entry, keys, confirm, ran):
    """`e` enables or disables the add-on under the cursor and `d` uninstalls
    it after the console's confirm (spec §7.8), each the CLI verb in the
    output panel; neither acts on an add-on not installed."""
    acme.install(entry)
    kraft_is(ECHO)
    console = board([])

    async def scenario(app, pilot):
        await _shown(console, app, pilot)
        await pilot.press(*keys)
        if confirm:
            await console.until(pilot, lambda: isinstance(app.screen, Confirm))
            await pilot.press(*confirm)
            await console.until(pilot, lambda: not isinstance(app.screen, Confirm))
        if ran:
            await console.until(pilot, lambda: console.output(app)[-1:] == ["exit 0"])
        await pilot.pause()
        return console.output(app)

    assert console.run(scenario) == ([f"kraft {ran}", ran, "exit 0"] if ran else [])


FROM_GIT = ["auto", "add", "fetch", "remove"]
IN_PLACE = ["add", "remove"]


@pytest.mark.parametrize(
    ("at", "entries", "down", "source", "ran"),
    [
        ("addon:lint@beta", FROM_GIT, 0, None, "admin addon auto-update lint@beta on"),
        ("collection:beta", FROM_GIT, 0, None, "admin addon collection auto-update beta on"),
        ("collection:beta", FROM_GIT, 2, None, "admin addon collection update beta"),
        (
            "collection:acme",
            IN_PLACE,
            0,
            "acme/kraft-addons",
            "admin addon collection add -- acme/kraft-addons",
        ),
        ("addon:release@acme", IN_PLACE, 1, None, "admin addon collection remove acme"),
    ],
    ids=[
        "auto-update-of-the-addon",
        "auto-update-of-the-collection",
        "fetches",
        "adds-a-collection",
        "removes-the-addons-collection",
    ],
)
def test_the_menu_runs_its_verbs(board, acme, kraft_is, at, entries, down, source, ran):
    """`m` (spec §6), for the row it was pressed on: auto-update and fetch
    for a collection fetched from git and its add-ons, never for one read in
    place from a directory; add a collection; remove the row's. Each is the
    CLI verb in the output panel. `beta` is never fetched: its mirror does
    not exist, so its catalogue reads as not fetched yet."""
    write_yaml(
        acme.config / "addons.yaml",
        {
            "collections": {
                "acme": {"path": str(acme.repo)},
                "beta": {"git": "file:///nowhere/beta.git"},
            },
            "addons": {"lint@beta": True},
        },
    )
    kraft_is(ECHO)
    console = board([])

    async def scenario(app, pilot):
        await _shown(console, app, pilot)
        rows = app.default_screen.query_one("#addon-rows", OptionList)
        rows.highlighted = [option.id for option in rows.options].index(at)
        await pilot.press("m")
        await console.until(pilot, lambda: isinstance(app.screen, Menu))
        offered = [option.id for option in app.screen.query_one(OptionList).options]
        await pilot.press(*["down"] * down, "enter")
        if source is not None:
            await console.until(pilot, lambda: isinstance(app.screen, Note))
            app.screen.query_one(Input).value = source
            await pilot.press("enter")
        await console.until(pilot, lambda: console.output(app)[-1:] == ["exit 0"])
        return offered, console.output(app)

    assert console.run(scenario) == (entries, [f"kraft {ran}", ran, "exit 0"])


def test_a_hidden_addons_view_reads_nothing(board, acme, monkeypatch):
    """It stays mounted behind the others and hears every reread and health
    answer: it reads when shown, and on a reread only while shown."""
    reads: list[None] = []
    real = flow.collection_rows

    def counted():
        reads.append(None)
        return real()

    monkeypatch.setattr(flow, "collection_rows", counted)
    console = board([])

    async def scenario(app, pilot):
        await _settled(console, app, pilot)  # the first board read was a reread too
        seen = [len(reads)]
        await _shown(console, app, pilot)
        app._live_event([], [])
        await console.until(pilot, lambda: len(reads) >= 2)
        await pilot.press("1")
        app._live_event([], [])
        app._live_health({"status": "ok"})
        await pilot.pause()
        return [*seen, len(reads)]

    assert console.run(scenario) == [0, 2]
