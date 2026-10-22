"""The Seatbelt profile `kind: os` runs a worker under: rule order, quoting,
and what it refuses. Text only; the real sandbox-exec is proven by the e2e tier."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from kraft.worker import host_view
from kraft.worker.backends import seatbelt
from kraft.worker.mounts import Mount
from kraft.worker.sandbox import SandboxRefused

W = Path("/work/tree")


def profile(**kw) -> str:
    args = dict(
        mounts=[],
        read_roots=[],
        read_literals=[],
        denied_reads=[],
        tmpdir=Path("/run/tmp/s"),
        marker=Path("/run/markers/s"),
        relay=False,
    )
    return seatbelt.render_profile(**{**args, **kw})


@pytest.mark.parametrize(
    ("path", "literal"),
    [
        ("/a b", '"/a b"'),
        ('/a"b', '"/a\\"b"'),
        ("/a\\b", '"/a\\\\b"'),
        ("/ünï", '"/ünï"'),
    ],
    ids=["space", "double-quote", "backslash", "non-ascii"],
)
def test_quote(path, literal):
    assert seatbelt.quote(path) == literal
    assert seatbelt.quote(Path(path)) == literal


def test_render_order(tmp_path):
    w = tmp_path / "wt"
    w.mkdir()
    (w / ".git").write_text("gitdir: x")
    text = profile(
        mounts=[Mount(w / ".git", w / ".git", True), Mount(w, w, False)],
        relay=True,
    )
    rw = text.index(f'(allow file-read* file-write* (subpath "{w.resolve()}"))')
    ro = text.index(f'(deny file-write* (literal "{w.resolve()}/.git"))')
    assert rw < ro  # the shallower mount first, whatever order they came in
    pin = f'(deny file-write-unlink (literal "{w.resolve()}"))'
    assert rw < text.index(pin) < ro
    assert text.index("(deny default)") < min(
        text.index(m)
        for m in ("(allow process-exec*", "(allow file-read-metadata", "(allow file-read*")
    )
    assert '(remote ip (string-append "localhost:" (param "RELAY_PORT")))' in text
    assert "RELAY_PORT" not in profile()


def test_render_late_allows_follow_the_mounts(tmp_path):
    """A read-only mount holding the tmpdir (a oneshot shows home read-only) must
    not deny the worker its own tmpdir, marker and socket: last match wins."""
    home = tmp_path.resolve() / "home"
    tmp = home / "run" / "tmp" / "s"
    tmp.mkdir(parents=True)
    text = profile(mounts=[Mount(home, home, True)], tmpdir=tmp, marker=home / "run" / "m")
    ro = text.index(f'(deny file-write* (subpath "{home}"))')
    sub = f'(subpath "{tmp}")'
    for rule in (
        f"(allow file-read* file-write* {sub})",
        f"(allow network-bind network-inbound network-outbound (local unix-socket {sub})",
        f"(allow network-outbound (remote unix-socket {sub})",
        f'(allow file-read-data (literal "{home}/run/m"))',
        '(allow file-write* (literal "/dev/null"))',
    ):
        assert text.index(rule) > ro, rule


def test_render_sorts_mounts_by_their_resolved_depth(tmp_path):
    """`lnk` is two components deep but resolves to four: the ro child written
    through it must still render after its rw parent."""
    root = tmp_path.resolve()
    parent = root / "d1" / "d2" / "d3"
    (parent / "c").mkdir(parents=True)
    (root / "lnk").symlink_to(parent)
    text = profile(
        mounts=[Mount(root / "lnk" / "c", root / "lnk" / "c", True), Mount(parent, parent, False)]
    )
    rw = text.index(f'(allow file-read* file-write* (subpath "{parent}"))')
    assert rw < text.index(f'(deny file-write* (subpath "{parent}/c"))')


def test_render_usr_local_then_roots(monkeypatch):
    monkeypatch.setattr(host_view, "system_excluded", lambda: (Path("/usr/local"),))
    text = profile(read_roots=[Path("/usr/local/opt/python@3.12")])
    deny = text.index('(deny file-read* (subpath "/usr/local"))')
    assert deny < text.index('(allow file-read* (subpath "/usr/local/opt/python@3.12"))')


def test_denied_reads_last():
    text = profile(
        mounts=[Mount(W, W, False)],
        read_literals=[Path("/opt/cli-link")],
        denied_reads=[Path("/opt/real/credentials.toml")],
        relay=True,
    )
    deny = text.index('(deny file-read* (literal "/opt/real/credentials.toml"))')
    assert deny > text.rindex("(allow file-")
    assert deny < text.index("(allow network-outbound (remote ip")


def test_remapped_mount_refused():
    with pytest.raises(SandboxRefused, match="cannot show /a at /b"):
        profile(mounts=[Mount(Path("/a"), Path("/b"), True)])


def test_mach_allowlist_has_no_escape_services():
    banned = (
        "com.apple.SecurityServer",
        "com.apple.securityd",
        "com.apple.coreservices.launchservicesd",
        "com.apple.lsd.",
        "com.apple.coreservices.appleevents",
        "com.apple.pasteboard",
    )
    assert not [s for s in seatbelt.MACH_SERVICES if s.startswith(banned)]


@pytest.mark.e2e("sandbox-exec")
def test_profile_parses(tmp_path):
    prof = tmp_path / "p.sb"
    prof.write_text(profile(relay=True))
    run = subprocess.run(
        ["sandbox-exec", "-f", str(prof), "-D", "RELAY_PORT=1", "/usr/bin/true"],
        capture_output=True,
        text=True,
    )
    assert run.returncode == 0, run.stderr


@pytest.mark.e2e("sandbox-exec")
def test_odd_path_matches_only_itself(tmp_path):
    odd = tmp_path / 'a b"c\\ü'
    odd.mkdir()
    sibling = tmp_path / "a b"
    sibling.mkdir()
    prof = tmp_path / "p.sb"
    prof.write_text(
        profile(mounts=[Mount(odd, odd, False)], tmpdir=tmp_path / "t", marker=tmp_path / "m")
    )

    def write(target: Path):
        return subprocess.run(
            ["sandbox-exec", "-f", str(prof), "/bin/sh", "-c", 'echo x > "$1"', "sh", str(target)],
            capture_output=True,
            text=True,
            cwd="/",
        ).returncode

    assert write(odd / "f") == 0
    assert (odd / "f").read_text() == "x\n"
    assert write(sibling / "f") != 0
    assert not (sibling / "f").exists()


def test_render_pins_the_tmpdir_and_refuses_a_symlinked_rw_mount(tmp_path):
    real = tmp_path.resolve() / "real"
    real.mkdir()
    (tmp_path / "lnk").symlink_to(real)
    tmp = tmp_path.resolve() / "t"
    tmp.mkdir()
    text = profile(tmpdir=tmp)
    allow = text.index(f'(allow file-read* file-write* (subpath "{tmp}"))')
    assert allow < text.index(f'(deny file-write-unlink (literal "{tmp}"))')
    with pytest.raises(SandboxRefused, match="is a symlink"):
        profile(mounts=[Mount(tmp_path / "lnk", tmp_path / "lnk", False)])
    # read-only mounts keep resolving
    profile(mounts=[Mount(tmp_path / "lnk", tmp_path / "lnk", True)])


@pytest.mark.e2e("sandbox-exec")
def test_a_read_write_directory_cannot_be_renamed_away(tmp_path):
    root = tmp_path.resolve()
    outer = root / "outer"  # writable, so only the pin stops `d` being renamed
    d = outer / "d"
    d.mkdir(parents=True)
    (d / "keep").write_text("k")
    tmp = outer / "t"
    tmp.mkdir()
    prof = root / "p.sb"
    prof.write_text(
        profile(
            mounts=[
                Mount(outer, outer, False),
                Mount(d, d, False),
                Mount(d / "keep", d / "keep", True),
            ],
            tmpdir=tmp,
            marker=root / "m",
        )
    )

    def sh(script: str, *args: Path):
        return subprocess.run(
            ["sandbox-exec", "-f", str(prof), "/bin/sh", "-c", script, "sh", *map(str, args)],
            capture_output=True,
            text=True,
            cwd="/",
        ).returncode

    assert sh('mv "$1" "$2"', d, outer / "d2") != 0
    assert d.is_dir() and not (outer / "d2").exists()
    assert sh('mv "$1" "$2"', tmp, outer / "t2") != 0
    assert tmp.is_dir()
    assert sh('echo x > "$1/f" && mv "$1/f" "$1/g"', d) == 0
    assert (d / "g").read_text() == "x\n"
    assert sh('echo x > "$1/keep"', d) != 0
    assert sh('mv "$1/keep" "$1/other"', d) != 0
    assert (d / "keep").read_text() == "k"


@pytest.mark.e2e("sandbox-exec")
@pytest.mark.parametrize(
    "code",
    [
        "import multiprocessing as m; m.Lock().acquire()",
        "import multiprocessing.shared_memory as m; m.SharedMemory(create=True, size=8).unlink()",
    ],
    ids=["lock", "shared-memory"],
)
def test_python_multiprocessing_runs(tmp_path, code):
    prof = tmp_path / "p.sb"
    prof.write_text(profile(read_roots=[Path(sys.base_prefix)], tmpdir=tmp_path / "t"))
    exe = os.path.realpath(sys.executable)
    run = subprocess.run(
        ["sandbox-exec", "-f", str(prof), exe, "-I", "-S", "-c", code],
        capture_output=True,
        text=True,
        cwd="/",
        timeout=60,
    )
    assert run.returncode == 0, run.stderr
