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


def _run_installer(tmp_path: Path, *, bin_on_path: bool, old_uv: bool = False):
    """install.sh against a stub `curl` and `uv`: the release feed names one
    wheel, and `uv tool install` puts a `kraft` into a bin directory that is
    on the caller's PATH or, as after a first uv install, not. An `old_uv`
    has no `tool dir --bin` to say where that directory is."""
    import subprocess

    stubs, bin_dir = tmp_path / "stubs", tmp_path / "uv-bin"
    stubs.mkdir()
    bin_dir.mkdir()
    (stubs / "curl").write_text(
        "#!/bin/sh\n"
        'case "$*" in *api.github.com*) '
        """echo '"browser_download_url": "https://x/kraft_sdlc-9.9.9-py3-none-any.whl"';; """
        "*) echo wheel;; esac\n"
    )
    tool_dir = "exit 2" if old_uv else f'echo "{bin_dir}"'
    (stubs / "uv").write_text(
        "#!/bin/sh\n"
        f'case "$1 $2" in "tool dir") {tool_dir};;\n'
        f"\"tool install\") printf '#!/bin/sh\\necho kraft 9.9.9\\n' > {bin_dir}/kraft; "
        f"chmod +x {bin_dir}/kraft;; esac\n"
    )
    for stub in stubs.iterdir():
        stub.chmod(0o755)
    path = ":".join([str(stubs), *([str(bin_dir)] if bin_on_path else []), "/usr/bin", "/bin"])
    result = subprocess.run(
        ["sh", str(ROOT / "install.sh")],
        capture_output=True,
        text=True,
        env={"PATH": path, "HOME": str(tmp_path)},
    )
    assert result.returncode == 0, result.stderr
    return result.stdout, bin_dir


def test_install_script_runs_the_kraft_it_installed_off_path(tmp_path):
    """A first uv install leaves its bin directory off the caller's PATH, so a
    bare `kraft --version` printed " installed." under "kraft: not found"."""
    out, bin_dir = _run_installer(tmp_path, bin_on_path=False)
    assert f"kraft 9.9.9 installed in {bin_dir}." in out
    assert f"{bin_dir} is not on your PATH: run  uv tool update-shell" in out


def test_install_script_names_no_path_it_cannot_find(tmp_path):
    """Without `uv tool dir --bin` the script guesses ~/.local/bin; when the
    command is not there, it must not tell you to run it from there."""
    out, _ = _run_installer(tmp_path, bin_on_path=False, old_uv=True)
    assert "kraft installed. If `kraft` is not found, run  uv tool update-shell" in out
    assert ".local/bin" not in out


def test_install_script_says_nothing_of_path_when_kraft_is_on_it(tmp_path):
    out, bin_dir = _run_installer(tmp_path, bin_on_path=True)
    assert f"kraft 9.9.9 installed in {bin_dir}." in out
    assert "not on your PATH" not in out
