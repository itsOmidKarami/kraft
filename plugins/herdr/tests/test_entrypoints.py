"""The startup hook, the take-over action and the options file."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from herdr_fakes import Fakes, result

import kraft_herdr


@pytest.fixture
def fakes(tmp_path, monkeypatch):
    f = Fakes(tmp_path / "fakes").install(monkeypatch)
    f.reply(
        "herdr", "workspace list", result(workspaces=[{"label": "Kraft", "workspace_id": "w1"}])
    )
    return f


@pytest.mark.parametrize(
    ("present", "running"),
    [
        pytest.param(True, True, id="bridge-running"),
        pytest.param(False, False, id="bridge-absent"),
        # Herdr's server restarted: the tab is back under its name, as a plain shell.
        pytest.param(True, False, id="bridge-tab-restored-without-it"),
    ],
)
def test_start_opens_the_bridge_once(fakes, tmp_path, present, running):
    panes = [{"pane_id": "w1:p1", "label": "kraft bridge"}] if present else []
    fakes.reply("herdr", "pane list", result(panes=panes))
    fakes.reply("herdr", "plugin pane open", result(plugin_pane={"pane": {"pane_id": "w1:p4"}}))
    held = kraft_herdr.acquire_lock(tmp_path / "state") if running else None
    assert kraft_herdr.start(kraft_herdr.Herdr(), state_dir=tmp_path / "state") == 0
    opened = fakes.calls("herdr", "plugin pane open")
    if running:
        assert opened == [] and fakes.calls("herdr", "pane close") == []
        held.close()
        return
    # the stale tab goes, so its name points at the new one only
    assert [a[2:] for a in fakes.calls("herdr", "pane close")] == ([["w1:p1"]] if present else [])
    (call,) = opened
    assert call[call.index("--entrypoint") + 1] == "bridge"
    assert call[call.index("--placement") + 1] == "tab" and "--no-focus" in call
    # named, so the next start (a live handoff runs it again) finds it
    assert [a[2:] for a in fakes.calls("herdr", "pane rename")] == [["w1:p4", "kraft bridge"]]


@pytest.fixture
def state_dir(tmp_path):
    directory = tmp_path / "state"
    directory.mkdir()
    (directory / "panes.json").write_text(json.dumps({"items": {"aaaaaaaa1": {"pane": "w1:p3"}}}))
    return directory


def test_takeover_runs_kraft_item_takeover_for_the_focused_item(fakes, state_dir):
    fakes.reply("herdr", "pane split", result(pane={"pane_id": "w1:p8"}))
    code = kraft_herdr.takeover(
        kraft_herdr.Herdr(), {"focused_pane_id": "w1:p3"}, state_dir=state_dir
    )
    assert code == 0
    (split,) = fakes.calls("herdr", "pane split")
    assert split[2] == "w1:p3"  # beside the item's tab
    (run,) = fakes.calls("herdr", "pane run")
    assert run[2:] == ["w1:p8", "kraft item takeover aaaaaaaa1"]


def test_takeover_refuses_a_non_kraft_pane(fakes, state_dir, capsys):
    code = kraft_herdr.takeover(
        kraft_herdr.Herdr(), {"focused_pane_id": "w1:p99"}, state_dir=state_dir
    )
    assert code == 1
    assert "not a Kraft item tab" in capsys.readouterr().err
    assert fakes.calls("herdr", "pane split") == [] and fakes.calls("herdr", "pane run") == []


def test_takeover_with_no_focused_pane_refuses_too(fakes, state_dir, monkeypatch):
    monkeypatch.delenv("HERDR_PANE_ID", raising=False)
    assert kraft_herdr.takeover(kraft_herdr.Herdr(), {}, state_dir=state_dir) == 1


def test_load_config_defaults(tmp_path):
    assert kraft_herdr.load_config(tmp_path) == {"notify": False, "kraft": "kraft"}
    (tmp_path / "config.json").write_text("not json")
    assert kraft_herdr.load_config(tmp_path) == {"notify": False, "kraft": "kraft"}
    (tmp_path / "config.json").write_text('{"notify": true, "kraft": "/opt/kraft", "other": 1}')
    assert kraft_herdr.load_config(tmp_path) == {"notify": True, "kraft": "/opt/kraft"}


def test_the_manifest_declares_every_entry_point_the_script_has():
    tomllib = pytest.importorskip("tomllib")  # 3.11+; the 3.9 floor skips it
    manifest = tomllib.loads((Path(kraft_herdr.__file__).parent / "herdr-plugin.toml").read_text())
    assert manifest["min_herdr_version"] == "0.9.2"
    assert {p["id"] for p in manifest["panes"]} == {"bridge", "item"}
    assert [a["id"] for a in manifest["actions"]] == ["takeover"]
    commands = [manifest["startup"][0]["command"]]
    commands += [p["command"] for p in manifest["panes"]] + [manifest["actions"][0]["command"]]
    assert {c[-1] for c in commands} == {"start", "bridge", "item", "takeover"}
    assert all(c[:2] == ["python3", "kraft_herdr.py"] for c in commands)
