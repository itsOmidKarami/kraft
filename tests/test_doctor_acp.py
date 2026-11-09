"""`kraft admin doctor` rows for ACP harnesses."""

from __future__ import annotations

import asyncio
import shutil
import sys

import pytest
import yaml
from support.acp import FAKE
from support.harness import write_harness_profiles

from kraft import doctor
from kraft.paths import config_dir, default_harnesses_dir

# `app` fixture: tests/conftest.py. It points KRAFT_CONFIG_DIR at a hermetic library.


def _arrange(command: list[str], reports: list[str] | None = None) -> None:
    """One chain whose one task runs the `fake-acp` profile over an ACP entry at `command`."""
    live = config_dir()
    shutil.rmtree(live / "chains")
    (live / "chains").mkdir()
    task = {"kind": "agent", "harness": "fake-acp", "prompt": "p"}
    (live / "library.yaml").write_text(yaml.safe_dump({"tasks": {"t": task}}))
    node = {"id": "t", "kind": "exec", "tasks": [{"id": "t", "extends": "t"}]}
    (live / "chains" / "c.yaml").write_text(yaml.safe_dump({"id": "c", "nodes": [node]}))
    write_harness_profiles(live, {"fake-acp": {"provider": "fake-acp"}})
    default_harnesses_dir().mkdir(parents=True, exist_ok=True)
    entry = {"id": "fake-acp", "kind": "acp", "command": command}
    if reports is not None:
        entry["reports"] = reports
    (default_harnesses_dir() / "fake-acp.yaml").write_text(yaml.safe_dump(entry))


def _rows() -> dict[str, dict]:
    return {r["name"]: r for r in asyncio.run(doctor.run_checks())}


def test_doctor_shows_what_an_acp_agent_offers_and_flags_unreported_cost(app):
    _arrange([sys.executable, str(FAKE)])
    rows = _rows()
    assert rows["acp: fake-acp"]["ok"] and "resume=yes" in rows["acp: fake-acp"]["detail"]
    assert "options=model,effort" in rows["acp: fake-acp"]["detail"]
    cost = rows["acp-cost: fake-acp"]
    assert cost["warn"] and "unknown spend" in cost["detail"]


def test_an_entry_that_reports_cost_gets_no_cost_warning(app):
    _arrange([sys.executable, str(FAKE)], reports=["tokens", "cost"])
    rows = _rows()
    assert rows["acp: fake-acp"]["ok"] and "acp-cost: fake-acp" not in rows


def test_a_missing_agent_program_is_an_agent_row_and_is_not_probed(app):
    _arrange(["/no/such/agent"])
    rows = _rows()
    assert not rows["agent: fake-acp"]["ok"]
    assert not [n for n in rows if n.startswith("acp")]


def test_an_agent_that_asks_for_a_login_fails_its_row(app, monkeypatch):
    _arrange([sys.executable, str(FAKE)])
    monkeypatch.setenv("KRAFT_FAKE_ACP", "auth")
    row = _rows()["acp: fake-acp"]
    assert not row["ok"] and "log in" in row["detail"]


@pytest.fixture(autouse=True)
def _no_ambient_fake_flags(monkeypatch):
    monkeypatch.delenv("KRAFT_FAKE_ACP", raising=False)
