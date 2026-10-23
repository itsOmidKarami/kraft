# tests/remote/test_check.py
"""`worker check`: what this machine needs to run steps."""

from __future__ import annotations

import pytest

from kraft.remote import check


def _by_name(rows):
    return {r["name"]: r for r in rows}


async def test_git_and_one_agent_cli_pass_and_a_missing_credential_only_warns(monkeypatch):
    monkeypatch.setattr(
        check.shutil, "which", lambda exe: f"/bin/{exe}" if exe in ("git", "claude") else None
    )
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    rows = _by_name(await check.run(kraft_version="2.3.0"))
    assert rows["git"]["ok"] and rows["agent: claude"]["ok"]
    assert rows["credential: claude"]["ok"] and rows["credential: claude"]["warn"]
    assert not any(not r["ok"] for r in rows.values())
    assert rows["sandbox runtime"]["warn"]


@pytest.mark.parametrize(
    ("present", "failing"),
    [((), {"git", "agent CLIs"}), (("git",), {"agent CLIs"}), (("claude",), {"git"})],
    ids=["nothing", "no-agent", "no-git"],
)
async def test_what_must_be_present_fails_when_it_is_not(monkeypatch, present, failing):
    monkeypatch.setattr(
        check.shutil, "which", lambda exe: f"/bin/{exe}" if exe in present else None
    )
    rows = await check.run(kraft_version="2.3.0")
    assert {r["name"] for r in rows if not r["ok"]} == failing


async def test_a_server_that_refuses_the_token_fails_the_server_row():
    # server on a closed port: the row fails and says it could not connect
    rows = _by_name(
        await check.run(server="http://127.0.0.1:1", token="kwt_x", kraft_version="2.3.0")
    )
    assert not rows["server"]["ok"] and "connect" in rows["server"]["detail"]
