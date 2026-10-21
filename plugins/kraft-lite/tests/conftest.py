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
