"""Kraft-m2wru: every (harness, model, effort) the shipped library launches is
one its real CLI accepts. Kraft-eat7p shipped four claude tasks on
`gpt-5.6-terra` and nothing caught it: the claude provider has no `values:`
for `model` on purpose (harnesses/claude.yaml), so the CLI is the only judge.
Each distinct combination runs once, as its launch's own argv, on a one-word
prompt; an unknown model fails before a token is spent."""

from __future__ import annotations

import shlex
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from support.launches import distinct_launches

from kraft import harness as _harness
from kraft.adapters import agent

SHIPPED = Path(__file__).resolve().parents[1] / "templates"
#: The bundled harnesses only: collection runs outside `_isolated_kraft_home`,
#: and an operator's `$KRAFT_HOME` overlay is not what ships.
BUNDLED = _harness.load(SHIPPED / "no-overlay")


def _shipped() -> dict:
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("KRAFT_TEMPLATES_DIR", str(SHIPPED))
        return distinct_launches(SHIPPED, BUNDLED)


def _binary(harness: str, command: str) -> str:
    return Path(shlex.split(command)[0] if command else BUNDLED.valid[harness].command[0]).name


def _live(tmp_path, monkeypatch, tasks: dict) -> Path:
    live = tmp_path / "templates"
    (live / "chains").mkdir(parents=True)
    (live / "library.yaml").write_text(yaml.safe_dump({"tasks": tasks}))
    nodes = [{"id": n, "kind": "exec", "tasks": [{"id": "t", "extends": n}]} for n in tasks]
    (live / "chains" / "c.yaml").write_text(yaml.safe_dump({"id": "c", "nodes": nodes}))
    harnesses = {"claude": {"provider": "claude", "defaults": {"model": "sonnet"}}}
    profiles = {"fast": {"effort": "low", "model": {"claude": "haiku"}}}
    (live / "harnesses.yaml").write_text(
        yaml.safe_dump({"harnesses": harnesses, "profiles": profiles})
    )
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(live))
    return live


def test_each_combination_is_resolved_once_fallbacks_included(tmp_path, monkeypatch):
    """Dedupe on what the CLI sees, resolved as dispatch resolves it: the
    harness default, a profile, a task's own fields and a fallback entry."""
    agent_task = {"kind": "agent", "harness": "claude", "prompt": "p"}
    live = _live(
        tmp_path,
        monkeypatch,
        {
            "a": {**agent_task, "effort": "high"},
            "b": {**agent_task, "model": "sonnet", "effort": "high"},
            "c": {**agent_task, "profile": "fast"},
            "d": {**agent_task, "effort": "high", "fallback": [{"model": "opus"}]},
        },
    )
    assert distinct_launches(live) == {
        ("claude", "", "sonnet", "high"): "c:a.main.t",
        ("claude", "", "haiku", "low"): "c:c.main.t",
        ("claude", "", "opus", "high"): "c:d.main.t",
    }


def test_a_wrong_model_in_a_shipped_task_reaches_the_smoke(tmp_path, monkeypatch):
    """Kraft-eat7p's own defect, replayed on a copy of what ships: it becomes
    a combination the real-CLI smoke below launches."""
    home = tmp_path / "templates"
    shutil.copytree(SHIPPED, home)
    library = yaml.safe_load((home / "library.yaml").read_text())
    implementer = library["tasks"]["implementer"]
    del implementer["profile"]
    implementer |= {"model": "gpt-5.6-terra", "effort": "high"}
    (home / "library.yaml").write_text(yaml.safe_dump(library))
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(home))
    combos = distinct_launches(home)
    wrong = ("claude", "claude", "gpt-5.6-terra", "high")
    assert combos[wrong] == "default:implementation.main.implement"
    assert set(_shipped()) < set(combos)


@pytest.mark.parametrize(
    ("combo", "where"),
    [
        pytest.param(k, w, id="-".join(map(str, k)), marks=pytest.mark.e2e(_binary(k[0], k[1])))
        for k, w in _shipped().items()
    ],
)
def test_the_real_cli_accepts_every_shipped_model(combo, where, tmp_path):
    harness, command, model, effort = combo
    h = BUNDLED.valid[harness]
    options = {k: v for k, v in (("model", model), ("effort", effort)) if v is not None}
    argv = _harness.build_argv(
        h,
        command=command or None,
        prompt="Reply with the single word OK and nothing else.",
        context="This is a smoke test of the command line. Use no tools.",
        options=options,
    )
    log = tmp_path / "log"
    with log.open("w") as out:
        proc = subprocess.run(
            argv, cwd=tmp_path, stdout=out, stderr=subprocess.PIPE, text=True, timeout=300
        )
    usage = h.capabilities["usage"]
    reader = usage.reader if usage.source == "envelope" else None
    status = agent._envelope_is_error("done", log, proc.returncode, reader)
    assert proc.returncode == 0 and status == "done", (
        f"{where} launches {harness} with {options}, which the CLI refused "
        f"(exit {proc.returncode}):\n{log.read_text()[-2000:]}\n{proc.stderr[-2000:]}"
    )
