"""The one test that drives the real `bd` binary.

Every other bd test fakes `subprocess.run`, which is how three defects shipped
green: the dependency shape was wrong, `bd export -o -` wrote a file instead of
stdout, and walk order was taken from the store's order. All three pass a fake
and fail a database. Skipped when bd is absent, so the plugin still tests on a
machine that has never heard of it.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(shutil.which("bd") is None, reason="bd is not installed")


@pytest.fixture(scope="session")
def _bd_template(tmp_path_factory):
    """One `bd init`ed workspace per test-run process, handed out by `copytree`.

    `bd init` spins up Dolt (~2.6s), and doing it per test was most of this
    file's runtime. Every test here only needs a *working* bd workspace, so one
    init and a copy per test is the same thing for less than a tenth of the cost.
    """
    tpl = tmp_path_factory.mktemp("kl-bd-tpl")
    subprocess.run(["git", "init", "-q"], cwd=tpl, check=True)
    init = subprocess.run(["bd", "init", "--prefix", "KL"], cwd=tpl, capture_output=True, text=True)
    if init.returncode != 0:
        pytest.skip(f"bd init failed here: {init.stderr.strip()}")
    return tpl


@pytest.fixture
def bd_repo(tmp_path, _bd_template):
    """A throwaway bd workspace. Never the repo the suite is running in."""
    repo = tmp_path / "repo"
    shutil.copytree(_bd_template, repo)
    return repo


def kl(cwd: Path, *args: str) -> dict:
    result = subprocess.run(
        [sys.executable, str(PLUGIN / "kl.py"), *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"kl {' '.join(args)} failed: {result.stderr}"
    return json.loads(result.stdout) if result.stdout.strip() else {}


def test_a_chain_walks_through_a_real_bd_database(bd_repo):
    started = kl(bd_repo, "start", "--title", "Add a flag")
    assert started["backend"] == "bd", "a repo with .beads must use the bd store"

    state = kl(bd_repo, "state")
    assert state["node"] == "spec", "bd export order is arbitrary; the walk must not depend on it"
    assert state["hooks"] == ["on.spec.requested"]

    kl(bd_repo, "gate", "--name", "spec_approval")
    assert kl(bd_repo, "state")["status"] == "blocked"
    kl(bd_repo, "approve")
    assert kl(bd_repo, "state")["node"] == "plan"

    assert not (bd_repo / "-").exists(), "bd wrote a file named '-' instead of using stdout"


def test_the_records_really_round_trip_through_bd_import(bd_repo):
    """The fallback file's whole justification is that `bd import` accepts it."""
    kl(bd_repo, "start", "--title", "Add a flag")
    exported = subprocess.run(
        ["bd", "export"], cwd=bd_repo, capture_output=True, text=True, check=True
    ).stdout
    ours = [
        line
        for line in exported.splitlines()
        if line.strip() and json.loads(line)["id"].startswith("kl-")
    ]
    assert ours, "nothing of ours survived the write"

    reimport = subprocess.run(
        ["bd", "import", "-"],
        cwd=bd_repo,
        input="\n".join(ours) + "\n",
        capture_output=True,
        text=True,
    )
    assert reimport.returncode == 0, reimport.stderr


def test_the_jsonl_fallback_file_is_importable_by_bd(tmp_path, bd_repo):
    """`bd import .kraft-lite/chain.jsonl` is the documented migration. If that
    command fails, the shared format bought nothing."""
    plain = tmp_path / "no-beads"
    plain.mkdir()
    # Its own git root: state is written at the repo root, and without this the
    # bd fixture's repo one level up would own it.
    subprocess.run(["git", "init", "-q"], cwd=plain, check=True)
    kl(plain, "start", "--title", "Add a flag")
    fallback = plain / ".kraft-lite" / "chain.jsonl"
    assert fallback.is_file()

    result = subprocess.run(
        ["bd", "import", str(fallback)], cwd=bd_repo, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
