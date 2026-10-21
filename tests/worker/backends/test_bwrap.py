"""The bubblewrap argv of `kind: os`: what it mounts, in what order, and that
no secret is on it. Argv only; the real bwrap is proven by the e2e tier."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from kraft.paths import RunDirs, kraft_home
from kraft.worker import host_view, mounts, shim
from kraft.worker.backends import bwrap
from kraft.worker.sandbox import FORWARDED_ENV, SandboxRefused

SANDBOX = {"kind": "os", "network": {"runtime": {"allow": ["a.io"]}}}


@pytest.fixture
def scene(tmp_path, run_dirs, monkeypatch):
    """A checkout, a fake `bwrap` first on PATH, a CLI reached through a
    symlink with a credential file beside it, and a socket directory."""
    wt = tmp_path / "wt"
    wt.mkdir()
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    (fakebin / "bwrap").write_text("#!/bin/sh\n")
    (fakebin / "bwrap").chmod(0o755)
    monkeypatch.setenv("PATH", f"{fakebin}:{os.environ['PATH']}")
    real = tmp_path / "opt" / "real"
    (real / "bin").mkdir(parents=True)
    cli = real / "bin" / "cli"
    cli.write_text("#!/bin/sh\n")
    cli.chmod(0o755)
    (real / "credentials.toml").write_text("secret")
    link = tmp_path / "opt" / "cli-link"
    link.symlink_to(cli)
    tool = wt / "tool"
    tool.symlink_to(cli)
    sockdir = tmp_path / "chan"
    sockdir.mkdir()
    interp, _ = host_view.init_interpreter()

    def build(**kw):
        args = {
            "cmd": ["true"],
            "cwd": wt,
            "sandbox": SANDBOX,
            "session": mounts.session_mounts(wt, None),
            "env": {"PATH": "/opt/kraft/bin:/usr/bin"},
            "passthrough": (),
            "home": None,
            "workdir": None,
            "sock_path": None,
            "started": None,
            "search_path": "/usr/bin:/bin",
            "run_dirs": run_dirs,
        }
        return bwrap.bwrap_argv(**{**args, **kw})

    return SimpleNamespace(
        wt=wt,
        real=real,
        cli=cli,
        link=link,
        tool=tool,
        sockdir=sockdir,
        interp=str(interp),
        init=str(Path(bwrap.os_init.__file__)),
        seccomp=str(bwrap.seccomp_file(run_dirs)),
        bwrap=str(fakebin / "bwrap"),
        build=build,
    )


def _at(argv, frag):
    """Index of the first place `frag` appears contiguously in `argv`, or -1."""
    n = len(frag)
    return next((i for i in range(len(argv) - n + 1) if tuple(argv[i : i + n]) == frag), -1)


def _row(kw, frags, absent=()):
    return (kw, frags, lambda s: list(absent(s)) if callable(absent) else list(absent))


@pytest.mark.parametrize(
    ("kw", "frags", "absent"),
    [
        _row(lambda s: {}, lambda s: [("--unshare-all",), ("--die-with-parent",)]),
        _row(lambda s: {}, lambda s: [("--unshare-all",), ("--disable-userns",)]),
        _row(lambda s: {}, lambda s: [("--unshare-all",), ("--cap-drop", "ALL")]),
        _row(
            lambda s: {},
            lambda s: [("--unshare-all",), ("--as-pid-1",), ("--ro-bind", "/usr", "/usr")],
        ),
        _row(
            lambda s: {},
            lambda s: [
                ("--proc", "/proc"),
                ("--dev", "/dev"),
                ("--size", "2147483648", "--tmpfs", "/tmp"),
                ("--bind", str(s.wt), str(s.wt)),
            ],
        ),
        _row(
            lambda s: {},
            lambda s: [
                ("--size", "268435456", "--tmpfs", "/dev/shm"),
                ("--symlink", "/tmp", "/var/tmp"),
            ],
        ),
        _row(lambda s: {}, lambda s: [("--", s.bwrap, "--unshare-all")]),
        _row(
            lambda s: {"started": s.wt.parent / "cid"},
            lambda s: [
                (s.interp, "-I", "-S", s.init, "launch", "--keep"),
                ("--seccomp-file", s.seccomp, "--started", str(s.wt.parent / "cid"), "--"),
            ],
        ),
        _row(
            lambda s: {"mode": "oneshot", "started": s.wt.parent / "cid"},
            lambda s: [(s.interp, "-I", "-S", s.init, "oneshot", "--keep")],
            lambda s: [("--started", str(s.wt.parent / "cid"))],
        ),
        _row(
            lambda s: {"sock_path": s.sockdir / "s.sock", "home": s.wt.parent / "h"},
            lambda s: [
                ("--ro-bind", str(shim.HOST_DIR), "/opt/kraft/bin"),
                ("--ro-bind", str(s.sockdir), str(s.sockdir)),
                ("--bind", str(s.wt.parent / "h"), str(s.wt.parent / "h")),
                ("--setenv", "HOME", str(s.wt.parent / "h")),
                ("--chdir", str(s.wt)),
                (s.interp, "-I", "-S", s.init, "init", "--sock", str(s.sockdir / "s.sock")),
                ("--port", "3128", "--", "true"),
            ],
            lambda s: [("--bind", str(s.sockdir), str(s.sockdir))],
        ),
        _row(
            lambda s: {},
            lambda s: [("--setenv", "HOME", "/tmp/home")],
            lambda s: [("--bind", "/tmp/home", "/tmp/home")],
        ),
        _row(
            lambda s: {
                "session": [
                    mounts.Mount(s.wt.parent / "p" / "c", s.wt.parent / "p" / "c", False),
                    mounts.Mount(s.wt.parent / "p", s.wt.parent / "p", True),
                ]
            },
            lambda s: [
                ("--ro-bind", str(s.wt.parent / "p"), str(s.wt.parent / "p")),
                ("--bind", str(s.wt.parent / "p" / "c"), str(s.wt.parent / "p" / "c")),
            ],
        ),
        _row(
            lambda s: {"cmd": [str(s.link)]},
            lambda s: [
                ("--ro-bind", str(s.real), str(s.real)),
                ("--ro-bind", "ZERO", str(s.real / "credentials.toml")),
                ("--symlink", str(s.cli), str(s.link)),
            ],
        ),
        _row(
            lambda s: {"agent": str(s.cli), "cmd": [str(s.tool)]},
            lambda s: [("--ro-bind", str(s.real), str(s.real))],
        ),
    ],
    ids=[
        "die-with-parent",
        "no-nested-userns",
        "cap-drop",
        "as-pid-1",
        "system-before-session",
        "shm-capped",
        "bwrap-absolute",
        "launcher-prefix",
        "oneshot-no-started",
        "socket-dir-ro",
        "default-home",
        "session-sorted-by-depth",
        "link-and-cred-shadow",
        "agent-roots-visible-to-command",
    ],
)
def test_argv_has(scene, kw, frags, absent):
    argv = scene.build(**kw(scene))
    at = -1
    for frag in frags(scene):
        if "ZERO" in frag:  # the zero-byte file Kraft made, wherever it is
            i = argv.index(frag[2]) - 1
            frag = (frag[0], argv[i], frag[2])
            assert stat.S_IMODE(os.stat(argv[i]).st_mode) == 0o444
            assert os.stat(argv[i]).st_size == 0
        assert (i := _at(argv, frag)) > at, f"{frag} missing or out of order in {argv}"
        at = i
    for frag in absent(scene):
        assert _at(argv, frag) == -1


def test_system_dirs_are_as_the_host_has_them():
    args = bwrap.system_args()
    assert args[-3:] == ["--symlink", "/tmp", "/var/tmp"]
    for p in ("/bin", "/sbin", "/lib", "/lib64"):
        if Path(p).is_symlink():
            assert ["--symlink", os.readlink(p), p] == args[args.index(p) - 2 : args.index(p) + 1]


@pytest.mark.parametrize(
    "kw",
    [
        lambda s: {"sandbox": {**SANDBOX, "expose": ["/does/not/exist"]}},
        lambda s: {"sandbox": {**SANDBOX, "expose": ["~/.ssh"]}},
        lambda s: {"cmd": [str(s.tool)]},
    ],
    ids=["expose-missing-refused", "expose-protected-refused", "checkout-link-outside-view"],
)
def test_refusals_raise_sandbox_refused(scene, kw):
    with pytest.raises(SandboxRefused, match="expose"):
        scene.build(**kw(scene))


def test_unknown_architecture_is_refused(run_dirs, monkeypatch):
    monkeypatch.setattr(bwrap.os_seccomp, "machine", lambda: "riscv64")
    with pytest.raises(SandboxRefused, match="riscv64"):
        bwrap.seccomp_file(run_dirs)


def test_session_only_from_kraft_home(scene, monkeypatch):
    """Of `$KRAFT_HOME`, only the session's mounts, its home, the socket
    directory and the shim are bound: never the session folder or the
    seccomp file."""
    kh = kraft_home()
    dirs = RunDirs(kh / "run").ensure()
    item = kh / "run" / "items" / "w1"
    folder, home = item / "sessions" / "s1", item / "home"
    result = item / "sessions" / "s1.json"
    for d in (folder, home):
        d.mkdir(parents=True)
    sockdir = kh / "run" / "chan" / "w1"
    sockdir.mkdir(parents=True)
    session = mounts.session_mounts(scene.wt, None, result_path=result, rw_dirs=[item / "docs"])
    argv = scene.build(
        session=session,
        run_dirs=dirs,
        home=home,
        sock_path=sockdir / "s.sock",
        started=folder / "c",
    )
    sources = {argv[i + 1] for i, a in enumerate(argv) if a in ("--bind", "--ro-bind")}
    under = {s for s in sources if Path(s).is_relative_to(kh)}
    allowed = {str(m.src) for m in session} | {str(home), str(sockdir), str(shim.HOST_DIR)}
    assert under <= allowed
    assert str(folder) not in sources
    assert str(bwrap.seccomp_file(dirs)) not in sources
    assert not Path(bwrap.seccomp_file(dirs)).stat().st_mode & 0o077


def test_no_secret_on_argv(scene, monkeypatch):
    secrets = {"ANTHROPIC_API_KEY": "sk-ant-s3cret", "MY_TOKEN": "tok-s3cret"}
    secrets |= {name: f"v-{name}" for name in FORWARDED_ENV}
    for name, value in secrets.items():
        monkeypatch.setenv(name, value)
    argv = scene.build(passthrough=["MY_TOKEN"])
    assert not [a for a in argv for v in secrets.values() if v in a]
    keep = argv[argv.index("--keep") + 1].split(",")
    assert {"MY_TOKEN", "ANTHROPIC_API_KEY", *FORWARDED_ENV} <= set(keep)
