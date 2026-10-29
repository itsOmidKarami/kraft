"""`api.deps.apply_policy`: what a policy save or reload makes the running server do."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from kraft.adapters import subprocess as _subprocess
from kraft.adapters.forge import git as forge_git
from kraft.adapters.forge import mr as forge_mr
from kraft.api import deps

_BASE = "default: { attempts: 3, wall_clock_s: 60 }\n"


def test_apply_policy_sets_the_idle_limit(tmp_path, monkeypatch):
    """A reload that changes `agent_idle_minutes` reaches later launches."""
    (tmp_path / "policy.yaml").write_text(_BASE + "agent_idle_minutes: 7\n")
    monkeypatch.setattr(_subprocess, "IDLE_MINUTES", 30)

    deps.apply_policy(SimpleNamespace(), *deps.read_policy(tmp_path))

    assert _subprocess.IDLE_MINUTES == 7


def test_apply_policy_sets_the_forge_timeout(tmp_path, monkeypatch):
    """A reload that changes `forge_cli_timeout_s` reaches the next forge call,
    as Settings tells a person when they publish one."""
    (tmp_path / "policy.yaml").write_text(_BASE + "forge_cli_timeout_s: 45\n")
    monkeypatch.setattr(forge_git, "CLI_TIMEOUT_S", 120.0)

    deps.apply_policy(SimpleNamespace(), *deps.read_policy(tmp_path))

    assert forge_git.CLI_TIMEOUT_S == 45.0


def test_apply_policy_wakes_the_archive_sweep(tmp_path):
    """So a new `archive.after_days` applies on publish, as Settings says it does."""
    (tmp_path / "policy.yaml").write_text(_BASE + "archive: { after_days: 1 }\n")
    st = SimpleNamespace(archive_wake=asyncio.Event())

    deps.apply_policy(st, *deps.read_policy(tmp_path))

    assert st.archive_wake.is_set()


@pytest.mark.parametrize(
    "tail, signed",
    [
        ("attribution: { mr: false }\n", False),
        # A bare key, as uncommenting only its first line leaves it, loads as on.
        ("attribution:\n", True),
    ],
    ids=["off", "bare-key-is-on"],
)
def test_apply_policy_sets_mr_attribution(tmp_path, monkeypatch, tail, signed):
    """A reload that changes `attribution.mr` reaches the next description."""
    (tmp_path / "policy.yaml").write_text(_BASE + tail)
    monkeypatch.setattr(forge_mr, "ATTRIBUTION", not signed)

    deps.apply_policy(SimpleNamespace(), *deps.read_policy(tmp_path))

    assert forge_mr.ATTRIBUTION is signed
