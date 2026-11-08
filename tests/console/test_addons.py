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
from support.addons import AGENT, chain, edit_addons_yaml, make_collection, publish
from support.console_board import _settled
from textual.widgets import Button, Input, OptionList

from kraft import client
from kraft.addons import fetch, flow
from kraft.addons import update as addon_update
from kraft.addons.config import AddonsLock
from kraft.config import write_yaml
from kraft.console.dialogs import Confirm, Menu, Note
from kraft.console.screens import addons as addons_view
from kraft.console.screens.addons import Addons

ECHO = "import sys; print(' '.join(sys.argv[1:]))"
ID = "release@acme"
GOOD = {"library": {"tasks": {"base": AGENT}}, "chains": {"ship": chain("base")}}


def _asking(app) -> bool:
    """A Confirm done mounting: `#listing`, in a nested scroll, can mount after
    `#question`, and Back focused (the keys pressed start there) ends `on_mount`."""
    d = app.screen
    ready = all(d.query(w) for w in ("#question", "#listing", "#yes"))
    return isinstance(d, Confirm) and ready and getattr(d.focused, "id", None) == "no"


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


#: What Add-ons says with no collection: what they are, and the key.
EMPTY = (
    "An add-on is a directory of chains, library components, skills and agent "
    "profiles. A collection is a git repository or a directory that lists add-ons.\n\n"
    "m adds a collection."
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

    want = [f"acme  {acme.repo}", *lines] if lines else [EMPTY]
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
            await console.until(pilot, lambda: _asking(app))
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
                await console.until(pilot, lambda: _rows(app) == [EMPTY])
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


def _toasts(app) -> list[tuple[str, bool]]:
    return [(str(toast.render()), toast.has_class("-error")) for toast in app.screen.query("Toast")]


@pytest.mark.parametrize(
    ("entry", "change", "key", "answer", "asked", "said", "version"),
    [
        (
            None,
            None,
            "i",
            ["shift+tab", "enter"],
            "release@acme new, 1.0.0",
            ("release@acme: applied", False),
            "1.0.0",
        ),
        (
            None,
            None,
            "i",
            ["escape"],
            "release@acme new, 1.0.0",
            ("release@acme: declined", False),
            None,
        ),
        (None, "refused", "i", None, None, ("release@acme: refused", True), None),
        (
            True,
            "1.1.0",
            "u",
            ["shift+tab", "enter"],
            "release@acme 1.0.0 -> 1.1.0",
            ("release@acme: applied", False),
            "1.1.0",
        ),
        (True, None, "u", None, None, ("release@acme: current", False), "1.0.0"),
    ],
    ids=["i-applies", "i-cancelled", "i-refused", "u-applies", "u-current"],
)
def test_i_and_u_take_an_addon_after_its_review(
    board, acme, entry, change, key, answer, asked, said, version
):
    """Spec §7.4: the CLI's own flow, its review in the console's confirm
    (Apply or Back, Back focused); applied, the running server is asked
    to reload, as the CLI asks it. Nothing to take, or a refusal, asks
    nothing."""
    if entry is not None:
        acme.install(entry)
    if change == "1.1.0":
        publish(acme.repo, "release", version="1.1.0")
    elif change == "refused":
        publish(acme.repo, "release", library={"tasks": {"b[/]": {**AGENT, "kind": "subprocess"}}})
    console = board([])

    async def scenario(app, pilot):
        await _shown(console, app, pilot)
        await pilot.press("j", key)
        shown = None
        if answer:
            await console.until(pilot, lambda: _asking(app))
            dialog = app.screen
            shown = (
                str(dialog.query_one("#question").render()),
                str(dialog.query_one("#listing").render()).split("\n")[0],
                str(dialog.query_one("#yes", Button).label),
            )
            await pilot.press(*answer)
        await console.until(pilot, lambda: _toasts(app))
        text, error = _toasts(app)[0]
        return shown, (": ".join(text.split(": ")[:2]), error)

    shown, head = console.run(scenario, notifications=True)
    locked = AddonsLock.load(acme.config / "addons.lock").addons.get(ID)
    assert (shown, head, locked and locked.version, acme.reloads) == (
        (f"Apply {ID}?", asked, "Apply") if asked else None,
        said,
        version,
        [None] if said == ("release@acme: applied", False) else [],
    )


@pytest.mark.parametrize("when", ["dialog-open", "asked-after-quit"])
def test_quitting_with_a_review_open_answers_it_no(board, acme, monkeypatch, when):
    """Spec §7.4, §8: the review's thread waits on its answer; quitting
    answers it no, so nothing is installed and the console can exit. Asked
    after the view closed, the post is refused and the answer is no too."""
    before = acme.written()
    fired: list[None] = []
    asked: list[bool] = []
    gate = threading.Event()
    console = board([])
    if when == "asked-after-quit":
        real_take, unmount = addons_view.take, Addons.on_unmount

        def take(addon_id, install, accept):
            gate.wait(5.0)  # until the view has unmounted

            def answer(reviewed):
                asked.append(accept(reviewed))
                return asked[-1]

            return real_take(addon_id, install, answer)

        def on_unmount(self):
            unmount(self)
            gate.set()
            # Should the thread wait on a review nobody answers, this says yes
            # after 5 s: the test then fails on what was installed.
            timer = threading.Timer(5.0, lambda: self.waiting and self.waiting.give(True))
            timer.daemon = True
            timer.start()

        monkeypatch.setattr(addons_view, "take", take)
        monkeypatch.setattr(Addons, "on_unmount", on_unmount)

    async def scenario(app, pilot):
        await _shown(console, app, pilot)
        view = app.default_screen.query_one("#addons", addons_view.Addons)
        await pilot.press("j", "i")
        if when == "asked-after-quit":
            await console.until(pilot, lambda: view.taking)
            return None, None
        await console.until(pilot, lambda: _asking(app))
        # Should quitting leave the thread waiting, this answers it yes after
        # 5 s: the test then fails on what was installed instead of hanging.
        waiting = view.waiting
        timer = threading.Timer(5.0, lambda: (fired.append(None), waiting.give(True)))
        timer.daemon = True
        timer.start()
        return waiting, timer

    waiting, timer = console.run(scenario)
    if timer:
        timer.cancel()
    # The run joined the thread: what it was answered is in `asked` by now.
    answered = [waiting.yes] if waiting else asked
    assert (answered, fired, acme.written(), acme.reloads) == ([False], [], before, [])


@pytest.mark.parametrize(
    ("why", "key", "action"),
    [
        ("kraft-was-updated", "i", "install"),
        ("kraft-was-updated", "u", "update"),
        ("one-is-running", "i", "install"),
    ],
    ids=["updated-install", "updated-update", "one-is-running"],
)
def test_install_and_update_stay_closed(board, acme, why, key, action):
    """After an update installed another Kraft, the add-on modules would load
    from it (spec §3); while one install runs, a second would race it."""
    if key == "u":
        acme.install()
    console = board([])

    async def scenario(app, pilot):
        await _shown(console, app, pilot)
        view = app.default_screen.query_one("#addons", addons_view.Addons)
        await pilot.press("j")
        opened = view.check_action(action, ())  # before it is closed
        if why == "kraft-was-updated":
            app.updated = True
        else:
            view.taking = True
        await pilot.press(key)
        await pilot.pause()
        return opened, isinstance(app.screen, Confirm), view.check_action(action, ())

    assert console.run(scenario) == (True, False, False)


def test_the_key_closes_install_before_its_worker_starts(board, acme):
    """`taking` is set by the key, not by the worker: with no await between
    two presses, the second is already closed. The take is held on an Event,
    so nothing else moves."""
    release = threading.Event()
    console = board([])

    async def scenario(app, pilot):
        await _shown(console, app, pilot)
        view = app.default_screen.query_one("#addons", addons_view.Addons)
        await pilot.press("j")
        was = view.check_action("install", ())
        view.action_install()  # no await: the worker has not run
        closed = view.check_action("install", ())
        release.set()
        return was, closed

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(addons_view, "take", lambda *args: release.wait(5.0) and [])
        assert console.run(scenario) == (True, False)


def test_a_review_is_stored_before_it_is_posted(board, acme):
    """A quit between the store and the post must find the `Waiting` to
    answer it (ruling 12): when the post happens, it is already stored."""
    seen: list[bool] = []
    posted = Addons.post_message

    def post(self, message):
        if isinstance(message, addons_view.ReviewAsked):
            seen.append(self.waiting is message.waiting)
        return posted(self, message)

    console = board([])

    async def scenario(app, pilot):
        await _shown(console, app, pilot)
        await pilot.press("j", "i")
        await console.until(pilot, lambda: _asking(app))
        await pilot.press("escape")
        await console.until(
            pilot, lambda: not app.default_screen.query_one("#addons", Addons).taking
        )

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(Addons, "post_message", post)
        console.run(scenario)
    assert seen == [True]


@pytest.mark.parametrize("how", ["hidden-after-apply", "reload-refused", "no-server"])
def test_an_applied_take_reloads_the_server_and_says_when_it_could_not(
    board, acme, monkeypatch, how
):
    """Ruling 16: an accepted take is not cancelled by hiding the view, and
    the server is still asked to reload. A refused reload is a warning in the
    server's words, markup off; no server running is nothing to say."""
    hold, took = threading.Event(), threading.Event()
    real_take = addons_view.take

    def take(*args):
        results = real_take(*args)
        took.set()
        hold.wait(5.0)  # the view is hidden by now; the take commits after
        return results

    async def refused():
        raise client.ClientError(
            "kraft 500: boom [/]" if how == "reload-refused" else "no Kraft server",
            kind=client.Failure.REFUSED,
        )

    if how == "hidden-after-apply":
        monkeypatch.setattr(addons_view, "take", take)
    else:
        monkeypatch.setattr(client, "reload_addons", refused)
    console = board([])

    async def scenario(app, pilot):
        await _shown(console, app, pilot)
        view = app.default_screen.query_one("#addons", addons_view.Addons)
        await pilot.press("j", "i")
        await console.until(pilot, lambda: _asking(app))
        await pilot.press("shift+tab", "enter")
        if how == "hidden-after-apply":
            await console.until(pilot, took.is_set)
            await pilot.press("1")
            await console.until(pilot, lambda: not view.display)
            hold.set()
        await console.until(pilot, lambda: not view.taking)
        await pilot.pause()
        return [str(t.render()) for t in app.screen.query("Toast") if t.has_class("-warning")]

    warned = console.run(scenario, notifications=True)
    assert (warned, acme.reloads) == (
        ["warning: the running server did not reload: kraft 500: boom [/]"]
        if how == "reload-refused"
        else [],
        [None] if how == "hidden-after-apply" else [],
    )
