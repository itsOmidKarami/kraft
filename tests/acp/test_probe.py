import asyncio
import json
import sys
import tempfile

import pytest
from support.acp import agent_argv

from kraft.acp import probe


def _shake(flags: str, monkeypatch, **kw):
    monkeypatch.setenv("KRAFT_FAKE_ACP", flags)
    return asyncio.run(probe.handshake(agent_argv(), cwd=tempfile.gettempdir(), **kw))


@pytest.mark.parametrize(
    ("flags", "resume", "load", "system", "ok", "login"),
    [
        ("", True, True, True, True, False),
        ("no_resume,no_system_prompt", False, True, False, True, False),
        ("auth", True, True, True, False, True),
    ],
    ids=["full", "load-only", "needs-login"],
)
def test_the_handshake_reports_what_the_agent_offers(
    monkeypatch, flags, resume, load, system, ok, login
):
    got = _shake(flags, monkeypatch)
    assert (got.resume, got.load_session, got.system_prompt, got.ok, got.needs_login) == (
        resume,
        load,
        system,
        ok,
        login,
    )
    assert "model" in got.config_options or not ok


def test_a_missing_agent_is_a_failed_handshake_not_a_crash():
    got = asyncio.run(probe.handshake(["/no/such/agent"], cwd="/tmp"))
    assert got.ok is False and "no/such" in got.error


def test_an_agent_that_exited_before_we_wrote_is_a_failed_handshake_not_a_reset():
    async def go():
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            "pass",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
        )
        await proc.wait()
        with pytest.raises(probe._Failed, match="exited before answering initialize"):
            await probe._ask(proc, 1, "initialize", {})

    asyncio.run(go())


def test_an_agent_that_never_answers_fails_at_the_timeout():
    silent = [sys.executable, "-c", "import time; time.sleep(60)"]
    got = asyncio.run(probe.handshake(silent, cwd="/tmp", timeout_s=0.5))
    assert got.ok is False and "no answer" in got.error


def test_the_probe_sends_no_host_access_and_an_empty_mcp_server_list(monkeypatch, tmp_path):
    monkeypatch.setenv("KRAFT_FAKE_ACP_RECORD", str(tmp_path / "rec"))
    _shake("", monkeypatch)
    sent = [json.loads(x) for x in (tmp_path / "rec").read_text().splitlines()]
    assert sent[0]["params"]["clientCapabilities"] == {}
    new = next(m for m in sent if m.get("method") == "session/new")
    assert new["params"]["mcpServers"] == []
    assert not any(m.get("method") == "session/prompt" for m in sent)
