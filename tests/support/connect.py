"""What the connect tests share: an origin, forges.yaml, a terminal, and spies on the client."""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from kraft import client
from kraft.adapters.forge import probe
from support.harness import commit_all, git


def _origin(repo, url):
    """An origin (none for None), and a test command found, so the only
    questions are the forge's."""
    (repo / "pyproject.toml").write_text("[project]\nname = 'x'\n[tool.pytest.ini_options]\n")
    (repo / "uv.lock").write_text("version = 1\n")
    commit_all(repo)
    if url:
        git(repo, "remote", "add", "origin", url)


def _forges_yaml():
    return Path(os.environ["KRAFT_CONFIG_DIR"]) / "forges.yaml"


def _accounts():
    return yaml.safe_load(_forges_yaml().read_text())["accounts"]


def _prompts(monkeypatch, answers):
    """A terminal; every prompt recorded, answered from `answers`."""
    seen, left = [], iter(answers)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)

    def ask(prompt=""):
        seen.append(prompt)
        answer = next(left, None)
        assert answer is not None, f"a prompt nobody answered: {prompt!r}"
        return answer

    monkeypatch.setattr("builtins.input", ask)
    return seen


def _posted(monkeypatch):
    """Every body `POST /repos` was sent."""
    posted, post = [], client.transport._post

    async def spy(path, payload, **kw):
        if path == "/repos":
            posted.append(payload)
        return await post(path, payload, **kw)

    monkeypatch.setattr(client.transport, "_post", spy)
    return posted


def _spy(monkeypatch, *names):
    """Every call to the named `client` functions, in order: `[(name, args, kwargs)]`."""
    calls = []

    def wrapping(name, real):
        def wrap(*a, **kw):
            calls.append((name, a, kw))
            return real(*a, **kw)

        return wrap

    for name in names:
        monkeypatch.setattr(client, name, wrapping(name, getattr(client, name)))
    return calls


def _probes(monkeypatch, state_of):
    """The server's token probe answers `state_of(account)`: what the account holds decides,
    since a probe also runs in the background after every save."""

    async def probing(name, account, **kw):
        state = state_of(account)
        login = "canned" if state == "ok" else None
        return probe.Probe(name, "2026-01-01T00:00:00+00:00", state, f"{state} detail", login)

    monkeypatch.setattr(probe, "probe_account", probing)
