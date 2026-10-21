"""The bubblewrap argv of `kind: os`: what it mounts, in what order, and that
no secret is on it. Argv only; the real bwrap is proven by the e2e tier."""

from __future__ import annotations

import asyncio
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from kraft.paths import RunDirs, kraft_home
from kraft.worker import host_view, mounts, refstore, shim
from kraft.worker.backends import bwrap
from kraft.worker.backends.docker import _ONESHOT_SH, ONESHOT_HOME_RO
from kraft.worker.sandbox import FORWARDED_ENV, SandboxNotReady, SandboxRefused

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


# --- OsBackend ----------------------------------------------------------------------------

_FLAGS = (
    "--size",
    "--disable-userns",
    "--as-pid-1",
    "--cap-drop",
    "--seccomp",
    "--die-with-parent",
)


@pytest.fixture(autouse=True)
def _fresh_probe(monkeypatch):
    monkeypatch.setattr(bwrap, "_probed", None)


@pytest.fixture
def host(tmp_path, monkeypatch):
    """A Linux, non-root host whose `PATH` holds only a fake `bwrap` (its
    `--help` and trial run set by the test), `sh` and `curl`; no sysctl set."""
    bindir = tmp_path / "hostbin"
    bindir.mkdir()
    (bindir / "sh").symlink_to("/bin/sh")
    (bindir / "curl").write_text("#!/bin/sh\n")
    (bindir / "curl").chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir))
    monkeypatch.setattr(bwrap.sys, "platform", "linux")
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    monkeypatch.setattr(bwrap.os_seccomp, "machine", lambda: "x86_64")
    sysctls: dict[str, str] = {}
    monkeypatch.setattr(bwrap, "_sysctl", sysctls.get)

    def fake(flags=_FLAGS, rc=0, err=""):
        script = bindir / "bwrap"
        script.write_text(
            "#!/bin/sh\n"
            f'case "$1" in --help) echo "{" ".join(flags)}";; '
            f'--version) echo "bubblewrap 0.9.0";; *) echo "{err}" >&2; exit {rc};; esac\n'
        )
        script.chmod(0o755)

    return SimpleNamespace(bindir=bindir, sysctls=sysctls, fake=fake)


@pytest.mark.parametrize(
    ("setup", "expect"),
    [
        (lambda h, mp: mp.setattr(bwrap.sys, "platform", "darwin"), "Linux only"),
        (
            lambda h, mp: mp.setattr(os, "geteuid", lambda: 0),
            "does not run while Kraft runs as root",
        ),
        (lambda h, mp: mp.setattr(bwrap.os_seccomp, "machine", lambda: "riscv64"), "not riscv64"),
        (lambda h, mp: (h.bindir / "bwrap").unlink(), "bubblewrap is not installed"),
        (lambda h, mp: (h.bindir / "sh").unlink(), "sh is not installed"),
        (lambda h, mp: (h.bindir / "curl").unlink(), "curl is not installed"),
        (lambda h, mp: h.fake(flags=("--die-with-parent",)), "--size, --disable-userns"),
        (
            lambda h, mp: (h.fake(rc=1), h.sysctls.update({"user/max_user_namespaces": "0"})),
            "sysctl user.max_user_namespaces=15000",
        ),
        (
            lambda h, mp: (
                h.fake(rc=1),
                h.sysctls.update({"kernel/unprivileged_userns_clone": "0"}),
            ),
            "sysctl kernel.unprivileged_userns_clone=1",
        ),
        (
            lambda h, mp: (
                h.fake(rc=1),
                h.sysctls.update({"kernel/apparmor_restrict_unprivileged_userns": "1"}),
            ),
            "kernel.apparmor_restrict_unprivileged_userns=0",
        ),
        (
            lambda h, mp: (
                h.fake(rc=0),
                h.sysctls.update({"kernel/apparmor_restrict_unprivileged_userns": "1"}),
            ),
            None,
        ),
        (lambda h, mp: h.fake(rc=1, err="bwrap: no way"), "bwrap: no way"),
    ],
    ids=[
        "not-linux",
        "root",
        "arch",
        "no-bwrap",
        "no-sh",
        "no-curl",
        "missing-flag",
        "max-userns-0",
        "userns-clone-0",
        "apparmor",
        "apparmor-but-trial-passes",
        "trial-fails",
    ],
)
def test_probe_explains(host, monkeypatch, setup, expect):
    host.fake()
    setup(host, monkeypatch)
    problem = bwrap.probe_host(refresh=True)
    if expect is None:
        assert problem is None
    else:
        assert expect in problem


async def test_open_session_env():
    backend = bwrap.OsBackend()
    assert await backend.egress_transport() == "unix"
    assert await backend.open_session("s1", SANDBOX, None, work_item_id="w1") == {
        **mounts.RELAY_PROXY_ENV,
        "PATH": f"/opt/kraft/bin:{os.environ['PATH']}",
    }


@pytest.mark.parametrize(
    ("content", "failed"),
    [(None, True), ("", True), ("started\n", False)],
    ids=["missing", "empty", "started"],
)
def test_launch_failed(tmp_path, content, failed):
    cid = tmp_path / "cid"
    if content is not None:
        cid.write_text(content)
    assert bwrap.OsBackend().launch_failed(cid) is failed


def test_wrap_without_a_channel_is_refused(scene, run_dirs, monkeypatch):
    monkeypatch.setenv("KRAFT_RUN_DIR", str(run_dirs.base))
    with pytest.raises(SandboxRefused, match="egress channel"):
        bwrap.OsBackend().wrap(
            ["true"], scene.wt, SANDBOX, None, session_id="s1", work_item_id="w1"
        )


async def test_wrap_finds_socket_by_ids(scene, run_dirs, channels, monkeypatch):
    monkeypatch.setenv("KRAFT_RUN_DIR", str(run_dirs.base))
    argv = bwrap.OsBackend().wrap(
        ["true"], scene.wt, SANDBOX, None, session_id="s1", work_item_id="w1"
    )
    sock = channels.socket_path("s1", work_item_id="w1")
    assert _at(argv, ("--sock", str(sock), "--port", "3128")) > 0


def test_oneshot_argv(scene, run_dirs, tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_RUN_DIR", str(run_dirs.base))
    monkeypatch.setattr(bwrap, "probe_host", lambda **kw: None)
    home = tmp_path / "item-home"
    home.mkdir()
    shot = bwrap.OsBackend().oneshot(SANDBOX, scene.wt, home, executables=(str(scene.cli),))
    argv = shot.argv()
    assert _at(argv, (scene.interp, "-I", "-S", scene.init, "oneshot", "--keep")) == 0
    for frag in [
        ("--ro-bind", str(scene.wt), str(scene.wt)),
        ("--ro-bind", str(home), ONESHOT_HOME_RO),
        ("--ro-bind", str(scene.real), str(scene.real)),
        ("--setenv", "HOME", "/tmp/home"),
        ("--setenv", "PATH", f"/opt/kraft/bin:{os.environ['PATH']}"),
        (
            scene.interp,
            "-I",
            "-S",
            scene.init,
            "init",
            "--",
            "sh",
            "-c",
            _ONESHOT_SH,
            ONESHOT_HOME_RO,
        ),
    ]:
        assert _at(argv, frag) > 0, frag
    assert "--started" not in argv and "--sock" not in argv
    assert argv[-4:] == ["sh", "-c", _ONESHOT_SH, ONESHOT_HOME_RO]
    assert asyncio.run(shot.close()) is None


def test_oneshot_not_ready_where_the_host_cannot(scene, monkeypatch):
    monkeypatch.setattr(bwrap, "probe_host", lambda **kw: "no bwrap here")
    with pytest.raises(SandboxNotReady, match="no bwrap here"):
        bwrap.OsBackend().oneshot(SANDBOX, scene.wt, scene.wt.parent)


def test_os_leaves_item_sync_to_docker(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(refstore, "sync_item", lambda *a: called.append(a) or ["twice"])
    assert bwrap.OsBackend().code_out_item(tmp_path, "w1") == []
    assert called == []


@pytest.mark.parametrize(
    ("executable", "expect"),
    [
        ("nosuchcmd-anywhere", "not found"),
        ("sh", None),
        ("./gradlew", None),
        ("/home/x/.kraft/run/items/w1/worktree/tool", None),
    ],
    ids=[
        "bare-name-not-found",
        "bare-name-found",
        "checkout-relative-not-probed",
        "worktree-path-not-probed",
    ],
)
async def test_probe_resolves_only_a_bare_name(monkeypatch, executable, expect):
    monkeypatch.setattr(bwrap, "probe_host", lambda **kw: None)
    problem = await bwrap.OsBackend().probe(SANDBOX, executable, {"PATH": "/usr/bin:/bin"})
    assert (problem is None) if expect is None else (expect in problem)


def test_probe_host_is_cached_for_a_minute_unless_refreshed(monkeypatch):
    runs, now = [], [1000.0]
    monkeypatch.setattr(bwrap, "_probe", lambda: runs.append(1))
    monkeypatch.setattr(bwrap.time, "monotonic", lambda: now[0])
    bwrap.probe_host()
    now[0] += 1
    bwrap.probe_host()
    assert len(runs) == 1
    bwrap.probe_host(refresh=True)
    assert len(runs) == 2
    now[0] += bwrap._PROBE_TTL + 1
    bwrap.probe_host()
    assert len(runs) == 3
