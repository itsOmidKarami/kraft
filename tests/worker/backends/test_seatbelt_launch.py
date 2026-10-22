"""The launch `kind: os` builds on a Mac: `seatbelt_argv` and the profile it
writes, `OsBackend`'s darwin branches (wrap, oneshot, close, sweep, the direct
branch checks), and the probe. Argv and text on any host, with the platform
patched; the e2e rows run the real sandbox-exec."""

from __future__ import annotations

import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from support.harness import git

from kraft.worker import host_view, os_init, shim
from kraft.worker.backends import bwrap, seatbelt
from kraft.worker.backends.docker import _ONESHOT_SH
from kraft.worker.mounts import session_mounts

BRANCH = "kraft/item-1"
SANDBOX = {"kind": "os", "network": {"runtime": {"allow": ["a.io"]}}}


@pytest.fixture
def mac(tmp_path, run_dirs, monkeypatch):
    """A Mac as `OsBackend` sees one, on any host: darwin, nothing in the way of a
    sandbox, the run dir it reads, and a checkout with a CLI behind a symlink and a
    credential file beside it."""
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setenv("KRAFT_RUN_DIR", str(run_dirs.base))
    interp = Path(os.path.realpath(sys.executable)), (Path(sys.base_prefix).resolve(),)
    monkeypatch.setattr(host_view, "init_interpreter", lambda: interp)
    system = tuple(Path(p) for p in ("/usr", "/bin", "/sbin", "/System"))
    monkeypatch.setattr(host_view, "system_dirs", lambda: system)
    monkeypatch.setattr(host_view, "system_excluded", lambda: (Path("/usr/local"),))
    monkeypatch.setattr(bwrap, "probe_host", lambda **kw: None)
    wt = tmp_path / "wt"
    (wt / "sub").mkdir(parents=True)
    real = tmp_path / "opt" / "real"
    (real / "bin").mkdir(parents=True)
    cli = real / "bin" / "cli"
    cli.write_text("#!/bin/sh\n")
    cli.chmod(0o755)
    (real / "credentials.toml").write_text("secret")
    link = tmp_path / "opt" / "cli-link"
    link.symlink_to(cli)
    (tmp_path / "h").mkdir()

    def build(**kw):
        args = {
            "cmd": ["true"],
            "cwd": wt,
            "sandbox": SANDBOX,
            "session": session_mounts(wt, None),
            "env": {"PATH": f"{shim.HOST_DIR}:/usr/bin"},
            "passthrough": (),
            "home": None,
            "workdir": None,
            "sock_path": None,
            "started": None,
            "search_path": "/usr/bin:/bin",
            "run_dirs": run_dirs,
            "key": "w1-s1",
        }
        return seatbelt.seatbelt_argv(**{**args, **kw})

    return SimpleNamespace(
        wt=wt,
        real=real,
        cli=cli,
        link=link,
        home=tmp_path / "h",
        build=build,
        files=lambda key="w1-s1": seatbelt.session_files(run_dirs.base, key),
        interp=str(interp[0]),
    )


def _launch(argv):
    """`argv` as the launcher parses it: its flags, and the command after `--`."""
    assert argv[1:4] == ["-I", "-S", os_init.__file__] and argv[4] in ("launch", "oneshot")
    return os_init._parse(argv[5:], os_init.LAUNCH_FLAGS, repeat=("--setenv",))


@pytest.mark.parametrize(
    ("kw", "check"),
    [
        (
            lambda m: {"started": m.wt.parent / "cid"},
            lambda m, o: o["--started"] == str(m.wt.parent / "cid") and "--relay-sock" not in o,
        ),
        (
            lambda m: {"sock_path": m.wt.parent / "s.sock"},
            lambda m, o: o["--relay-sock"] == str(m.wt.parent / "s.sock"),
        ),
        (
            lambda m: {"mode": "oneshot", "started": m.wt.parent / "cid"},
            lambda m, o: "--started" not in o,
        ),
        (lambda m: {}, lambda m, o: f"HOME={m.files().tmpdir / 'home'}" in o["--setenv"]),
        (lambda m: {"home": m.home}, lambda m, o: f"HOME={m.home}" in o["--setenv"]),
        (
            lambda m: {"env": {"PATH": "/x", "FOO": "a=b"}},
            lambda m, o: {"PATH=/x", "FOO=a=b"} <= set(o["--setenv"]),
        ),
        (lambda m: {}, lambda m, o: o["--marker"] == str(m.files().marker)),
        (lambda m: {}, lambda m, o: o["--control"] == str(m.files().control)),
        (lambda m: {}, lambda m, o: o["--tmpdir"] == str(m.files().tmpdir)),
    ],
    ids=[
        "started",
        "relay-only-with-a-socket",
        "oneshot-no-started",
        "default-home",
        "own-home",
        "env",
        "marker",
        "control",
        "tmpdir",
    ],
)
def test_the_launcher_is_told(mac, kw, check):
    opts, rest = _launch(mac.build(**kw(mac)))
    assert check(mac, opts)
    f = mac.files()
    # What the launcher runs: sandbox-exec on this launch's profile, then the init.
    assert rest == [
        seatbelt.SANDBOX_EXEC,
        *("-f", str(f.profile)),
        *(mac.interp, "-I", "-S", os_init.__file__, "init"),
        *("--chdir", str(mac.wt.resolve()), "--", "true"),
    ]


def test_the_init_starts_in_the_workdir(mac):
    _, rest = _launch(mac.build(workdir=mac.wt / "sub"))
    assert rest[9] == str(mac.wt.resolve() / "sub")


def test_the_profile_of_a_launch(mac):
    mac.build(cmd=[str(mac.link)], home=mac.home, sock_path=mac.wt.parent / "s")
    f = mac.files()
    text = f.profile.read_text()
    assert stat.S_IMODE(f.profile.stat().st_mode) == 0o600
    assert stat.S_IMODE(f.profile.parent.stat().st_mode) == 0o700
    for rule in [
        "(deny default)",
        f'(allow file-read* (subpath "{shim.HOST_DIR.resolve()}"))',
        f'(allow file-read* (subpath "{mac.real.resolve()}"))',
        f'(deny file-read* file-write* (literal "{mac.real.resolve()}/credentials.toml"))',
        f'(allow file-read* file-write* (subpath "{mac.wt.resolve()}"))',
        f'(allow file-read* file-write* (subpath "{mac.home.resolve()}"))',
        f'(allow file-read-data (literal "{f.marker}"))',
        '(param "RELAY_PORT")',
    ]:
        assert rule in text, rule
    # Its own marker alone: the whole directory would show it the control marker too,
    # and the sweep would take every sandbox for the session's.
    assert f'"{f.marker.parent}"' not in text and str(f.control) not in text


def test_no_secret_on_argv(mac, monkeypatch):
    secrets = {"ANTHROPIC_API_KEY": "sk-ant-s3cret", "MY_TOKEN": "tok-s3cret"}
    for name, value in secrets.items():
        monkeypatch.setenv(name, value)
    argv = mac.build(passthrough=["MY_TOKEN"])
    assert not [a for a in argv for v in secrets.values() if v in a]
    assert {"MY_TOKEN", "ANTHROPIC_API_KEY"} <= set(_launch(argv)[0]["--keep"].split(","))


@pytest.mark.parametrize(
    ("kw", "name"),
    [
        ({"session_id": "s1", "work_item_id": "w1"}, "w1-s1"),
        ({"session_id": "a" * 32, "work_item_id": "w1"}, "a{32}"),
        ({}, "setup-[0-9a-f]{32}"),
    ],
    ids=["session", "legacy-session", "no-session"],
)
def test_wrap_names_its_files_by_session(mac, kw, name):
    argv = bwrap.OsBackend().wrap(["true"], mac.wt, {"kind": "os"}, None, **kw)
    assert re.fullmatch(name, Path(_launch(argv)[0]["--marker"]).name)


async def test_open_session_on_a_mac_sets_only_the_path(mac):
    """The launcher mints the relay's port and token and sets the proxy names itself."""
    env = await bwrap.OsBackend().open_session("s1", SANDBOX, None, work_item_id="w1")
    assert env == {"PATH": f"{shim.HOST_DIR}:{os.environ['PATH']}"}


async def test_health_names_seatbelt(mac):
    ok, what = await bwrap.OsBackend().health(SANDBOX)
    assert ok and what.startswith("Seatbelt (sandbox-exec), macOS ")


# --- oneshot, close and sweep --------------------------------------------------------------------


@pytest.fixture
def sweeps(monkeypatch):
    """`os_init.sweep` replaced: which markers it was asked about, the pids it
    answers are left, and an error to raise."""
    calls = SimpleNamespace(asked=[], left=[], error=None)

    def sweep(marker, control):
        calls.asked.append(Path(marker).name)
        if calls.error:
            raise calls.error
        if not calls.left:
            os.unlink(marker)
        return calls.left

    monkeypatch.setattr(os_init, "sweep", sweep)
    return calls


def _made(mac, key):
    """What a launch leaves behind: its marker, temporary directory and profile."""
    f = mac.files(key)
    for d in (f.marker.parent, f.tmpdir, f.profile.parent):
        d.mkdir(parents=True, exist_ok=True)
    f.marker.write_text("")
    f.profile.write_text("")
    return f


async def test_oneshot_shows_its_home_at_its_own_path_and_closes_clean(mac, sweeps):
    shot = bwrap.OsBackend().oneshot(SANDBOX, mac.wt, mac.home, executables=(str(mac.cli),))
    opts, rest = _launch(shot.argv())
    f = mac.files(shot.key)
    assert shot.key.startswith("oneshot-") and opts["--marker"] == str(f.marker)
    assert rest[-4:] == ["sh", "-c", _ONESHOT_SH, str(mac.home)]
    assert "--started" not in opts and "--relay-sock" not in opts
    assert f"HOME={f.tmpdir / 'home'}" in opts["--setenv"]
    text = f.profile.read_text()
    assert f'(deny file-write* (subpath "{mac.home.resolve()}"))' in text
    assert f'(allow file-read* (subpath "{mac.real.resolve()}"))' in text
    _made(mac, shot.key)

    await shot.close()

    assert sweeps.asked == [shot.key] and not f.tmpdir.exists() and not f.profile.exists()


@pytest.mark.parametrize(
    ("left", "error", "kept", "logged"),
    [
        ([], None, False, ""),
        ([4242], None, True, "survived the sweep, pids [4242]"),
        ([], OSError("no sandbox_check"), True, "could not be swept: no sandbox_check"),
    ],
    ids=["clean", "survivors", "sweep-fails"],
)
async def test_close_sweeps_its_own_marker_only(mac, sweeps, caplog, left, error, kept, logged):
    """Co-tasks of one item run side by side: closing one never touches the other's."""
    mine, other = _made(mac, "w1-s1"), _made(mac, "w1-s2")
    sweeps.left, sweeps.error = left, error

    await bwrap.OsBackend().close("s1", work_item_id="w1")

    assert sweeps.asked == ["w1-s1"]
    assert (mine.tmpdir.exists(), mine.profile.exists()) == (kept, kept)
    assert other.tmpdir.exists() and other.marker.exists() and other.profile.exists()
    assert (logged in caplog.text) if logged else not caplog.records


async def test_close_has_nothing_to_sweep_for_a_session_it_never_ran(mac, sweeps):
    await bwrap.OsBackend().close("s1", work_item_id="w1")
    assert sweeps.asked == []


async def test_sweep_ends_only_what_no_live_session_owns(mac, sweeps):
    """A daemon restart: the live session's processes survive; markers nobody owns,
    a leftover `setup-` one among them, are swept; `control` is no session's."""
    live = _made(mac, "w1-s1")
    orphans = [_made(mac, key) for key in ("w2-s9", "setup-abc", "a" * 32)]
    mac.files().control.write_text("")

    swept = await bwrap.OsBackend().sweep([("w1", "s1")])

    assert sorted(sweeps.asked) == sorted(f.marker.name for f in orphans) == sorted(swept)
    assert live.marker.exists() and live.tmpdir.exists() and mac.files().control.exists()


async def test_sweep_does_nothing_on_linux(mac, sweeps, monkeypatch):
    _made(mac, "w2-s9")
    monkeypatch.setattr(sys, "platform", "linux")
    assert await bwrap.OsBackend().sweep([]) == []
    assert sweeps.asked == []


# --- git: the branch the worker moves in place ----------------------------------------------------


@pytest.fixture
def item(mac, repo, tmp_path, run_dirs):
    """The item's linked worktree and its `code_in`."""
    wt = tmp_path / "item-wt"
    git(repo, "worktree", "add", "-q", "-b", BRANCH, str(wt))
    backend = bwrap.OsBackend()

    def code_in(**kw):
        return backend.code_in(run_dirs.base, wt, BRANCH, work_item_id="w1", session_id="s1", **kw)

    ref = repo / ".git" / "refs" / "heads" / BRANCH
    return SimpleNamespace(wt=wt, backend=backend, code_in=code_in, ref=ref)


def test_code_in_gives_direct_stores_and_keeps_a_live_co_tasks_lock(item):
    lock = item.ref.with_name(item.ref.name + ".lock")
    lock.write_text("")
    (store,) = item.code_in(live=["s2"])
    assert store.direct and store.branch == BRANCH and lock.exists()
    item.code_in()  # nothing else is live: a stale one goes
    assert not lock.exists()


@pytest.mark.parametrize("through", ["code_out", "code_out_item"])
def test_a_branch_aimed_at_main_is_put_back_before_anything_reads_it(item, run_dirs, through):
    """`code_out_item` is what a daemon restart reaches: no `run_task` is left to call
    `code_out`, and no ref store exists for Docker's `code_out_item` to find."""
    refs = item.code_in()
    start = item.ref.read_text()
    item.ref.write_text("ref: refs/heads/main\n")

    if through == "code_out":
        problems = item.backend.code_out(refs, "s1")
    else:
        problems = item.backend.code_out_item(run_dirs.base, "w1")

    assert len(problems) == 1 and "restored" in problems[0]
    assert item.ref.read_text() == start


# --- probe ----------------------------------------------------------------------------------------


def _ran(returncode=0, stderr=""):
    return subprocess.CompletedProcess([], returncode, "", stderr)


def _trial(result):
    return lambda mp: mp.setattr(bwrap, "_run", lambda argv, **kw: result)


@pytest.mark.parametrize(
    ("setup", "expect"),
    [
        (lambda mp: None, None),
        (
            lambda mp: mp.setattr(seatbelt.platform, "machine", lambda: "ppc"),
            "kind: os supports arm64 and x86_64 Macs, not ppc",
        ),
        (lambda mp: mp.setattr(os, "geteuid", lambda: 0), "does not run while Kraft runs as root"),
        (
            lambda mp: mp.setattr(seatbelt, "SANDBOX_EXEC", "/nonexistent/sandbox-exec"),
            "/nonexistent/sandbox-exec is missing",
        ),
        (
            lambda mp: mp.setattr(bwrap, "missing_tools", lambda: "curl is not installed"),
            "curl is not installed",
        ),
        (
            _trial(_ran(65, "sandbox_apply: Operation not permitted")),
            "Kraft is itself running inside a sandbox, so it cannot start one",
        ),
        (
            _trial(_ran(65, "syntax error in profile")),
            "sandbox-exec cannot start a sandbox here: syntax error in profile",
        ),
        (_trial("timed out"), "sandbox-exec cannot start a sandbox here: timed out"),
    ],
    ids=["ok", "arch", "root", "no-sandbox-exec", "no-curl", "nested", "profile-error", "timeout"],
)
def test_probe_darwin(tmp_path, monkeypatch, setup, expect):
    sandbox_exec = tmp_path / "sandbox-exec"
    sandbox_exec.write_text("#!/bin/sh\n")
    sandbox_exec.chmod(0o755)
    monkeypatch.setattr(seatbelt, "SANDBOX_EXEC", str(sandbox_exec))
    monkeypatch.setattr(seatbelt.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(os, "geteuid", lambda: 501)
    monkeypatch.setattr(bwrap, "missing_tools", lambda: None)
    monkeypatch.setattr(host_view, "system_dirs", lambda: (Path("/usr"),))
    monkeypatch.setattr(host_view, "system_excluded", lambda: ())
    ran = []

    def run(argv, **kw):
        ran.append((argv, Path(argv[2]).read_text()))
        return _ran()

    monkeypatch.setattr(bwrap, "_run", run)
    setup(monkeypatch)

    problem = seatbelt.probe_darwin()

    if expect is not None:
        assert expect in problem
        return
    assert problem is None
    # The renderer's own output for an empty session, run on `true`.
    ((argv, profile),) = ran
    assert argv == [str(sandbox_exec), "-f", argv[2], "-D", "RELAY_PORT=1", "/usr/bin/true"]
    assert "(deny default)" in profile and '(param "RELAY_PORT")' in profile


# --- the real sandbox-exec ---------------------------------------------------------------------


def _launch_for_real(argv, cwd):
    return subprocess.run(
        argv,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=120,
        env={"PATH": "/usr/bin:/bin"},
    )


@pytest.mark.e2e("sandbox-exec")
def test_a_wrapped_command_runs_and_leaves_nothing_behind(tmp_path, run_dirs, monkeypatch):
    monkeypatch.setenv("KRAFT_RUN_DIR", str(run_dirs.base))
    wt, cid = tmp_path / "wt", tmp_path / "cid"
    wt.mkdir()
    backend = bwrap.OsBackend()
    argv = backend.wrap(
        ["/usr/bin/true"], wt, {"kind": "os"}, None, session_id="s1", work_item_id="w1", cidfile=cid
    )

    done = _launch_for_real(argv, wt)

    assert done.returncode == 0, done.stderr
    assert not backend.launch_failed(cid)
    f = seatbelt.session_files(run_dirs.base, "w1-s1")
    assert not f.marker.exists() and not f.tmpdir.exists()


@pytest.mark.e2e("sandbox-exec")
def test_a_wrapped_commit_lands_on_the_item_branch(repo, tmp_path, run_dirs, monkeypatch):
    """Apple Git ends a commit by deleting pseudo-refs that are not there, which takes
    `packed-refs.lock`; it prints an error that it cannot, and the commit lands."""
    monkeypatch.setenv("KRAFT_RUN_DIR", str(run_dirs.base))
    wt = tmp_path / "item-wt"
    git(repo, "worktree", "add", "-q", "-b", BRANCH, str(wt))
    main = git(repo, "rev-parse", "main")
    backend = bwrap.OsBackend()
    refs = backend.code_in(run_dirs.base, wt, BRANCH, work_item_id="w1", session_id="s1")
    commit = [
        *("git", "-c", "user.name=a", "-c", "user.email=a@b"),
        *("commit", "--allow-empty", "-m", "x"),
    ]
    argv = backend.wrap(
        commit, wt, {"kind": "os"}, None, session_id="s1", work_item_id="w1", refs=refs
    )

    done = _launch_for_real(argv, wt)

    assert done.returncode == 0, done.stderr
    assert backend.code_out(refs, "s1") == []
    assert git(repo, "log", "-1", "--format=%s", BRANCH) == "x"
    assert git(repo, "rev-parse", "main") == main
