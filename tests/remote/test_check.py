# tests/remote/test_check.py
"""`worker check`: what this machine needs to run steps."""

from __future__ import annotations

import pytest

from kraft.remote import check


def _by_name(rows):
    return {r["name"]: r for r in rows}


class _Backend:
    def __init__(self, kind, health):
        self.kind, self._health = kind, health

    async def health(self, sandbox, *, refresh=True):
        assert sandbox == {"kind": self.kind}
        if isinstance(self._health, Exception):
            raise self._health
        return self._health


@pytest.fixture(autouse=True)
def _no_sandbox_runtime(monkeypatch):
    """What a sandbox backend says about the machine running the tests is not what is tested."""
    monkeypatch.setattr(
        check.backends, "every", lambda: (_Backend("docker", (False, "no docker")),)
    )


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
    assert rows["sandbox runtime"]["warn"] and rows["sandbox: docker"]["skipped"]


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


@pytest.mark.parametrize(
    ("health", "usable"),
    [
        ({"docker": (True, "docker 27, image-less check"), "os": (False, "no bwrap")}, True),
        ({"docker": (False, "daemon down"), "os": (False, "no bwrap")}, False),
        ({"docker": RuntimeError("boom")}, False),
    ],
    ids=["one-usable", "none-usable", "check-crashes"],
)
async def test_a_row_per_sandbox_backend_with_its_health_and_a_warning_when_none_is_usable(
    monkeypatch, health, usable
):
    monkeypatch.setattr(
        check.backends, "every", lambda: tuple(_Backend(k, v) for k, v in health.items())
    )
    rows = _by_name(await check.run(kraft_version="2.3.0"))
    for kind, answer in health.items():
        row = rows[f"sandbox: {kind}"]
        assert row["ok"] and row["skipped"] is (isinstance(answer, Exception) or not answer[0])
        assert (answer[1] if isinstance(answer, tuple) else "boom") in row["detail"]
    assert ("sandbox runtime" in rows) is (not usable)
    assert not any(not r["ok"] for r in rows.values() if r["name"].startswith("sandbox"))


@pytest.mark.parametrize("present", [True, False], ids=["present", "absent"])
async def test_git_lfs_is_a_row_that_never_fails(monkeypatch, present):
    monkeypatch.setattr(
        check.shutil,
        "which",
        lambda exe: (
            f"/bin/{exe}" if exe in ("git", "claude") or (present and exe == "git-lfs") else None
        ),
    )
    row = _by_name(await check.run(kraft_version="2.3.0"))["git-lfs"]
    assert row["ok"] and row["skipped"] is (not present)
    assert row["detail"] == (
        "/bin/git-lfs" if present else "not installed: LFS repos are not placed here"
    )
