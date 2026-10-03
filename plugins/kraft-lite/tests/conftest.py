"""kraft-lite's own test configuration. It runs with nothing installed but
pytest (CI's lite-floor job), so it imports nothing from Kraft."""

from __future__ import annotations

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
