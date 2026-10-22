"""The Seatbelt profile `kind: os` runs a worker under: rule order, quoting,
and what it refuses. Text only; the real sandbox-exec is proven by the e2e tier."""

from __future__ import annotations

import errno
import fcntl
import os
import pty
import re
import select
import subprocess
import sys
import termios
import tty
from pathlib import Path

import pytest
from support.harness import git

from kraft.worker import host_view, refstore
from kraft.worker.backends import seatbelt
from kraft.worker.mounts import Mount, session_mounts
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
    # Writes too: renaming it inside a read-write tree would let the copy be read.
    deny = text.index('(deny file-read* file-write* (literal "/opt/real/credentials.toml"))')
    assert deny > text.rindex("(allow file-")
    assert deny < text.index("(allow network-outbound (remote ip")


def test_remapped_mount_refused():
    with pytest.raises(SandboxRefused, match="cannot show /a at /b"):
        profile(mounts=[Mount(Path("/a"), Path("/b"), True)])


def test_dev_is_read_by_name_never_whole():
    """`/dev` whole holds the operator's other terminals: reading one is their keystrokes."""
    text = profile()
    assert '(subpath "/dev")' not in text
    for name in ("null", "zero", "random", "urandom", "dtracehelper"):
        assert f'(allow file-read* (literal "/dev/{name}"))' in text
    # Rendered resolved: a symlink on Linux, the directory itself on a Mac.
    assert f'(allow file-read* (subpath "{os.path.realpath("/dev/fd")}"))' in text
    # And what the `git` shim through Xcode.app needs outside `/dev`.
    assert '(allow file-read* (subpath "/Library/Preferences/com.apple.dt.Xcode.plist"))' in text
    framework = "/Library/Apple/System/Library/PrivateFrameworks/MobileDevice.framework"
    assert f'(allow file-read* (subpath "{framework}"))' in text


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


def _real_mounts(root: Path, rw: list[str], ro: list[str]) -> list[Mount]:
    mounts = []
    for rel, is_ro in [(r, False) for r in rw] + [(r, True) for r in ro]:
        path = root / rel
        path.mkdir(parents=True, exist_ok=True)
        mounts.append(Mount(path, path, is_ro))
    return mounts


@pytest.mark.parametrize(
    ("rw", "ro", "refused"),
    [
        (["a"], ["a/x/r"], "a/x"),
        (["a"], ["a/x/y/r"], "a/x/y"),
        (["a", "a/x"], ["a/x/r"], None),
        (["a"], ["a/r"], None),
        (["a"], ["b/x/r"], None),
    ],
    ids=["two-below", "three-below", "intermediate-mounted", "one-below", "elsewhere"],
)
def test_a_read_only_carve_out_needs_its_path_pinned(tmp_path, rw, ro, refused):
    """Renaming an unmounted directory between a writable mount and a read-only one
    inside it would carry the carve-out away."""
    root = tmp_path.resolve()
    mounts = _real_mounts(root, rw, ro)
    if refused is None:
        profile(mounts=mounts)
        return
    want = f"{root / ro[0]} read-only: {root / refused},"
    with pytest.raises(SandboxRefused, match=re.escape(want)):
        profile(mounts=mounts)


def test_the_mounts_of_a_direct_store_render(repo, tmp_path):
    """Slice 3 renders what `session_mounts` emits for a direct store: the common gitdir
    read-only with `objects` read-write and `objects/info` read-only inside it."""
    wt = tmp_path / "wt"
    git(repo, "worktree", "add", "-q", "-b", "kraft/item-1", str(wt))
    stores = refstore.prepare_direct(tmp_path / "run", wt, "kraft/item-1")
    text = profile(mounts=session_mounts(wt, [], refstores=stores), tmpdir=tmp_path / "t")
    common = (repo / ".git").resolve()
    assert f'(deny file-write* (subpath "{common}"))' in text
    assert f'(allow file-read* file-write* (subpath "{common}/objects"))' in text
    assert f'(deny file-write* (subpath "{common}/objects/info"))' in text


def _sandbox_argv(tmp_path: Path, argv: list[str], *, mounts=(), **kw) -> list[str]:
    prof = tmp_path / "p.sb"
    prof.write_text(
        profile(
            mounts=list(mounts),
            read_roots=[Path(sys.base_prefix)],
            tmpdir=tmp_path / "t",
            marker=tmp_path / "m",
            **kw,
        )
    )
    return ["sandbox-exec", "-f", str(prof), *argv]


def _sandboxed(tmp_path: Path, argv: list[str], *, mounts=(), cwd="/", **kw):
    return subprocess.run(
        _sandbox_argv(tmp_path, argv, mounts=mounts, **kw),
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=60,
    )


_PY = [os.path.realpath(sys.executable), "-I", "-S", "-c"]
_OPEN = "import os, sys; os.close(os.open(sys.argv[1], os.O_RDONLY | os.O_NONBLOCK | os.O_NOCTTY))"


@pytest.mark.e2e("sandbox-exec")
@pytest.mark.parametrize(
    "argv",
    [
        [*_PY, "import os, ssl; ssl.create_default_context(); assert len(os.urandom(8)) == 8"],
        ["/bin/bash", "-c", 'test "$(cat <(echo x))" = x'],
    ],
    ids=["python-ssl-urandom", "process-substitution"],
)
def test_naming_the_dev_files_keeps_ordinary_tools_working(tmp_path, argv):
    run = _sandboxed(tmp_path, argv)
    assert run.returncode == 0, run.stderr


@pytest.mark.e2e("sandbox-exec")
def test_a_certificate_verifies_through_the_systems_trust_evaluation(tmp_path):
    """Security.framework (a Go CLI's TLS, `security verify-cert`) asks trustd to evaluate
    a chain; with the service not allowed, a root the host trusts fails to verify."""
    pems = re.findall(
        r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----\n",
        Path("/etc/ssl/cert.pem").read_text(),
        re.S,
    )
    certs = tmp_path.resolve() / "certs"
    certs.mkdir()
    verify = ["/usr/bin/security", "verify-cert", "-p", "basic", "-L", "-c"]
    for i, pem in enumerate(pems[:20]):
        (certs / f"{i}.pem").write_text(pem)
        if subprocess.run([*verify, str(certs / f"{i}.pem")], capture_output=True).returncode == 0:
            break
    else:
        pytest.fail("the host trusts none of the first 20 roots in /etc/ssl/cert.pem")

    run = _sandboxed(
        tmp_path, [*verify, str(certs / f"{i}.pem")], mounts=[Mount(certs, certs, True)]
    )

    assert run.returncode == 0, run.stdout + run.stderr


@pytest.mark.e2e("sandbox-exec")
def test_a_commit_runs_with_dev_read_by_name(tmp_path):
    work = tmp_path.resolve() / "work"
    work.mkdir()
    git(work, "init", "-q")
    run = _sandboxed(
        tmp_path,
        ["/usr/bin/git", "-c", "user.name=a", "-c", "user.email=a@b", "commit", "-q"]
        + ["--allow-empty", "-m", "x"],
        mounts=[Mount(work, work, False)],
        cwd=work,
    )
    assert run.returncode == 0, run.stderr
    assert git(work, "log", "--format=%s") == "x"


@pytest.mark.e2e("sandbox-exec")
@pytest.mark.parametrize("target", ["tty", "null"])
def test_the_operators_other_terminals_cannot_be_opened(tmp_path, target):
    """Only the by-name devices open under the profile; a terminal the operator owns
    opens outside it, so the row proves the profile and not a permission."""
    if target == "null":
        path = "/dev/null"
    else:
        mine = [p for p in sorted(Path("/dev").glob("ttys*")) if p.stat().st_uid == os.getuid()]
        if not mine:
            pytest.skip("this machine has no terminal the user owns under /dev/ttys*")
        path = str(mine[0])
        outside = subprocess.run([*_PY, _OPEN, path], capture_output=True, text=True)
        assert outside.returncode == 0, outside.stderr
    run = _sandboxed(tmp_path, [*_PY, _OPEN, path])
    assert (run.returncode == 0) == (target == "null"), run.stderr


@pytest.mark.e2e("sandbox-exec")
@pytest.mark.parametrize("sandboxed", [False, True], ids=["control-outside", "refused-inside"])
def test_tiocsti_cannot_type_into_a_controlling_terminal(tmp_path, sandboxed):
    """Outside the sandbox the ioctl puts a character into the terminal's input; inside it
    is refused. The control run is what shows the terminal is one that takes the injection.
    The process stays alive after the ioctl: a session leader's exit hangs the terminal up
    and flushes what was typed."""
    master, slave = pty.openpty()
    tty.setraw(slave)
    code = (
        "import fcntl, sys, termios, time; fcntl.ioctl(0, termios.TIOCSTI, b'x'); "
        "print('injected', flush=True); time.sleep(30)"
    )
    argv = [*_PY, code]
    if sandboxed:
        argv = _sandbox_argv(tmp_path, argv)
    proc = subprocess.Popen(
        argv,
        stdin=slave,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
        preexec_fn=lambda: fcntl.ioctl(0, termios.TIOCSCTTY, 0),
    )
    try:
        said = proc.stdout.readline()
        try:
            typed = os.read(slave, 1) if select.select([slave], [], [], 1)[0] else b""
        except OSError as exc:  # EIO: a refused run exited, which hung the terminal up
            if exc.errno != errno.EIO:
                raise
            typed = b""
    finally:
        proc.kill()
        proc.wait()
        os.close(master)
        os.close(slave)
    assert (said.strip(), typed) == (("", b"") if sandboxed else ("injected", b"x")), (
        proc.stderr.read()
    )


@pytest.mark.e2e("sandbox-exec")
@pytest.mark.parametrize("how", ["rename", "read"])
def test_a_denied_credential_in_a_writable_tree_can_be_neither_renamed_nor_read(tmp_path, how):
    tree = tmp_path.resolve() / "tree"
    tree.mkdir()
    cred = tree / "credentials"
    cred.write_text("secret")
    script = 'mv "$1" "$1.x"' if how == "rename" else 'cat "$1"'
    run = _sandboxed(
        tmp_path,
        ["/bin/sh", "-c", script, "sh", str(cred)],
        mounts=[Mount(tree, tree, False)],
        denied_reads=[cred],
    )
    assert run.returncode != 0
    assert cred.read_text() == "secret" and not (tree / "credentials.x").exists()
