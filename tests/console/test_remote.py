"""A console opened with `--server URL` (spec "Console"): its start-up, how it
ends a session, and what it will not do on this machine. The server is the
`remote_server` fixture; no test here reaches a network."""

# ruff: noqa: F811 -- `acme` is imported from `test_addons`, taken by name.
from __future__ import annotations

import pytest
from support.console_board import _text

from console.test_addons import _shown, acme  # noqa: F401
from kraft import cli, client
from kraft.console import app as console_app
from kraft.console.app import KraftConsole
from kraft.console.live import Link
from kraft.console.prompt import Prompt


def test_server_flag_sets_the_remote_target_and_skips_the_instance_check(monkeypatch):
    from kraft.client import remote
    from kraft.console import signin

    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True, raising=False)
    monkeypatch.setattr(signin, "connect", lambda url, **kw: remote.Target("https://box:443", "t"))
    monkeypatch.setattr(
        client, "health", lambda: pytest.fail("a remote server has no run_dir to compare")
    )
    monkeypatch.setattr(client.transport, "_target", None)  # put back after the test
    opened = []
    monkeypatch.setattr(KraftConsole, "run", lambda self: opened.append(client.is_remote()))
    with pytest.raises(SystemExit) as left:
        cli.main(["console", "--server", "https://box"])
    assert (left.value.code, opened) == (0, [True])


def test_server_flag_is_refused_in_a_worker_before_any_prompt(monkeypatch, capsys):
    from kraft.console import signin

    monkeypatch.setenv("KRAFT_WORK_ITEM_ID", "w1")
    monkeypatch.setattr(signin, "connect", lambda *a, **k: pytest.fail("prompted"))
    with pytest.raises(SystemExit):
        cli.main(["console", "--server", "https://box"])
    assert "a worker does not open the console" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("is_remote", "error", "said"),
    [
        (
            False,
            client.ClientError("x", kind=client.Failure.UNAUTHORIZED),
            "The server refused this machine's token (run/mcp-token).",
        ),
        (
            True,
            client.ClientError("kraft 401: x", kind=client.Failure.UNAUTHORIZED, status=401),
            "The server ended this session. Quit and run "
            "`kraft console --server https://kraft.example:443` to sign in again.",
        ),
        (
            True,
            client.ClientError("no", kind=client.Failure.UNREACHABLE),
            "No server at kraft.example:443.",
        ),
        (
            True,
            client.ClientError("kraft 500: boom", kind=client.Failure.SERVER_ERROR, status=500),
            "The server could not show the board: 500: boom",
        ),
    ],
    ids=[
        "local-token-wording-unchanged",
        "remote-session-ended",
        "remote-no-server-names-no-scheme",
        "remote-server-words",
    ],
)
def test_the_down_sentence_names_the_server_without_its_scheme_and_a_session_that_ended(
    monkeypatch, remote_server, is_remote, error, said
):
    if not is_remote:
        monkeypatch.setattr(client.transport, "_target", None)
    assert console_app.down_sentence(error) == said


def test_a_remote_console_never_offers_to_start_a_server_even_through_a_loopback_forward(
    monkeypatch,
):
    from kraft.client import remote

    monkeypatch.setattr(client.transport, "_target", remote.Target("http://127.0.0.1:9000", "t"))
    assert console_app.starts_here() is False


def test_a_remote_path_is_not_shortened_with_this_machines_home(monkeypatch, remote_server):
    monkeypatch.setenv("HOME", "/Users/omid")
    assert console_app.home_relative("/Users/omid/work/x") == "/Users/omid/work/x"


def test_a_session_that_ends_while_running_says_so_and_is_deleted(console, tmp_path, monkeypatch):
    from kraft.client import remote

    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path))
    store = remote.SessionStore(remote.sessions_path())
    store.put("https://kraft.example:443", "tok-1")
    monkeypatch.setattr(
        client.transport, "_target", remote.Target("https://kraft.example:443", "tok-1")
    )

    async def board_rows(**kw):
        raise client.status_error(401, "Not authenticated")

    monkeypatch.setattr(client, "board_rows", board_rows)

    async def scenario(app, pilot):
        await console.until(pilot, lambda: app.link is Link.DOWN)
        return _text(app, "#down")

    said = console.run(scenario)
    assert "ended this session" in said and "kraft console --server" in said
    assert store.get("https://kraft.example:443") is None


@pytest.mark.parametrize(
    "typed", ["/item pause a1", "/repo"], ids=["runs-a-child", "goes-to-a-screen"]
)
def test_a_remote_console_refuses_a_typed_command_and_does_nothing(
    console, remote_server, kraft_is, typed
):
    kraft_is("import sys; print('RAN', *sys.argv[1:])")

    async def scenario(app, pilot):
        before = app.view.id
        await pilot.press("slash")
        app.query_one(Prompt).value = typed
        await pilot.press("enter")
        await pilot.pause()
        return app.command, app.view.id == before, [n.message for n in app._notifications]

    command, stayed, toasts = console.run(scenario, notifications=True)
    assert (command, stayed) == (None, True)
    assert console_app.TYPED_REMOTE in toasts


def test_run_command_is_closed_remotely_whoever_asks(console, remote_server, kraft_is):
    """Every child the console starts goes through run_command; a remote console
    must never start a `kraft` on this machine."""
    kraft_is("import sys; print('RAN', *sys.argv[1:])")

    async def scenario(app, pilot):
        return await app.run_command(["admin", "doctor"]), app.command, console.output(app)

    assert console.run(scenario) == (None, None, [])


def test_a_remote_palette_lists_no_verbs_to_type(console, remote_server):
    async def scenario(app, pilot):
        return [title for title, about, _ in app.places() if about == "Type it at the prompt"]

    assert console.run(scenario) == []


def test_a_remote_addons_screen_offers_no_action(board, acme, remote_server):
    acme.install()
    console = board([])

    async def scenario(app, pilot):
        await _shown(console, app, pilot)
        await pilot.press("j")
        return {label for _, label in app.screen_keys()}

    assert (
        console.run(scenario)
        & {
            "install",
            "update",
            "enable/disable",
            "uninstall",
            "menu",
        }
        == set()
    )


def test_a_remote_addons_screen_reads_nothing_here_and_says_where_they_are(
    board, acme, remote_server, monkeypatch
):
    from kraft.console.screens import addons

    acme.install()  # this machine's add-on: the server's list must not show it
    read: list[None] = []  # a failure raised in the read worker would not reach the test
    monkeypatch.setattr(addons, "_listing", lambda: read.append(None) or ([], []))
    console = board([])

    async def scenario(app, pilot):
        return await _shown(console, app, pilot)

    assert (console.run(scenario), read) == ([addons.REMOTE], [])
