"""The paths a stranger installs and updates through must not name GitLab.

`glab` support in the forge adapter is a feature and legitimately mentions
gitlab.com all over; this checks only the three files that decide where a
user's Kraft comes from.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST_SURFACE = ("install.sh", "src/kraft/update.py", "README.md")


def test_distribution_surface_points_at_github():
    for name in DIST_SURFACE:
        text = (ROOT / name).read_text()
        assert "gitlab.com" not in text, f"{name} still points at GitLab"
        assert "GITLAB_TOKEN" not in text, f"{name} still carries the private-repo token"


def test_install_script_is_valid_shell():
    """`sh -n` parses without executing. A broken installer fails on a
    stranger's machine, where nobody will debug it."""
    import subprocess

    result = subprocess.run(["sh", "-n", str(ROOT / "install.sh")], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
