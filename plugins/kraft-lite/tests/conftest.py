"""kraft-lite's own test configuration. It runs with nothing installed but
pytest (CI's lite-floor job), so it imports nothing from Kraft."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
if str(PLUGIN) not in sys.path:
    sys.path.insert(0, str(PLUGIN))

import kl  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_home(request, tmp_path_factory, monkeypatch):
    """No test scans the machine's own `~/.claude/{plugins,skills}`, which
    `kl.SKILL_ROOTS` resolved against the real home when `kl` was imported:
    what `detect` reports, and how long it takes, would depend on whoever
    runs the suite. The roots are moved under an empty home, and `HOME` with
    them for a `kl.py` run as a child.

    An e2e test keeps the real `HOME`: the real `bd` it drives commits with
    the git identity configured there."""
    home = tmp_path_factory.mktemp("home")
    real = Path.home()

    def moved(root: Path) -> Path:
        try:
            return home / root.relative_to(real)
        except ValueError:  # relative to the repo, not the home
            return root

    monkeypatch.setattr(kl, "SKILL_ROOTS", tuple(moved(root) for root in kl.SKILL_ROOTS))
    if request.node.get_closest_marker("e2e") is None:
        monkeypatch.setenv("HOME", str(home))


@pytest.fixture
def kl_cli():
    """`kl.py` run as a child in `cwd`, as the human's session runs it.

    `ok=True` asserts a clean exit and hands back the parsed JSON (`{}` for no
    output); `ok=False` asserts a refusal and hands back the process, so a test
    can read what it said on stderr."""

    def run(cwd: Path, *args: str, ok: bool = True):
        result = subprocess.run(
            [sys.executable, str(PLUGIN / "kl.py"), *args],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
        if not ok:
            assert result.returncode != 0, result.stdout
            return result
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout) if result.stdout.strip() else {}

    return run


class FakeBd:
    """A bd that holds what was imported and exports it newest first, as
    `bd export` promises no order. `imports` keeps every payload it was sent,
    so a test can say what reached the issue store."""

    def __init__(self):
        self.stored: dict[str, dict] = {}
        self.imports: list[str] = []

    def __call__(self, cmd, **kwargs):
        if cmd[1] == "import":
            self.imports.append(kwargs["input"])
            for line in kwargs["input"].splitlines():
                record = json.loads(line)
                self.stored[record["id"]] = record
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        lines = [json.dumps(r) for r in reversed(list(self.stored.values()))]
        return subprocess.CompletedProcess(cmd, 0, stdout="\n".join(lines) + "\n", stderr="")


@pytest.fixture
def fake_bd():
    return FakeBd()


#: What `Walk.slice` splits a feature into when a test names no plan of its own.
SLICE_PLAN = {
    "feature_branch": "feature/x",
    "state_branch": "kraft-lite/state",
    "slices": [
        {"key": "a", "title": "Alpha", "tasks": [1, 2]},
        {"key": "b", "title": "Beta", "tasks": [3], "after": ["a"]},
    ],
}


class Walk:
    """The default chain walked as far as a test needs it, through `run(cwd,
    *args)`: `kl_cli` for the real CLI, or `kl.main` in-process."""

    def __init__(self, run):
        self.run = run

    def start(self, cwd: Path) -> str:
        return self.run(cwd, "start", "--title", "A feature")["chain_id"]

    def to_chain_review(self, cwd: Path) -> str:
        """A chain whose spec and plan gates are approved, so the next node is
        `chain_review`. Returns the chain id."""
        chain_id = self.start(cwd)
        for name in ("spec_approval", "plan_approval"):
            self.run(cwd, "gate", "--name", name)
            self.run(cwd, "approve")
        assert self.run(cwd, "state")["node"] == "chain_review"
        return chain_id

    def write_plan(self, cwd: Path, plan: dict | None = None) -> Path:
        path = cwd / "plan.json"
        path.write_text(json.dumps(plan or SLICE_PLAN))
        return path

    def slice(self, cwd: Path, plan: dict | None = None) -> str:
        """A chain sliced at `chain_finalized`, standing at `implementation`."""
        chain_id = self.to_chain_review(cwd)
        self.run(cwd, "gate", "--name", "chain_finalized")
        self.run(cwd, "approve", "--slices", str(self.write_plan(cwd, plan)))
        return chain_id


@pytest.fixture
def walk(kl_cli):
    return Walk(kl_cli)


@pytest.fixture
def walk_in_process(monkeypatch, capsys):
    """`Walk` over `kl.main` in this process, so a test can swap `kl.store_for`."""

    def run(cwd: Path, *args: str):
        monkeypatch.chdir(cwd)
        capsys.readouterr()
        kl.main(list(args))
        out = capsys.readouterr().out
        return json.loads(out) if out.strip() else {}

    return Walk(run)
