"""`api.deps.apply_policy`: what a policy save or reload makes the running server do."""

from __future__ import annotations

from types import SimpleNamespace

from kraft.adapters import subprocess as _subprocess
from kraft.api import deps

_BASE = "default: { attempts: 3, wall_clock_s: 60 }\n"


def test_apply_policy_sets_the_idle_limit(tmp_path, monkeypatch):
    """A reload that changes `agent_idle_minutes` reaches later launches."""
    (tmp_path / "policy.yaml").write_text(_BASE + "agent_idle_minutes: 7\n")
    monkeypatch.setattr(_subprocess, "IDLE_MINUTES", 30)

    deps.apply_policy(SimpleNamespace(), *deps.read_policy(tmp_path))

    assert _subprocess.IDLE_MINUTES == 7
