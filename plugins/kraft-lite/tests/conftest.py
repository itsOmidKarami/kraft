"""kraft-lite's own test configuration. It runs with nothing installed but
pytest (CI's lite-floor job), so it imports nothing from Kraft."""

from __future__ import annotations

import collections
import json
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
if str(PLUGIN) not in sys.path:
    sys.path.insert(0, str(PLUGIN))

import kl  # noqa: E402
import kl_chains  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_home(request, tmp_path_factory, monkeypatch):
    """No test scans the machine's own `~/.claude/{plugins,skills}`, which
    `kl.SKILL_ROOTS` resolved against the real home when `kl` was imported:
    what `detect` reports, and how long it takes, would depend on whoever
    runs the suite. The roots are moved under an empty home, and `HOME` with
    them for a `kl.py` run as a child. The variables that name a user library
    go too, so `start` resolves from the bundled one, as on a fresh machine.

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
        for name in ("KRAFT_CONFIG_DIR", "KRAFT_TEMPLATES_DIR", "KRAFT_HOME"):
            monkeypatch.delenv(name, raising=False)


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    """An empty home, which `Path.home()` answers for the test."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


@pytest.fixture(scope="module")
def texts():
    """Every skill's SKILL.md, by skill name."""
    return {path.parent.name: path.read_text() for path in (PLUGIN / "skills").glob("*/SKILL.md")}


@pytest.fixture
def default_chain(_isolated_home, tmp_path_factory):
    """Kraft's packaged V1 `default`, as `start` resolves and freezes it from
    the bundled library."""
    return kl_chains.resolve(tmp_path_factory.mktemp("bare"), {}, "default")


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


@pytest.fixture
def fake_bds():
    """One `FakeBd` per repo root, for a test that moves a run between two."""
    return collections.defaultdict(FakeBd)


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

    def advance(self, cwd: Path) -> dict:
        """Past the current node: a gate node blocked and approved, an exec node
        closed. Returns the state after it."""
        if self.run(cwd, "state")["kind"] == "gate":
            self.run(cwd, "gate")
            return self.run(cwd, "approve")
        return self.run(cwd, "close")

    def to(self, cwd: Path, node: str) -> None:
        """Walked on until `node` is the current one."""
        while (state := self.run(cwd, "state"))["node"] != node:
            assert state["status"] != "done", f"the chain finished without reaching {node}"
            self.advance(cwd)

    def finish(self, cwd: Path) -> None:
        """Walked on to `done`."""
        while self.run(cwd, "state")["status"] != "done":
            self.advance(cwd)

    def to_chain_revision(self, cwd: Path) -> str:
        """A chain whose spec and plan gates are approved, so the next node is
        `chain_revision`. Returns the chain id."""
        chain_id = self.start(cwd)
        self.to(cwd, "chain_revision")
        return chain_id

    def plan(self, **over) -> dict:
        """`SLICE_PLAN` with `over` on top. Main mode has no feature branch, so
        a main-mode plan names none unless `over` does."""
        base = dict(SLICE_PLAN, feature_branch=None if over.get("mode") == "main" else "feature/x")
        return {**base, **over}

    def write_plan(self, cwd: Path, plan: dict | None = None) -> Path:
        path = cwd / "plan.json"
        path.write_text(json.dumps(plan or SLICE_PLAN))
        return path

    def to_slice_gate(self, cwd: Path) -> str:
        """A chain blocked at `chain_revision_approval`, where it is sliced.
        Returns the chain id."""
        chain_id = self.to_chain_revision(cwd)
        self.run(cwd, "close")
        self.run(cwd, "gate")
        return chain_id

    def slice(self, cwd: Path, plan: dict | None = None) -> str:
        """A chain sliced at `chain_revision_approval`, standing at `implementation`."""
        chain_id = self.to_slice_gate(cwd)
        self.run(cwd, "approve", "--slices", str(self.write_plan(cwd, plan)))
        return chain_id

    def merge_slices(self, cwd: Path) -> None:
        """Every slice started from `ready` and merged, so a sliced run's
        `implementation` may close."""
        while ready := self.run(cwd, "slices")["ready"]:
            for key in ready:
                self.run(cwd, "slice-set", "--slice", key, "--status", "running")
                self.run(cwd, "slice-set", "--slice", key, "--status", "merged")


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
