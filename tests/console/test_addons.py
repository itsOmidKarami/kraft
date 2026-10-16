"""Add-ons (spec §7.4): each collection with what it publishes and what is
installed from it; `e`, `d` and the `m` menu run their `kraft admin addon`
verb in the output panel. The collection is read from a directory in this
test's Kraft home, so nothing is fetched, and every `kraft` child is a
`python -c` echo (`kraft_is`)."""

from __future__ import annotations

import asyncio
import threading
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
from kraft.console.screens.addons import Addons

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
    ("entry", "keys", "confirm", "ran", "open_"),
    [
        (True, ["j", "e"], None, "admin addon disable release@acme", True),
        ({"enabled": False}, ["j", "e"], None, "admin addon enable release@acme", True),
        (True, ["j", "j", "e"], None, None, False),
        (True, ["j", "d"], ["shift+tab", "enter"], "admin addon uninstall release@acme", True),
        (True, ["j", "d"], ["escape"], None, True),
        (True, ["d"], None, None, False),
    ],
    ids=[
        "e-disables",
        "e-enables",
        "e-on-one-not-installed",
        "d-after-the-confirm",
        "d-cancelled",
        "d-on-a-collection",
    ],
)
def test_e_and_d_run_their_verbs(board, acme, kraft_is, entry, keys, confirm, ran, open_):
    """`e` enables or disables the add-on under the cursor and `d` uninstalls
    it after the console's confirm (spec §7.8), each the CLI verb in the
    output panel; neither acts, nor is offered in the footer, on a row that
    is not an installed add-on."""
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
        view = app.query_one(Addons)
        return console.output(app), [view.check_action(a, ()) for a in ("enable", "uninstall")]

    ran_lines = [f"kraft {ran}", ran, "exit 0"] if ran else []
    assert console.run(scenario) == (ran_lines, [open_, open_])


FROM_GIT = ["auto", "add", "fetch", "remove"]
IN_PLACE = ["add", "remove"]


@pytest.mark.parametrize(
    ("at", "entries", "down", "source", "ran", "auto"),
    [
        ("addon:lint@beta", FROM_GIT, 0, None, "admin addon auto-update lint@beta on", "none"),
        (
            "addon:lint@beta",
            FROM_GIT,
            0,
            None,
            "admin addon auto-update lint@beta off",
            "addon",
        ),
        (
            "collection:beta",
            FROM_GIT,
            0,
            None,
            "admin addon collection auto-update beta on",
            "none",
        ),
        (
            "collection:beta",
            FROM_GIT,
            0,
            None,
            "admin addon collection auto-update beta off",
            "collection",
        ),
        (
            "addon:lint@beta",
            ["add", "fetch", "remove"],
            1,
            None,
            "admin addon collection update beta",
            "collection",
        ),
        ("collection:beta", FROM_GIT, 2, None, "admin addon collection update beta", "none"),
        (
            "collection:acme",
            IN_PLACE,
            0,
            "acme/kraft-addons",
            "admin addon collection add -- acme/kraft-addons",
            "none",
        ),
        ("addon:release@acme", IN_PLACE, 1, None, "admin addon collection remove acme", "none"),
    ],
    ids=[
        "auto-update-of-the-addon",
        "auto-update-off-of-the-addon",
        "auto-update-of-the-collection",
        "auto-update-off-of-the-collection",
        "no-addon-auto-update-under-an-auto-updating-collection",
        "fetches",
        "adds-a-collection",
        "removes-the-addons-collection",
    ],
)
def test_the_menu_runs_its_verbs(board, acme, kraft_is, at, entries, down, source, ran, auto):
    """`m` (spec §6), for the row it was pressed on: auto-update and fetch
    for a collection fetched from git and its add-ons, never for one read in
    place from a directory; add a collection; remove the row's. Each is the
    CLI verb in the output panel. `beta` is never fetched: its mirror does
    not exist, so its catalogue reads as not fetched yet. `auto` is who
    auto-updates `lint@beta` already: nobody, the add-on, or `beta`, which
    holds every add-on's setting (the CLI refuses an add-on's own then)."""
    write_yaml(
        acme.config / "addons.yaml",
        {
            "collections": {
                "acme": {"path": str(acme.repo)},
                "beta": {"git": "file:///nowhere/beta.git", "auto_update": auto == "collection"},
            },
            "addons": {"lint@beta": {"auto_update": True} if auto == "addon" else True},
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


@pytest.mark.parametrize(
    ("row", "line"),
    [
        (
            {"name": "acme", "source": "/x[/]y", "ref": None, "auto_update": False},
            "acme  /x[/]y",
        ),
        (
            {"name": "acme", "source": "/a\x1b[2Kb", "ref": "v1", "auto_update": True},
            "acme  /a\\x1b[2Kb @ v1 · auto-update",
        ),
    ],
    ids=["plain", "written-out-with-ref-and-auto-update"],
)
def test_a_collection_line_writes_out_its_source(row, line):
    """Its source is the typed one: `[/]` as typed, a control character
    written out, never obeyed."""
    assert addons_view.collection_line(row).plain == line


@pytest.mark.parametrize(
    ("beta", "says"),
    [
        ({"git": "file:///nowhere/beta.git"}, addons_view.NOT_FETCHED),
        ({"path": "DIRECTORY"}, "collection.json unreadable"),
    ],
    ids=["git-never-fetched", "directory-unreadable"],
)
def test_a_collection_without_a_catalogue_says_why(board, acme, tmp_path, beta, says):
    """Only a collection fetched from git is told to be fetched; a directory
    read in place that cannot be read is told as it is, since `m` offers it
    no fetch."""
    empty = tmp_path / "empty"
    empty.mkdir()
    beta = {k: str(empty) if v == "DIRECTORY" else v for k, v in beta.items()}
    write_yaml(acme.config / "addons.yaml", {"collections": {"beta": beta}, "addons": {}})
    console = board([])

    async def scenario(app, pilot):
        return await _shown(console, app, pilot)

    assert console.run(scenario)[1] == f"  {says}"


BROKEN = "collections: [\n"
# `first_error` quotes the key as written.
ESCAPED = 'collections:\n  "bad\\e[2K name": {path: /x}\naddons: {}\n'


@pytest.mark.parametrize(
    "case",
    ["before-the-first-read", "refused-then-a-health-poll", "read-after-a-refusal", "written-out"],
)
def test_the_list_claims_no_collection_only_after_a_read_found_none(board, acme, monkeypatch, case):
    """A health answer redraws the list: before the first read it says it is
    reading, and after a refusal the refusal stays, in the flow's words
    written out (in the list and in its toast), until a read succeeds.
    `EMPTY` is only for a read that found no collection."""
    gate = threading.Event()
    real = flow.collection_rows

    def held_back():
        gate.wait(5)
        return real()

    if case == "before-the-first-read":
        monkeypatch.setattr(flow, "collection_rows", held_back)
    else:
        (acme.config / "addons.yaml").write_text(ESCAPED if case == "written-out" else BROKEN)
    console = board([])

    def refusal_drawn(app) -> bool:
        return any("addons.yaml" in row for row in _rows(app))

    async def scenario(app, pilot):
        try:
            await _settled(console, app, pilot)
            await pilot.press("3")
            if case != "before-the-first-read":
                await console.until(pilot, lambda: refusal_drawn(app))
            app._live_health({"status": "ok"})
            await pilot.pause()
            drawn = _rows(app)
            toasts = [str(t.render()) for t in app.screen.query("Toast")]
            if case == "read-after-a-refusal":
                write_yaml(acme.config / "addons.yaml", {"collections": {}, "addons": {}})
                app._live_event([], [])
                await console.until(pilot, lambda: _rows(app) == [addons_view.EMPTY])
            return drawn, toasts
        finally:
            gate.set()

    drawn, toasts = console.run(scenario, notifications=True)
    if case == "before-the-first-read":
        assert drawn == [addons_view.READING]
    else:
        assert len(drawn) == 1 and "addons.yaml" in drawn[0]
        assert toasts == drawn  # `\x1b` written out in both, never an escape
        assert ("\\x1b" in drawn[0]) == (case == "written-out")


def test_m_while_a_command_runs_says_so_and_opens_no_menu(board, acme):
    """A verb refused after the menu would throw the choice (or a typed
    source) away: the menu does not open while another command runs."""
    console = board([])

    async def scenario(app, pilot):
        app.command = object()  # what `run_command` checks
        await _shown(console, app, pilot)
        await pilot.press("m")
        await pilot.pause(0.2)
        toasts = [str(t.render()) for t in app.screen.query("Toast")]
        app.command = None
        return isinstance(app.screen, Menu), toasts

    assert console.run(scenario, notifications=True) == (
        False,
        ["A command is still running: Ctrl+O shows it, x stops it."],
    )


def test_a_view_hidden_while_it_reads_draws_nothing(board, acme, monkeypatch):
    """Hiding cancels a read on its way, and a health answer redraws only a
    shown view: nothing is drawn, so nothing is claimed, while it is hidden."""
    gate = threading.Event()
    real = flow.collection_rows
    drawn: list[None] = []

    def held_back():
        gate.wait(5)
        return real()

    monkeypatch.setattr(flow, "collection_rows", held_back)
    monkeypatch.setattr(Addons, "draw", lambda self: drawn.append(None))
    console = board([])

    async def scenario(app, pilot):
        try:
            await _settled(console, app, pilot)
            await pilot.press("3")
            await pilot.pause()  # its read is waiting on the gate
            await pilot.press("1")
            drawn.clear()  # a health answer may have redrawn it while shown
            gate.set()
            await pilot.pause(0.2)
            app._live_health({"status": "ok"})
            await pilot.pause()
            return drawn
        finally:
            gate.set()

    assert console.run(scenario) == []
