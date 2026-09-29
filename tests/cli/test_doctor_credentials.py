"""`kraft doctor`'s credentials row: which names a sandbox's egress proxy
holds, and on which hosts, and which still pass through (ruling E2)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
import yaml
from support.harness import make_repo

from kraft import client, doctor

# `app` fixture: tests/conftest.py. It wires client.transport.http() to the ASGI app.

_OWN = {"env": "MY_KEY", "inject": [{"domain": "a.io", "header": "x-key"}]}


@pytest.mark.parametrize(
    ("credentials", "repo", "daemon", "ok", "said"),
    [
        (None, {}, {}, None, None),
        (
            [{"env": "ANTHROPIC_API_KEY"}],
            {},
            {"ANTHROPIC_API_KEY": "sk"},
            True,
            "proxy-managed: ANTHROPIC_API_KEY on api.anthropic.com; passes through: "
            "CLAUDE_CODE_OAUTH_TOKEN, CODEX_API_KEY, GEMINI_API_KEY",
        ),
        ([{"env": "ANTHROPIC_API_KEY"}], {}, {}, False, "no value for ANTHROPIC_API_KEY"),
        ([{"env": "ANTHROPIC_API_KEY"}], {"env": {"ANTHROPIC_API_KEY": "sk"}}, {}, True, None),
        ([_OWN], {"env_passthrough": ["MY_KEY"]}, {"MY_KEY": "k"}, True, "MY_KEY on a.io"),
        (
            [{"env": "OPENAI_API_KEY"}],
            {"env": {"OPENAI_API_KEY": "k"}},
            {},
            "warn",
            "OPENAI_API_KEY is managed but no harness declares how to inject it, so it is "
            "only a sentinel; claude declares ANTHROPIC_API_KEY, CLAUDE_CODE_OAUTH_TOKEN; ",
        ),
    ],
    ids=["none", "named", "no-value", "value-from-repo-env", "its-own", "declared-nowhere"],
)
def test_doctor_lists_a_sandboxs_proxy_managed_credentials(
    app, tmp_path, monkeypatch, credentials, repo, daemon, ok, said
):
    """Each managed name with its hosts, every other name a harness declares
    as passing through, a failure for a managed name with no value where
    its launch would read one (`worker_env`, doctor's own env for the
    daemon's), and a warning for a name only, which no harness declares
    (codex's is CODEX_API_KEY, not OPENAI_API_KEY)."""
    monkeypatch.setattr(
        "kraft.worker.backends.docker.DockerBackend.health", AsyncMock(return_value=(True, "ok"))
    )
    for name in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "MY_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    for name, value in daemon.items():
        monkeypatch.setenv(name, value)
    path = make_repo(tmp_path)
    asyncio.run(client.ensure_repo(str(path)))
    repos_yaml = tmp_path / "templates" / "repos.yaml"
    data = yaml.safe_load(repos_yaml.read_text())
    sandbox = {"kind": "docker", "image": "img", "network": {"runtime": {"allow": ["a.io"]}}}
    data["repos"][0] |= repo | {"sandbox": sandbox | {"credentials": credentials}}
    repos_yaml.write_text(yaml.safe_dump(data))

    rows = [r for r in asyncio.run(doctor.run_checks()) if r["name"].startswith("credentials ")]

    assert [(r["ok"], r["warn"]) for r in rows] == (
        [] if ok is None else [(bool(ok), ok == "warn")]
    )
    assert said is None or said in rows[0]["detail"]
