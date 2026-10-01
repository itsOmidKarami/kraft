"""`kraft admin harnesses [ID]`: harness profiles from a terminal
(Kraft-archr) -- a table, one profile's detail, `--json`."""

from __future__ import annotations

import json

import pytest

from kraft import cli


def test_the_table_lists_each_profile_its_provider_and_the_tasks_using_it(app, capsys, monkeypatch):
    # The last column truncates at the terminal's width; wide enough for every
    # task the shipped library gives `claude`.
    monkeypatch.setenv("COLUMNS", "400")
    cli.main(["admin", "harnesses"])
    lines = capsys.readouterr().out.split("\n\n")[0].splitlines()
    assert lines[0].split() == ["ID", "PROVIDER", "ENABLED", "CHAINS", "USED", "BY"]
    rows = {line.split()[0]: line.split() for line in lines[1:]}
    assert rows["codex"][1:] == ["fake", "yes", "-", "-"]
    assert rows["claude"][1:5] == ["fake", "yes", "default,", "quick-task"]
    assert "tasks.implementer," in rows["claude"]


def test_the_table_names_a_chain_that_selects_a_profile_on_its_own_task():
    """A chain setting `harness:` on its own task has no library task to show
    under USED BY; its CHAINS cell names it (Kraft-9efnk.35)."""
    from kraft.cli import templates

    profile = {
        "id": "codex",
        "provider": "codex",
        "enabled": True,
        "used_by": [],
        "problems": [],
        "chains": ["mine"],
    }
    out = templates._render_profiles({"profiles": [profile], "error": None})
    assert out.splitlines()[1].split() == ["codex", "codex", "yes", "mine", "-"]


def test_the_agent_profiles_follow_with_their_model_per_provider(app, capsys, monkeypatch):
    """Kraft-ps1ao: the tiers `profile:` selects, under the harnesses."""
    monkeypatch.setenv("COLUMNS", "400")
    cli.main(["admin", "harnesses"])
    tiers = capsys.readouterr().out.split("\n\n")[1].splitlines()
    assert tiers[0].split() == ["PROFILE", "EFFORT", "MODEL", "USED", "BY"]
    rows = {line.split()[0]: line.split() for line in tiers[1:]}
    assert set(rows) == {"deep", "strong", "fast"}
    assert rows["fast"][1:] == ["low", "claude=haiku,", "fake=haiku", "-"]
    assert "tasks.implementer," in rows["strong"]


def test_a_profile_whose_providers_differ_shows_each_providers_effort():
    from kraft.cli.templates import _render_profiles

    tier = {
        "id": "mixed",
        "effort": None,
        "providers": {
            "claude": {"model": "opus", "effort": "max"},
            "codex": {"model": "gpt-5.6-sol", "effort": "high"},
        },
        "used_by": [],
        "problems": [],
    }
    shared = {
        **tier,
        "id": "same",
        "effort": "high",
        "providers": {"claude": {"model": "opus", "effort": "high"}},
    }
    rows = {
        line.split()[0]: line
        for line in _render_profiles(
            {"profiles": [], "error": None, "file": "h.yaml", "agent_profiles": [tier, shared]}
        ).splitlines()
        if line.split() and line.split()[0] in {"mixed", "same"}
    }
    assert "per provider" in rows["mixed"]
    assert "claude=opus (max)" in rows["mixed"] and "codex=gpt-5.6-sol (high)" in rows["mixed"]
    assert "claude=opus" in rows["same"] and "(high)" not in rows["same"]


def test_one_profile_prints_its_settings_and_users(app, capsys):
    cli.main(["admin", "harnesses", "claude"])
    out = capsys.readouterr().out
    assert "provider    fake" in out
    assert "model=sonnet" in out
    assert "chains      default, quick-task" in out
    assert "tasks.implementer" in out


def test_json_prints_the_api_payload(app, capsys):
    cli.main(["admin", "harnesses", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert {"codex", "claude"} <= {p["id"] for p in payload["profiles"]}


def test_an_unknown_profile_is_a_kraft_message_and_exit_1(app, capsys):
    with pytest.raises(SystemExit) as caught:
        cli.main(["admin", "harnesses", "nope"])
    assert caught.value.code == 1
    assert "404" in capsys.readouterr().err
