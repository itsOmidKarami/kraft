"""A fallback entry pairs with its harness everywhere a task's own route does
(Kraft-0a3h8 on Kraft-ps1ao): Settings → Harnesses, the guard on a harness
save, and `kraft admin doctor`. Each problem names the task, the list's source
(the task's own, or its profile's) and the entry's index."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from fastapi import HTTPException

from kraft import doctor
from kraft.api.routes import harnesses as harnesses_route
from kraft.templates.library import TemplateLibrary

HARNESSES = {"claude": {"provider": "claude"}, "codex": {"provider": "codex"}}
PROFILES = {
    "deep": {
        "effort": "high",
        "model": {"claude": "opus", "codex": "gpt-5.6-sol"},
        "fallback": [{"harness": "codex"}, {"profile": "fast"}],
    },
    "fast": {"effort": "low", "model": {"claude": "haiku"}},
}


def _live(tmp_path, monkeypatch, task: dict, *, profiles=PROFILES) -> Path:
    live = tmp_path / "templates"
    (live / "chains").mkdir(parents=True)
    (live / "library.yaml").write_text(
        yaml.safe_dump({"tasks": {"x": {"kind": "agent", "prompt": "p", **task}}})
    )
    chain = {
        "id": "c",
        "nodes": [{"id": "x", "kind": "exec", "tasks": [{"id": "t", "extends": "x"}]}],
    }
    (live / "chains" / "c.yaml").write_text(yaml.safe_dump(chain))
    (live / "harnesses.yaml").write_text(
        yaml.safe_dump({"harnesses": HARNESSES, "profiles": profiles})
    )
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(live))
    return live


def _state(live: Path):
    return SimpleNamespace(templates_dir=live, library=TemplateLibrary.from_yaml_dir(live))


def test_a_profiles_list_is_shown_with_each_entrys_problems(tmp_path, monkeypatch):
    """deep's second entry, `fast`, has no codex model: a codex task taking
    deep's list gets that problem on the entry, and the first entry none."""
    live = _live(tmp_path, monkeypatch, {"harness": "codex", "profile": "deep"})

    view = harnesses_route._view(_state(live))

    deep = next(p for p in view["agent_profiles"] if p["id"] == "deep")
    assert [{k: v for k, v in e.items() if k != "problems"} for e in deep["fallback"]] == [
        {"harness": "codex"},
        {"profile": "fast"},
    ]
    assert deep["fallback"][0]["problems"] == []
    (why,) = deep["fallback"][1]["problems"]
    assert why == (
        "chain 'c' task 'x.main.t': fallback entry 1 (profile 'deep''s list): "
        "profile 'fast' has no model for provider 'codex' (harness 'codex')"
    )


def test_a_save_that_breaks_a_profile_entrys_pairing_is_refused(tmp_path, monkeypatch):
    """The task runs on claude and takes deep's list, whose first entry runs deep
    on `codex`. Moving `codex` onto gemini, which deep names no model for,
    breaks only that entry: refused, naming it, and nothing written."""
    live = _live(tmp_path, monkeypatch, {"harness": "claude", "profile": "deep"})
    before = (live / "harnesses.yaml").read_text()
    request = SimpleNamespace(app=SimpleNamespace(state=_state(live)))

    with pytest.raises(HTTPException) as refused:
        asyncio.run(harnesses_route.put_harness("codex", {"provider": "gemini"}, request))

    assert refused.value.status_code == 422
    assert refused.value.detail == (
        "chain 'c' task 'x.main.t': fallback entry 0 (profile 'deep''s list): "
        "profile 'deep' has no model for provider 'gemini' (harness 'codex')"
    )
    assert (live / "harnesses.yaml").read_text() == before


@pytest.mark.parametrize(
    ("task", "where"),
    [
        ({"harness": "codex", "profile": "deep"}, "fallback entry 1 (profile 'deep''s list)"),
        (
            {"harness": "codex", "model": "gpt-5.6-terra", "fallback": [{"profile": "fast"}]},
            "fallback entry 0 (the task's list)",
        ),
    ],
    ids=["profile-list", "task-list"],
)
def test_doctor_fails_a_fallback_entry_the_launch_would_refuse(tmp_path, monkeypatch, task, where):
    _live(tmp_path, monkeypatch, task)

    failed = [r for r in doctor._agent_checks() if not r["ok"] and r["name"].startswith("profile")]

    assert [r["name"] for r in failed] == ["profile: fast"]
    assert f"chain 'c' task 'x.main.t' {where}:" in failed[0]["detail"]
    assert "profile 'fast' has no model for provider 'codex'" in failed[0]["detail"]


def test_doctor_is_quiet_when_every_entry_pairs(tmp_path, monkeypatch):
    _live(tmp_path, monkeypatch, {"harness": "claude", "profile": "deep"})
    assert not [r for r in doctor._agent_checks() if r["name"].startswith("profile")]
