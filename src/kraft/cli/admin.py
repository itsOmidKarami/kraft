"""This machine's server and its install: seed a home if there isn't one, then
serve; `start` is what bare `kraft` runs."""

from __future__ import annotations

import argparse
import asyncio
import os
import plistlib
import shutil
import signal
import socket
import subprocess
import sys
import time
import traceback
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from pathlib import Path

import httpx
import uvicorn
import yaml

from kraft import client, config, render
from kraft.cli import common
from kraft.paths import BUNDLED, RunDirs, default_run_dir, default_templates_dir
from kraft.policy import CarriedPolicy

#: launchd label / systemd unit name. One daemon, one name -- not
#: per-instance, since the spec is about supervising *the* daemon.
_LAUNCHD_LABEL = "com.kraft.daemon"
_SYSTEMD_UNIT = "kraft.service"

#: Env vars that define which instance this is. Carried into the unit so the
#: service supervises the same instance the human's shell was pointed at, not
#: a fresh, empty ~/.kraft (spec §1: "the unit must carry the same
#: environment the human's shell had -- KRAFT_HOME above all, or the service
#: silently supervises a *different* instance").
_INSTANCE_ENV_VARS = (
    "KRAFT_HOME",
    "KRAFT_RUN_DIR",
    "KRAFT_TEMPLATES_DIR",
    "KRAFT_SKILLS_DIR",
    "KRAFT_HOST",
    "KRAFT_PORT",
)


def _instance_env() -> dict[str, str]:
    """The instance vars, plus PATH.

    Every agent the daemon spawns is a bare `claude`/`bd` argv resolved
    through PATH (adapters/subprocess.py's {**os.environ, ...}), and
    launchd/systemd hand a supervised process their own minimal PATH, not
    the shell's. Without this, the service starts, health reports ok, and
    every agent launch fails. `_kraft_executable()` covers `kraft` itself;
    this covers everything `kraft` then spawns.
    """
    env = {name: os.environ[name] for name in _INSTANCE_ENV_VARS if name in os.environ}
    if "PATH" in os.environ:
        env["PATH"] = os.environ["PATH"]
    return env


def _kraft_executable() -> str:
    """The same binary a human would run by hand -- resolved once and
    written into the unit, so it points at a real path rather than relying
    on the service manager's own (usually minimal) PATH."""
    found = shutil.which("kraft")
    if not found:
        raise SystemExit(
            "kraft admin install-service: `kraft` is not on PATH - install it "
            "the normal way first (`just install`, or install.sh)."
        )
    return found


def _launchd_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{_LAUNCHD_LABEL}.plist"


def _systemd_unit_path() -> Path:
    # `systemctl --user` resolves unit files under $XDG_CONFIG_HOME when set,
    # falling back to ~/.config only when it is not -- writing unconditionally
    # under Path.home() disagrees with systemctl on any machine where
    # $XDG_CONFIG_HOME points elsewhere, leaving `enable --now` unable to find
    # the unit this just wrote.
    config_home = os.environ.get("XDG_CONFIG_HOME")
    base = Path(config_home) if config_home else Path.home() / ".config"
    return base / "systemd" / "user" / _SYSTEMD_UNIT


def _write_launchd_plist(kraft_bin: str) -> Path:
    path = _launchd_plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Built with `plistlib`, not string-formatted XML: a `&` or a `<` in
    # PATH or KRAFT_HOME would otherwise produce a plist launchd cannot parse.
    path.write_bytes(
        plistlib.dumps(
            {
                "Label": _LAUNCHD_LABEL,
                "ProgramArguments": [kraft_bin, "admin", "start"],
                "KeepAlive": True,
                "RunAtLoad": True,
                "EnvironmentVariables": _instance_env(),
            }
        )
    )
    return path


def _write_systemd_unit(kraft_bin: str) -> Path:
    path = _systemd_unit_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    # systemd reads the quoted value with C-style escapes, so a literal
    # backslash or double quote in PATH/KRAFT_HOME has to be escaped or the
    # unit fails to parse.
    def escaped(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')

    env_lines = "".join(
        f'Environment="{key}={escaped(value)}"\n' for key, value in _instance_env().items()
    )
    path.write_text(
        "[Unit]\n"
        "Description=Kraft orchestrator daemon\n"
        "\n"
        "[Service]\n"
        f"ExecStart={kraft_bin} admin start\n"
        "Restart=always\n"
        "RestartSec=2\n"
        f"{env_lines}"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )
    return path


def _cmd_install_service(ns: argparse.Namespace) -> None:
    """Adopt or refuse, never start a second one on the same run dir (spec
    §1) -- refuse: there is no supported way to hand an already-running,
    unmanaged process to the service manager without evicting or faking it.
    """
    running = _read_pid(_pid_path())
    if running is not None:
        raise SystemExit(
            f"kraft admin install-service: a server is already running (pid {running}) - "
            "`kraft admin stop` first, then install the service so it starts the next one."
        )
    kraft_bin = _kraft_executable()
    if sys.platform == "darwin":
        path = _write_launchd_plist(kraft_bin)
        subprocess.run(["launchctl", "load", "-w", str(path)], check=True)
        manager = "launchctl"
    elif sys.platform.startswith("linux"):
        path = _write_systemd_unit(kraft_bin)
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "--user", "enable", "--now", _SYSTEMD_UNIT], check=True)
        manager = "systemctl --user"
    else:
        raise SystemExit(f"kraft admin install-service: unsupported platform {sys.platform}")
    print(f"kraft: wrote {path}, loaded with {manager}")


def _cmd_uninstall_service(ns: argparse.Namespace) -> None:
    if sys.platform == "darwin":
        path = _launchd_plist_path()
        if path.is_file():
            subprocess.run(["launchctl", "unload", "-w", str(path)], check=False)
            path.unlink()
        print(f"kraft: removed {path}")
    elif sys.platform.startswith("linux"):
        path = _systemd_unit_path()
        subprocess.run(["systemctl", "--user", "disable", "--now", _SYSTEMD_UNIT], check=False)
        if path.is_file():
            path.unlink()
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
        print(f"kraft: removed {path}")
    else:
        raise SystemExit(f"kraft admin uninstall-service: unsupported platform {sys.platform}")


def seed_home(templates_dir: Path) -> bool:
    """Copy the packaged config into an empty home. True if it seeded.

    Only ever creates. An upgrade must not overwrite a policy the operator
    edited, and `access.yaml` is never bundled — it holds a password hash and a
    bind address that belong to one machine. `notify.yaml` is never bundled
    either, for the same reason: it usually holds a webhook URL with a bearer
    token embedded, and that belongs to one machine too. Reachable today by
    anyone who points `KRAFT_TEMPLATES_DIR` at a checkout with a live
    notify.yaml and runs `just install` -- if either file ever slips into
    `BUNDLED / "templates"`, it must still not reach a seeded home.
    """
    if finish_interrupted_update(templates_dir) or templates_dir.exists():
        return False
    if not (BUNDLED / "templates").is_dir():
        raise SystemExit(
            f"kraft: no config in {templates_dir} and no bundled defaults to seed it "
            "with. This build shipped without them — reinstall with `just install`, "
            "or point KRAFT_TEMPLATES_DIR at a config directory."
        )
    # Build beside the target and rename: an interrupted copy must not leave a
    # half-seeded home that every later start then treats as already seeded.
    _stage_bundle(templates_dir).rename(templates_dir)
    return True


def _stage_bundle(templates_dir: Path) -> Path:
    """The bundled config, copied beside `templates_dir` under a staging name
    for the caller to rename into place. Returns the staging directory."""
    staging = templates_dir.with_name(templates_dir.name + ".seeding")
    shutil.rmtree(staging, ignore_errors=True)
    shutil.copytree(BUNDLED / "templates", staging)
    (staging / "access.yaml").unlink(missing_ok=True)
    (staging / "notify.yaml").unlink(missing_ok=True)
    # What this home was seeded from, for `capabilities.added_since` (Kraft-hxt6x).
    # Written into `staging`, before the rename, so an interrupted seed can never
    # leave a stamp describing config that is not there.
    #
    # Deliberately not `.yaml`: `templates.load_templates` globs `*.yaml`, and a
    # YAML stamp would be read as a malformed template and surface as degraded
    # health. A non-YAML name sidesteps that instead of documenting it.
    (staging / ".seeded-version").write_text(f"{_version()}\n")
    return staging


#: What a home holds that belongs to this machine rather than to the template
#: schema: the password hash and bind, the webhook, the theme, the connected
#: repositories, auto-intake, the operator's steering files and harness
#: overrides. A major update carries each across unchanged; everything else --
#: the registry, the chains, `policy.yaml` -- is replaced, and kept in the backup.
MACHINE_CONFIG = (
    "access.yaml",
    "notify.yaml",
    "theme.yaml",
    "repos.yaml",
    "intake.yaml",
    "steering",
    "harnesses",
)


#: Written into a major update's staging directory once it is complete, naming
#: the backup the old home moves to. Its presence is what tells a later start
#: that a missing home is a swap to finish, not a home to seed.
UPDATE_STAGED = ".pre-v1-update"


def replace_pre_v1_config(templates_dir: Path, backup: Path) -> CarriedPolicy | None:
    """Install the bundled V1 configuration in place of a pre-V1 home, which
    moves to `backup` whole (`major-update-preserves-replaced-configuration`).

    Nothing is converted (`migration-helper-is-not-guaranteed`): the old
    chains and registry are only in the backup. The operator's `policy.yaml`
    values move onto the V1 seed's where V1 still has the key (Ruling 172);
    the result says what was dropped, `None` if there was no readable policy.

    Crash-safe (Kraft-cttgx): the new home is built complete beside the old
    one and marked (`UPDATE_STAGED`) before anything moves. Then two renames:
    old -> backup, staged -> home. Between them the home is missing, and
    `finish_interrupted_update` -- run by the next start and the next update --
    completes the swap from the marked staging instead of seeding over it."""
    if not (BUNDLED / "templates").is_dir():
        raise SystemExit(
            "kraft admin update: this build shipped no bundled configuration to install; "
            "reinstall with `just install`. Nothing was changed."
        )
    staging = _stage_bundle(templates_dir)
    for name in MACHINE_CONFIG:
        kept = templates_dir / name
        if kept.is_dir():
            # The operator's copy wins over a bundled file of the same name.
            shutil.copytree(kept, staging / name, dirs_exist_ok=True)
        elif kept.is_file():
            shutil.copy2(kept, staging / name)
    carried = _carry_policy(templates_dir / "policy.yaml", staging / "policy.yaml", backup)
    (staging / UPDATE_STAGED).write_text(f"{backup.name}\n")
    templates_dir.rename(backup)
    staging.rename(templates_dir)
    (templates_dir / UPDATE_STAGED).unlink()
    return carried


def _carry_policy(old: Path, new: Path, backup: Path) -> CarriedPolicy | None:
    try:
        legacy = yaml.safe_load(old.read_text())
        seed = yaml.safe_load(new.read_text()) or {}
    except OSError, ValueError, yaml.YAMLError:
        return None
    if not isinstance(legacy, dict):
        return None
    carried = CarriedPolicy.from_legacy(legacy, seed)
    if carried.data != seed:
        new.write_text(
            f"# The V1 policy, with the values `kraft admin update` carried over from\n"
            f"# the pre-V1 policy.yaml; that file is unchanged in {backup}.\n"
            + yaml.safe_dump(carried.data, sort_keys=False)
        )
    return carried


def finish_interrupted_update(templates_dir: Path) -> bool:
    """Complete a major update that stopped between its two renames: the home
    is missing and its staging is marked complete. True if it did, and says so.

    That is the only half-state the swap can leave -- the marker is written
    after the staging is whole, and the home moves only after the marker -- so
    there is nothing to roll back. An unmarked staging is an interrupted
    *seed*, and seeding over it is right."""
    staging = templates_dir.with_name(templates_dir.name + ".seeding")
    marker = staging / UPDATE_STAGED
    if templates_dir.exists() or not marker.is_file():
        return False
    backup = templates_dir.with_name(marker.read_text().strip())
    staging.rename(templates_dir)
    (templates_dir / UPDATE_STAGED).unlink()
    print(
        f"kraft: finished an interrupted update: installed the staged V1 configuration "
        f"in {templates_dir}; the old one is at {backup}",
        file=sys.stderr,
    )
    return True


def _warn_if_pre_v1(templates_dir: Path) -> None:
    """A legacy install's first V1 start: nothing is converted or overwritten.
    The server comes up degraded and refuses new work until the operator runs
    `kraft admin update`, and this says so where they are looking."""
    from kraft.templates.library import is_pre_v1

    if is_pre_v1(templates_dir):
        print(
            f"kraft: {templates_dir} holds a pre-V1 template configuration. Starting "
            "degraded, refusing new work; run `kraft admin update` to back it up and "
            "install the V1 configuration.",
            file=sys.stderr,
        )


def _bind(templates_dir: Path) -> tuple[str, int]:
    """Bind address from access.yaml — this is the "takes effect on restart" in
    Settings → Access (design 5e). Env still wins, for a one-off run."""
    access = config.load_access(templates_dir / "access.yaml")
    host = os.environ.get("KRAFT_HOST") or access.bind
    port = int(os.environ.get("KRAFT_PORT") or access.port)
    if host not in config.LOOPBACK and not access.password_hash:
        raise SystemExit(
            f"refusing to bind {host}: no password is set. Set one in Settings → Access "
            "while running on 127.0.0.1, or add password_hash to access.yaml."
        )
    return host, port


def _pid_path() -> Path:
    """Same run dir the API, the client and the doctor resolve."""
    return RunDirs(Path(os.environ.get("KRAFT_RUN_DIR") or default_run_dir())).pid


def _read_pid(path: Path) -> int | None:
    """The live pid in `path`, or None - clearing the file when it is stale.

    A pidfile that outlived a SIGKILLed server names nothing, so no caller may
    treat its existence alone as "a server is running".
    """
    # ponytail: a pid can be recycled, so a stale file could name an unrelated
    # process; check the command name too if that ever bites. `_refuse_if_addr_taken`
    # closes the common two-run-dirs-one-port case (Kraft-kquf); a race between
    # that check and the actual bind is still possible but is now a window of
    # milliseconds, not "always collides silently".
    try:
        pid = int(path.read_text())
    except FileNotFoundError, ValueError:
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        path.unlink(missing_ok=True)
        return None
    except PermissionError:
        pass  # alive, and not ours to signal
    return pid


def _update_notice() -> None:
    """One line at boot when a newer release exists.

    Reads the 24h cache, so an ordinary start pays nothing. A cold cache on a
    machine with no route out costs `update.TIMEOUT` once a day, and
    `KRAFT_NO_UPDATE_CHECK=1` costs nothing ever.
    """
    if os.environ.get("KRAFT_NO_UPDATE_CHECK"):
        return
    from kraft import update

    release = update.latest()
    if update.is_behind(release):
        print(
            f"kraft: {update.installed()} installed, {release.tag} available - kraft admin update"
        )


_WILDCARDS = {"0.0.0.0", "::", ""}


def _probe_host(host: str) -> str:
    """127.0.0.1 is what a same-machine client actually reaches a wildcard
    bind through -- connecting a socket to 0.0.0.0 itself is not a reliable
    target on macOS or Linux."""
    return "127.0.0.1" if host in _WILDCARDS else host


def _describe_occupant(host: str, port: int) -> str:
    """Best-effort identity of whatever already answers, for the refusal
    message (spec: "exit with what answered and how to inspect it")."""
    probe = _probe_host(host)
    try:
        response = httpx.get(f"http://{probe}:{port}/api/health", timeout=1.0)
        data = response.json()
        return f"a Kraft server (run_dir {data.get('run_dir')}, pid {data.get('pid')})"
    except Exception:
        return "something"


def _refuse_if_addr_taken(host: str, port: int) -> None:
    """Before binding, connect to host:port: if something answers, refuse
    with what it is and how to inspect it, rather than letting uvicorn's
    bind either fail cryptically or -- the macOS "127.0.0.1 next to *:"
    case -- succeed alongside it (Kraft-kquf, admin half).
    """
    probe = _probe_host(host)
    try:
        with socket.create_connection((probe, port), timeout=0.5):
            pass
    except OSError:
        return
    occupant = _describe_occupant(host, port)
    raise SystemExit(
        f"kraft: refusing to start - {occupant} is already answering on "
        f"{probe}:{port}. curl http://{probe}:{port}/api/health to inspect "
        "it, or `kraft admin stop` if it's yours."
    )


def _rotate_if_large(log_path: Path, max_bytes: int = 8_000_000) -> None:
    """Rotate before each start, not continuously (see the plan's ponytail
    note) -- server.log was 176KB and unbounded (Kraft-mqwg)."""
    try:
        if log_path.stat().st_size <= max_bytes:
            return
    except FileNotFoundError:
        return
    backup = log_path.with_suffix(log_path.suffix + ".1")
    backup.unlink(missing_ok=True)
    log_path.rename(backup)


class _Tee:
    """Every write goes to both streams -- the log file, and whatever
    `sys.stdout`/`sys.stderr` was before this replaced it (the real
    terminal, for a foreground start).
    """

    def __init__(self, *streams) -> None:
        self._streams = streams

    def write(self, data: str) -> int:
        for stream in self._streams:
            stream.write(data)
        return len(data)

    def flush(self) -> None:
        for stream in self._streams:
            stream.flush()

    def isatty(self) -> bool:
        # The first stream is the real terminal (or whatever sys.stdout/
        # stderr already was) -- uvicorn checks this to decide whether to
        # colorize its own log output.
        return self._streams[0].isatty()


def _redirect_output_to_log(log_path: Path) -> None:
    """A foreground start writes to server.log too now, not only --detach
    (Kraft-mqwg) -- *too*, per spec §3: a human running bare `kraft`, or
    `just dev`, still has to see the URL line, shutdown reason, tracebacks
    and uvicorn errors on their own terminal, not just in the file.

    Replaces the `sys.stdout`/`sys.stderr` objects rather than dup2'ing the
    fds: `logging.lastResort` (the fallback handler behind every existing
    `logging.warning(...)` call in the app) and every `print(..., file=...)`
    call here resolve `sys.stderr` dynamically at write time, not once at
    import, and uvicorn configures its own log handlers even later, once
    `server.run()` starts -- so all three pick up the replacement with no
    changes anywhere else, and land in both places instead of only the file.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = open(log_path, "a")
    sys.stdout = _Tee(sys.stdout, log_file)
    sys.stderr = _Tee(sys.stderr, log_file)


class _SignalLoggingServer(uvicorn.Server):
    """The next silent death names itself (Kraft-mqwg): `_serve`'s `finally`
    already ran on the way out and said nothing about why. `handle_exit` is
    uvicorn's own signal callback -- log before deferring to its usual
    graceful shutdown.

    Deliberately does *not* clear the pidfile here. `handle_exit` runs at
    signal *delivery*, and uvicorn then drains for up to
    `timeout_graceful_shutdown` with the databases still open; a pidfile
    removed at delivery would make `_cmd_stop` print "stopped" while the old
    process is still alive, and would let a second `_serve` past both the
    pidfile check and the address probe (uvicorn closes the listening socket
    first) onto the same run dir. The pidfile of a process that died
    signal-killed is stale, not a conflict, and `_read_pid` is what clears
    it -- it checks the pid is alive on every read.
    """

    def handle_exit(self, sig, frame):
        print(f"kraft: shutdown - signal {signal.Signals(sig).name}", file=sys.stderr, flush=True)
        super().handle_exit(sig, frame)


def _serve() -> None:
    templates_dir = Path(os.environ.get("KRAFT_TEMPLATES_DIR") or default_templates_dir())
    pid_path = _pid_path()
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    log_path = RunDirs(pid_path.parent).logs / "server.log"
    if not os.environ.get("KRAFT_LOG_REDIRECTED"):
        # Before _bind/_refuse_if_addr_taken/the already-running exit, not
        # after: a supervised daemon (launchd KeepAlive / systemd
        # Restart=always, task 5) has no console and launchd's plist sets no
        # StandardOutPath/StandardErrorPath, so a failure on any of those
        # paths used to vanish into /dev/null and retry forever with no trace
        # anywhere (Kraft-mqwg). --detach's parent (_start_detached) already
        # ties fd 1/2 to this same file before spawning.
        _rotate_if_large(log_path)
        _redirect_output_to_log(log_path)
    if seed_home(templates_dir):
        print(f"kraft: seeded default config in {templates_dir}")
    _warn_if_pre_v1(templates_dir)
    host, port = _bind(templates_dir)
    _refuse_if_addr_taken(host, port)
    running = _read_pid(pid_path)
    if running is not None:
        # Two servers on one run dir share databases and worktrees, and only
        # collide on the port if they were given the same one.
        print(f"kraft: already running (pid {running}) - kraft admin stop", file=sys.stderr)
        raise SystemExit(1)

    # A signal landing between here and `server.run()` installing uvicorn's
    # own handlers kills the process outright: no handler, no `finally`, no
    # `handle_exit`, and the pidfile outlives it. That file names a dead pid,
    # which `_read_pid` detects and clears on the next read -- so the gap
    # needs no handler of its own.
    pid_path.write_text(str(os.getpid()))
    # So `kraft admin restart` starts it back up the same way it was running:
    # `_start_detached` marks its child with KRAFT_DETACHED before exec'ing
    # into this same function.
    RunDirs(pid_path.parent).mode.write_text(
        "detached" if os.environ.get("KRAFT_DETACHED") else "attached"
    )
    # Every worker Kraft launches inherits these two names (they're in
    # `worker_env.BASELINE`, which `adapters/subprocess.py`'s `full_env`
    # copies out of `os.environ`): a leftover process found on a port can be
    # checked against the daemon it actually is, rather than assumed stale
    # and killed (Kraft-f8u3).
    os.environ["KRAFT_DAEMON_PID"] = str(os.getpid())
    os.environ["KRAFT_DAEMON_PORT"] = str(port)
    _update_notice()
    print(f"kraft: http://{host}:{port}")
    try:
        server = _SignalLoggingServer(
            uvicorn.Config(
                "kraft.api:app",
                host=host,
                port=port,
                log_level="warning",
                # A backstop, not the fix: the fix is `ws_events` returning when
                # the connection goes away (Kraft-9oab). This bounds the next
                # endpoint that forgets, and an in-flight HTTP request that
                # hangs. 10s is comfortably above the slowest ordinary request
                # (a forge call).
                timeout_graceful_shutdown=10,
            )
        )
        server.run()
    except BaseException:
        print("kraft: shutdown - unhandled exception:", file=sys.stderr, flush=True)
        traceback.print_exc()
        raise
    finally:
        pid_path.unlink(missing_ok=True)
        RunDirs(pid_path.parent).mode.unlink(missing_ok=True)


def _cmd_start(ns: argparse.Namespace) -> None:
    """The same path as bare `kraft`, with three flags on top.

    The host/port flags become the env vars `_bind()` already reads, so there
    is one precedence chain (flag > env > access.yaml) and one place that
    refuses a LAN bind without a password. Setting the env rather than passing
    arguments through is deliberate: a second signature would be a second
    place for that check to be forgotten.
    """
    if ns.host:
        os.environ["KRAFT_HOST"] = ns.host
    if ns.port:
        os.environ["KRAFT_PORT"] = str(ns.port)
    if ns.detach:
        _start_detached()
        return
    _serve()


def _start_detached() -> None:
    """Launch `admin start` in its own session and return once it is up.

    Not this process backgrounding itself: once `_serve()` calls into uvicorn
    it owns signal handling and stdio for good, so there is no point after
    that where control could still return to a caller. A real child, in its
    own session (`start_new_session`) so it outlives the shell that launched
    it, with stdio redirected to the same place worker logs go. It writes the
    same pidfile `_serve` always has, so `admin stop`/`health`/`doctor` never
    need to know a server was started this way.
    """
    templates_dir = Path(os.environ.get("KRAFT_TEMPLATES_DIR") or default_templates_dir())
    if seed_home(templates_dir):
        print(f"kraft: seeded default config in {templates_dir}")
    run_dirs = RunDirs(Path(os.environ.get("KRAFT_RUN_DIR") or default_run_dir())).ensure()
    pid_path = run_dirs.pid
    running = _read_pid(pid_path)
    if running is not None:
        print(f"kraft: already running (pid {running}) - kraft admin stop", file=sys.stderr)
        raise SystemExit(1)
    # Resolved (and validated — refuses a password-less LAN bind) here too, so
    # a bad access.yaml fails this shell instead of showing up only as a child
    # that exited before ever writing a pidfile.
    host, port = _bind(templates_dir)
    _refuse_if_addr_taken(host, port)

    log_path = run_dirs.logs / "server.log"
    _rotate_if_large(log_path)
    with open(log_path, "ab") as log_file:
        proc = subprocess.Popen(
            [sys.executable, "-m", "kraft", "admin", "start"],
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=log_file,
            start_new_session=True,
            env={**os.environ, "KRAFT_LOG_REDIRECTED": "1", "KRAFT_DETACHED": "1"},
        )

    # Wait for the child to actually bind, not just fork: a bad config or a
    # port already in use both exit within the first second, and returning
    # before that would report success for a server that's already dead.
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        pid = _read_pid(pid_path)
        if pid is not None:
            print(f"kraft: http://{host}:{port} (pid {pid}, detached - kraft admin stop)")
            return
        if proc.poll() is not None:
            tail = log_path.read_text()[-2000:]
            print(f"kraft: detached start failed:\n{tail}", file=sys.stderr)
            raise SystemExit(1)
        time.sleep(0.1)
    print(f"kraft: detached start did not come up within 10s - check {log_path}", file=sys.stderr)
    raise SystemExit(1)


def _cmd_stop(ns: argparse.Namespace) -> None:
    """SIGTERM to the pid in the run dir, then wait for it to actually go.

    Nothing running is not a failure: `kraft admin stop` in a teardown script
    has to be safe to run twice.
    """
    pid_path = _pid_path()
    pid = _read_pid(pid_path)
    if pid is None:
        print("kraft: no server running")
        return
    os.kill(pid, signal.SIGTERM)
    # 15s, not 5: the poll must not expire before the graceful-shutdown backstop
    # it is waiting on (`_serve`, timeout_graceful_shutdown=10).
    #
    # `_read_pid` returns None only once the pid it names is actually gone
    # (it signals 0 on every read), so this waits for the real exit, not for
    # a file to disappear -- the server holds its databases open for the
    # whole graceful drain. A supervised daemon (launchd KeepAlive / systemd
    # Restart=always, task 5) rewrites the pidfile with a fresh pid well
    # inside this poll, which is the other way out.
    for _ in range(150):
        current = _read_pid(pid_path)
        if current is None or current != pid:
            print(f"kraft: stopped (pid {pid})")
            return
        time.sleep(0.1)
    print(f"kraft: pid {pid} did not stop within 15s", file=sys.stderr)
    raise SystemExit(1)


def _service_installed() -> bool:
    if sys.platform == "darwin":
        return _launchd_plist_path().is_file()
    if sys.platform.startswith("linux"):
        return _systemd_unit_path().is_file()
    return False


def _restart_service() -> None:
    """Through the service manager, not SIGTERM+start -- a unit's own
    `restart` verb is what keeps it a *managed* restart (systemd resets its
    failure-count window; launchd has no equivalent, so unload+load is the
    closest same thing) instead of one this process fakes by racing
    KeepAlive/Restart=always for who starts the next process."""
    if sys.platform == "darwin":
        path = _launchd_plist_path()
        subprocess.run(["launchctl", "unload", "-w", str(path)], check=False)
        subprocess.run(["launchctl", "load", "-w", str(path)], check=True)
    else:
        subprocess.run(["systemctl", "--user", "restart", _SYSTEMD_UNIT], check=True)
    print("kraft: restarted the service")


def _cmd_restart(ns: argparse.Namespace) -> None:
    """`stop` then `start` again the same way it was running.

    A service (launchd/systemd) is restarted through its own manager, service
    file untouched, never SIGTERM'd and left to KeepAlive/Restart=always to
    notice -- explicit beats a race with the supervisor. Otherwise: stop, then
    bring it back detached if it was detached. A server running attached to
    someone's terminal can't be handed back to that terminal from here, so
    this only stops it and says so -- restarting it is that terminal's job.
    """
    if _service_installed():
        _restart_service()
        return
    pid_path = _pid_path()
    pid = _read_pid(pid_path)
    if pid is None:
        print("kraft: no server running")
        return
    mode_path = RunDirs(pid_path.parent).mode
    detached = mode_path.is_file() and mode_path.read_text().strip() == "detached"
    _cmd_stop(ns)
    if detached:
        _start_detached()
    else:
        print(
            "kraft: was running attached to a terminal - start it again there: kraft",
            file=sys.stderr,
        )
        raise SystemExit(1)


def _render_health(payload: dict) -> str:
    return render.health_block(payload)


def _cmd_health(ns: argparse.Namespace) -> None:
    payload = asyncio.run(client.health())
    common.emit(payload, _render_health, ns.json)
    if payload.get("status") != "ok":
        # exit 1 so `kraft health && ...` works; the reasons are already on stdout
        raise SystemExit(1)


def _cmd_doctor(ns: argparse.Namespace) -> None:
    from kraft import doctor

    rows = asyncio.run(doctor.run_checks())
    common.emit(rows, render.doctor_block, ns.json)
    if any(not row["ok"] for row in rows):
        # exit 1 so `kraft doctor && ...` works; the failures are already on stdout
        raise SystemExit(1)


def _render_reindex(result: dict) -> str:
    scope = result.get("repo") or "all repos"
    counts = ", ".join(f"{k} {v}" for k, v in result.get("stats", {}).items())
    return f"reindexed {scope}: {counts}"


def _cmd_reindex(ns: argparse.Namespace) -> None:
    common.emit(asyncio.run(client.reindex(ns.repo)), _render_reindex, ns.json)


def _render_reload(result: dict) -> str:
    lines = [f"reloaded {len(result.get('valid', []))} template(s)"]
    for name, reason in (result.get("invalid_templates") or {}).items():
        lines.append(f"  invalid: {name}: {reason}")
    return "\n".join(lines)


def _cmd_reload(ns: argparse.Namespace) -> None:
    payload = asyncio.run(client.reload_templates())
    common.emit(payload, _render_reload, ns.json)
    if payload.get("invalid_templates"):
        # exit 1 so `kraft admin reload && ...` works; the reasons are already on stdout
        raise SystemExit(1)


def _render_lint(report: dict) -> str:
    if report["valid"]:
        return f"{len(report['chains'])} chain(s), no errors"
    return "\n".join(
        f"{issue['chain'] or issue['file']}: {issue['message']}" for issue in report["issues"]
    )


def _cmd_templates_lint(ns: argparse.Namespace) -> None:
    report = asyncio.run(client.lint_templates())
    common.emit(report, _render_lint, ns.json)
    if not report["valid"]:
        # exit 1 so `kraft admin templates lint && ...` works; the errors are on stdout
        raise SystemExit(1)


def _cmd_templates_show(ns: argparse.Namespace) -> None:
    if ns.resolved:
        payload = asyncio.run(client.resolved_template(ns.template_id))
        common.emit(payload, lambda p: yaml.safe_dump(p["chain"], sort_keys=False), ns.json)
    else:
        payload = asyncio.run(client.template(ns.template_id))
        common.emit(payload, lambda p: yaml.safe_dump(p, sort_keys=False), ns.json)


def _cmd_mcp(ns: argparse.Namespace) -> None:
    from kraft.mcp import serve_stdio

    serve_stdio()


def _cmd_init(ns: argparse.Namespace) -> None:
    from kraft.init import install

    for path in install(repo_scope=ns.repo):
        print(f"kraft: wrote {path}")


def _version() -> str:
    try:
        return _pkg_version("kraft-sdlc")
    except PackageNotFoundError:
        # A source checkout that was never installed still answers, rather than
        # traceback: `--version` exists to diagnose an install, so it has to
        # survive not being one.
        return "0.0.0+source"


def _accept_major_update(templates_dir: Path, assume_yes: bool) -> None:
    """Replace a pre-V1 `templates_dir` once the operator has said yes, or
    exit 1 having changed nothing (`major-update-requires-explicit-acceptance`).
    The breaking change is stated before the question is asked."""
    backup = templates_dir.with_name(
        f"{templates_dir.name}.pre-v1-{time.strftime('%Y%m%d-%H%M%S')}"
    )
    print(
        f"kraft: {templates_dir} holds a pre-V1 template configuration (a hook registry\n"
        "and gate_after chains). It is not compatible with this Kraft, which runs\n"
        "Template Schema V1 only, and there is no migration: replacing it installs the\n"
        "V1 configuration and moves the current one, whole, to a backup at\n"
        f"  {backup}\n"
        f"Carried across unchanged: {', '.join(MACHINE_CONFIG)}.\n"
        "policy.yaml keeps your value for every key V1 still has; any other key is\n"
        "dropped and listed. Your chains and registry.yaml are replaced. All of it\n"
        "stays in the backup.",
        file=sys.stderr,
    )
    # No terminal to ask on is a refusal, never a default yes.
    if not assume_yes and not (
        sys.stdin.isatty() and input("Replace it? [y/N] ").strip().lower() in ("y", "yes")
    ):
        print(
            "kraft admin update: nothing changed. Answer y, or pass -y, to replace it.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    carried = replace_pre_v1_config(templates_dir, backup)
    print(f"kraft: installed the V1 configuration in {templates_dir}; the old one is at {backup}")
    if carried is None:
        print("kraft: policy.yaml: no readable pre-V1 policy; the V1 default is installed")
    for key, value in (carried.dropped if carried else {}).items():
        print(
            f"kraft: policy.yaml: dropped {key} = {value!r} "
            "(V1 has no such key, or refuses the value)"
        )


def _cmd_update(ns: argparse.Namespace) -> None:
    from kraft import update
    from kraft.templates.library import is_pre_v1

    # The configuration first: a pre-V1 home is what a legacy install's first V1
    # binary finds, and that binary is the one running this.
    templates_dir = Path(os.environ.get("KRAFT_TEMPLATES_DIR") or default_templates_dir())
    finish_interrupted_update(templates_dir)
    if is_pre_v1(templates_dir):
        _accept_major_update(templates_dir, ns.yes)

    release = update.latest(force=True)
    if release is None:
        print(
            "kraft admin update: could not reach the release feed. Try again, "
            "or install by hand from the releases page.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    here = update.installed()
    if not update.is_behind(release) and not ns.force:
        print(f"kraft {here} is up to date ({release.tag} is the newest release)")
        return
    print(f"kraft {here} -> {release.tag}")
    code = update.perform(release)
    if code != 0:
        raise SystemExit(code)
    print(f"kraft {release.tag} installed.")
    if ns.restart:
        _cmd_restart(ns)
    else:
        print("Restart a running server: kraft admin restart")


def _add_admin(subs, common: argparse.ArgumentParser) -> None:
    """This machine's server and its install. `start` is what bare `kraft` runs."""
    start = subs.add_parser("start", help="run the server (the same as bare `kraft`)")
    start.add_argument("--host", help="bind address (default: access.yaml, or KRAFT_HOST)")
    start.add_argument("--port", type=int, help="port (default: access.yaml, or KRAFT_PORT)")
    start.add_argument(
        "--detach",
        "-d",
        action="store_true",
        help="fork into its own session and return once it's up, instead of blocking this shell",
    )
    start.set_defaults(func=_cmd_start)

    stop = subs.add_parser("stop", help="stop the running server")
    stop.set_defaults(func=_cmd_stop)

    restart = subs.add_parser("restart", help="stop then start again, the same way it was running")
    restart.set_defaults(func=_cmd_restart)

    install_service = subs.add_parser(
        "install-service", help="write and load an OS service unit (KeepAlive / Restart=always)"
    )
    install_service.set_defaults(func=_cmd_install_service)

    uninstall_service = subs.add_parser("uninstall-service", help="remove the OS service unit")
    uninstall_service.set_defaults(func=_cmd_uninstall_service)

    health = subs.add_parser("health", parents=[common], help="the server's own status")
    health.set_defaults(func=_cmd_health)

    doctor_p = subs.add_parser(
        "doctor", parents=[common], help="check the whole install, one line per check"
    )
    doctor_p.set_defaults(func=_cmd_doctor)

    update_p = subs.add_parser("update", help="install the newest released kraft")
    update_p.add_argument("--force", action="store_true", help="install even when already current")
    update_p.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="accept replacing a pre-V1 template configuration, which is backed up first",
    )
    update_p.add_argument(
        "--restart",
        action="store_true",
        help="restart a running server after a successful update, the same way it was running",
    )
    update_p.set_defaults(func=_cmd_update)

    reindex = subs.add_parser("reindex", parents=[common], help="rescan documents into the index")
    reindex.add_argument("--repo", help="one repo path (default: all)")
    reindex.set_defaults(func=_cmd_reindex)

    reload_p = subs.add_parser(
        "reload", parents=[common], help="reread templates and registry from disk, no restart"
    )
    reload_p.set_defaults(func=_cmd_reload)

    templates_p = subs.add_parser("templates", help="inspect the chain template library")
    template_verbs = templates_p.add_subparsers(dest="templates_verb", required=True)
    lint_p = template_verbs.add_parser(
        "lint",
        parents=[common],
        help="check every chain in the installed library; exit 1 on any error",
    )
    lint_p.set_defaults(func=_cmd_templates_lint)
    show_p = template_verbs.add_parser("show", parents=[common], help="print one chain template")
    show_p.add_argument("template_id", metavar="ID")
    show_p.add_argument(
        "--resolved",
        action="store_true",
        help="with its library components expanded, as a work item would get it",
    )
    show_p.set_defaults(func=_cmd_templates_show)

    init = subs.add_parser(
        "init", parents=[common], help="register Kraft's MCP server and skills with an agent"
    )
    init.add_argument("--repo", action="store_true", help="install into this repo, not the user")
    init.set_defaults(func=_cmd_init)

    mcp = subs.add_parser("mcp", help="serve the MCP tools over stdio")
    mcp.set_defaults(func=_cmd_mcp)
