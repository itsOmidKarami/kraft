"""`kraft admin update` shows what changes in kraft-default before installing."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from support.addons import a_terminal, home

from kraft import cli, update
from kraft.addons import builtin, review

CHANGED = builtin.Preview(
    review.Review(builtin.ID, "2.5.0", "2.6.0", reach=("gate removed: review",)), (), None
)
UNCHANGED = builtin.Preview(review.Review(builtin.ID, "2.5.0", "2.6.0"), (), None)
OLDER = builtin.Preview(None, (), "v2.6.0 ships no built-in kraft-default")
OFFLINE = builtin.Preview(None, (), "cannot compare: no network")
BREAKS = builtin.Preview(UNCHANGED.review, ("chains/default.yaml: node 'verify' is gone",), None)


@dataclass
class Setup:
    installs: list


@pytest.fixture
def setup(monkeypatch, tmp_path):
    monkeypatch.setenv("KRAFT_CONFIG_DIR", str(home(tmp_path)))
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setattr(update, "installed", lambda: "2.5.0")
    monkeypatch.setattr(update, "_is_homebrew_install", lambda: False)
    monkeypatch.setattr(update, "_stale_kraft_tool", lambda _run: False)
    monkeypatch.setattr(update, "shadowing_kraft", lambda: None)
    monkeypatch.setattr(update, "latest", lambda **_: update.Release("v2.6.0", "u"))
    installs: list = []
    monkeypatch.setattr(update, "perform", lambda r, *_a, **_k: installs.append(r.tag) or 0)
    return Setup(installs)


@pytest.mark.parametrize(
    "preview, argv, typed, installs, says",
    [
        pytest.param(CHANGED, [], "y", True, "gate removed", id="accepted"),
        pytest.param(CHANGED, [], "n", False, "gate removed", id="declined"),
        pytest.param(CHANGED, ["-y"], None, True, "gate removed", id="yes-flag"),
        pytest.param(UNCHANGED, [], None, True, "kraft-default: unchanged", id="unchanged-asks"),
        pytest.param(OFFLINE, [], "y", True, "cannot compare", id="offline-still-asks"),
        pytest.param(OFFLINE, [], "n", False, "cannot compare", id="offline-declined"),
    ],
)
def test_the_update_shows_kraft_default_and_asks(
    monkeypatch, setup, capsys, preview, argv, typed, installs, says
) -> None:
    monkeypatch.setattr(builtin, "preview", lambda *a, **k: preview)
    if typed is None:
        monkeypatch.setattr("builtins.input", lambda *_: pytest.fail("asked"))
    else:
        a_terminal(monkeypatch, typed)
    if installs:
        cli.main(["admin", "update", *argv])
    else:
        with pytest.raises(SystemExit, match="nothing installed"):
            cli.main(["admin", "update", *argv])
    assert says in capsys.readouterr().out
    assert setup.installs == (["v2.6.0"] if installs else [])


def test_no_terminal_declines(monkeypatch, setup) -> None:
    monkeypatch.setattr(builtin, "preview", lambda *a, **k: CHANGED)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    with pytest.raises(SystemExit, match="nothing installed"):
        cli.main(["admin", "update"])
    assert setup.installs == []


@pytest.mark.parametrize(
    "preview, returns, status",
    [
        pytest.param(BREAKS, "kraft-default: ", "held", id="breaks-an-override"),
        pytest.param(OFFLINE, "kraft-default: cannot compare", "held", id="cannot-compare"),
        pytest.param(CHANGED, None, "changed", id="changed-is-recorded"),
        pytest.param(UNCHANGED, None, None, id="unchanged-records-nothing"),
        pytest.param(OLDER, None, None, id="ships-no-builtin-is-not-held"),
    ],
)
def test_unattended_prechecks_and_kraft_default(
    monkeypatch, setup, tmp_path, preview, returns, status
) -> None:
    monkeypatch.setattr(builtin, "preview", lambda *a, **k: preview)
    why = cli.admin.update_prechecks(update.Release("v2.6.0", "u"), interactive=False)
    if returns is None:
        assert why is None
    else:
        assert why is not None and why.startswith(returns)
    got = builtin.recorded(tmp_path / "run" / "addons")
    assert (got or {}).get("status") == status
    if status == "changed":
        assert got["lines"] == list(CHANGED.review.reach)


def test_restart_across_a_major_prints_the_preview(monkeypatch, setup, capsys) -> None:
    from kraft import client

    monkeypatch.setattr(builtin, "preview", lambda *a, **k: BREAKS)
    monkeypatch.setattr(update, "latest", lambda **_: update.Release("v3.0.0", "u"))
    monkeypatch.setattr(cli.admin, "_running_server_version", lambda: "2.5.0")
    marked: list = []

    async def mark_update(tag, channel=None):
        marked.append(tag)
        return {"installed": "2.5.0", "latest": tag, "behind": True, "marked": tag}

    monkeypatch.setattr(client, "mark_update", mark_update)
    cli.main(["admin", "update", "--restart"])
    out = capsys.readouterr().out
    assert out.index("breaks:") < out.index("will be installed")
    assert "held until you run" in out
    assert marked == ["v3.0.0"] and setup.installs == []


@pytest.mark.parametrize(
    "stale_tool, interactive",
    [
        pytest.param(True, False, id="refused-later-records-no-change"),
        pytest.param(False, True, id="accepting-clears-an-old-hold"),
        pytest.param(False, False, id="an-unchanged-unattended-update-clears-it"),
    ],
)
def test_the_note_follows_what_happened(monkeypatch, setup, tmp_path, stale_tool, interactive):
    root = tmp_path / "run" / "addons"
    builtin.record(root, status="held", from_="2.4.0", to="2.5.0", lines=["old"])
    monkeypatch.setattr(builtin, "preview", lambda *a, **k: CHANGED if stale_tool else UNCHANGED)
    monkeypatch.setattr(update, "_stale_kraft_tool", lambda _run: stale_tool)
    a_terminal(monkeypatch, "y")
    cli.admin.update_prechecks(update.Release("v2.6.0", "u"), interactive=interactive)
    got = builtin.recorded(root)
    if stale_tool:
        assert got["status"] == "held"  # untouched: nothing was recorded for 2.6.0
    else:
        assert got is None
