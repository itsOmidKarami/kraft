"""The paths a stranger installs and updates through must not name GitLab.

`glab` support in the forge adapter is a feature and legitimately mentions
gitlab.com all over; this checks only the three files that decide where a
user's Kraft comes from.
"""

from __future__ import annotations

from pathlib import Path

import pytest

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


def test_an_install_script_cut_short_runs_nothing(tmp_path):
    """Piped into sh, a download that stops partway is still a script: every
    line that arrived would run. Cut at any line, it must run none of them."""
    import subprocess

    stubs, log = tmp_path / "stubs", tmp_path / "ran"
    stubs.mkdir()
    for name in ("curl", "uv", "mktemp", "rm"):
        (stubs / name).write_text(f'#!/bin/sh\necho "{name} $*" >> {log}\n')
        (stubs / name).chmod(0o755)
    lines = (ROOT / "install.sh").read_text().splitlines(keepends=True)
    for cut in range(1, len(lines)):
        subprocess.run(
            ["sh"],
            input="".join(lines[:cut]),
            capture_output=True,
            text=True,
            env={"PATH": f"{stubs}:/usr/bin:/bin", "HOME": str(tmp_path)},
        )
        assert not log.exists(), f"cut after line {cut}, it ran: {log.read_text()}"


def _run_installer(tmp_path: Path, *, bin_on_path: bool, old_uv: bool = False, index: int = 0):
    """install.sh against a stub `curl` and `uv`: the release feed names one
    wheel, and `uv tool install` puts a `kraft` into a bin directory that is
    on the caller's PATH or, as after a first uv install, not. An `old_uv`
    has no `tool dir --bin` to say where that directory is. `index` is the
    exit code of an install from PyPI; every `uv` call is in `uv.log`."""
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
        f'echo "$*" >> {tmp_path}/uv.log\n'
        f'case "$*" in *==9.9.9) [ {index} = 0 ] || exit {index};; esac\n'
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


@pytest.mark.parametrize(
    ("index", "installs"),
    [
        (0, ["tool install --force kraft-sdlc==9.9.9"]),
        (
            1,
            [
                "tool install --force kraft-sdlc==9.9.9",
                "tool install --force kraft-sdlc @ https://x/kraft_sdlc-9.9.9-py3-none-any.whl",
            ],
        ),
    ],
    ids=["from-pypi", "wheel-url-before-pypi-has-it"],
)
def test_install_script_installs_the_release_by_version(tmp_path, index, installs):
    """A wheel from a temporary directory left uv a record of a file that was
    gone, so `uv tool upgrade` failed. The release's version from PyPI, or
    its wheel by URL in the minutes before PyPI has it."""
    _run_installer(tmp_path, bin_on_path=True, index=index)
    calls = (tmp_path / "uv.log").read_text().splitlines()
    assert [call for call in calls if call.startswith("tool install")] == installs


def test_install_script_says_nothing_of_path_when_kraft_is_on_it(tmp_path):
    out, bin_dir = _run_installer(tmp_path, bin_on_path=True)
    assert f"kraft 9.9.9 installed in {bin_dir}." in out
    assert "not on your PATH" not in out


def _run_installer_without_uv(tmp_path: Path, uv_installer: str | None, *, piped: bool = False):
    """install.sh on a machine with no uv. `uv_installer` is the script the
    stub `curl` serves for astral.sh's installer, or None for a failed
    download. PATH holds only the stubs and the tools the script needs, so a
    uv installed on the machine running the test is not found. `piped` feeds
    the script to bash on stdin, as `curl ... | bash` does."""
    import shutil
    import subprocess

    stubs = tmp_path / "stubs"
    stubs.mkdir()
    tools = ("sh", "grep", "head", "mktemp", "rm", "basename", "chmod", "mkdir", "printf", "cat")
    for tool in tools:
        if found := shutil.which(tool):
            (stubs / tool).symlink_to(found)
    (tmp_path / "uv-install.sh").write_text(uv_installer or "")
    fetch = "exit 22" if uv_installer is None else f"cat {tmp_path}/uv-install.sh"
    (stubs / "curl").write_text(
        "#!/bin/sh\n"
        f'case "$*" in *astral.sh*) {fetch};; '
        """*api.github.com*) echo '"browser_download_url": "https://x/k-9.9.9.whl"';; """
        "*) echo wheel;; esac\n"
    )
    (stubs / "curl").chmod(0o755)
    script = ROOT / "install.sh"
    return subprocess.run(
        [shutil.which("bash")] if piped else ["sh", str(script)],
        input=script.read_text() if piped else None,
        capture_output=True,
        text=True,
        env={"PATH": str(stubs), "HOME": str(tmp_path)},
    )


# A uv installer that puts a working stub `uv` where the real one goes.
_GOOD_UV_INSTALLER = (
    'mkdir -p "$HOME/.local/bin"\n'
    "printf '#!/bin/sh\\n"
    'case "$1 $2" in "tool install") echo installed-kraft;; *) exit 2;; esac\\n\''
    ' > "$HOME/.local/bin/uv"\n'
    'chmod +x "$HOME/.local/bin/uv"\n'
)


@pytest.mark.parametrize(
    ("uv_installer", "says"),
    [
        (None, "could not download the uv installer from https://astral.sh/uv/install.sh"),
        ("exit 3\n", "the uv installer failed; install uv yourself"),
        ("true\n", "the uv installer ran, but uv is not in"),
    ],
    ids=["download-fails", "installer-fails", "uv-missing-after"],
)
def test_install_script_stops_when_it_cannot_install_uv(tmp_path, uv_installer, says):
    """Piped into sh, a failed download was an empty script that sh ran
    happily, and the installer went on to fail later with `uv: not found`."""
    result = _run_installer_without_uv(tmp_path, uv_installer)
    assert result.returncode == 1, result.stdout + result.stderr
    assert says in result.stderr
    assert "not found" not in result.stderr
    assert "https://docs.astral.sh/uv/getting-started/installation/" in result.stderr


def test_install_script_goes_on_with_the_uv_it_installed(tmp_path):
    result = _run_installer_without_uv(tmp_path, _GOOD_UV_INSTALLER)
    assert result.returncode == 0, result.stderr
    assert "installed-kraft" in result.stdout


def test_install_script_keeps_its_own_stdin_from_the_uv_installer(tmp_path):
    """Under `curl ... | bash` the script's stdin is the rest of the script.
    An installer that reads stdin swallowed it, and the install stopped after
    uv without a word."""
    reads_stdin = "cat >/dev/null\n" + _GOOD_UV_INSTALLER
    result = _run_installer_without_uv(tmp_path, reads_stdin, piped=True)
    assert result.returncode == 0, result.stderr
    assert "installed-kraft" in result.stdout
