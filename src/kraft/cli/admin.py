"""This machine's server and its install: seed a home if there isn't one, then
serve; `start` is what bare `kraft` runs."""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import signal
import subprocess
import sys
import time
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from pathlib import Path

import uvicorn

from kraft import client, config, render
from kraft.cli import common
from kraft.paths import BUNDLED, RunDirs, default_run_dir, default_templates_dir


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
    if templates_dir.exists():
        return False
    if not (BUNDLED / "templates").is_dir():
        raise SystemExit(
            f"kraft: no config in {templates_dir} and no bundled defaults to seed it "
            "with. This build shipped without them — reinstall with `just install`, "
            "or point KRAFT_TEMPLATES_DIR at a config directory."
        )
    # Build beside the target and rename: an interrupted copy must not leave a
    # half-seeded home that every later start then treats as already seeded.
    staging = templates_dir.with_name(templates_dir.name + ".seeding")
    shutil.rmtree(staging, ignore_errors=True)
    shutil.copytree(BUNDLED / "templates", staging)
    (staging / "access.yaml").unlink(missing_ok=True)
    (staging / "notify.yaml").unlink(missing_ok=True)
    staging.rename(templates_dir)
    return True


def _bind(templates_dir: Path) -> tuple[str, int]:
    """Bind address from access.yaml — this is the "takes effect on restart" in
    Settings → Access (design 5e). Env still wins, for a one-off run."""
    access = config.load_access(templates_dir / "access.yaml")
    host = os.environ.get("KRAFT_HOST") or access["bind"]
    port = int(os.environ.get("KRAFT_PORT") or access["port"])
    if host not in config.LOOPBACK and not access["password_hash"]:
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
    # process; check the command name too if that ever bites. Same for the gap
    # between this read and _serve's write - two starts racing still collide on
    # the port unless they were given different ones.
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


def _serve() -> None:
    templates_dir = Path(os.environ.get("KRAFT_TEMPLATES_DIR") or default_templates_dir())
    if seed_home(templates_dir):
        print(f"kraft: seeded default config in {templates_dir}")
    host, port = _bind(templates_dir)
    # After _bind, so a run refused for binding a LAN address without a password
    # leaves no pidfile behind.
    pid_path = _pid_path()
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    running = _read_pid(pid_path)
    if running is not None:
        # Two servers on one run dir share databases and worktrees, and only
        # collide on the port if they were given the same one.
        print(f"kraft: already running (pid {running}) - kraft admin stop", file=sys.stderr)
        raise SystemExit(1)
    pid_path.write_text(str(os.getpid()))
    # Every worker Kraft launches inherits this environment (adapters/
    # subprocess.py's `full_env` starts from `os.environ`): a leftover
    # process found on a port can be checked against the daemon it actually
    # is, rather than assumed stale and killed (Kraft-f8u3).
    os.environ["KRAFT_DAEMON_PID"] = str(os.getpid())
    os.environ["KRAFT_DAEMON_PORT"] = str(port)
    _update_notice()
    print(f"kraft: http://{host}:{port}")
    try:
        # A backstop, not the fix: the fix is `ws_events` returning when the
        # connection goes away (Kraft-9oab). This bounds the next endpoint that
        # forgets, and an in-flight HTTP request that hangs. 10s is comfortably
        # above the slowest ordinary request (a forge call).
        uvicorn.run(
            "kraft.api:app",
            host=host,
            port=port,
            log_level="warning",
            timeout_graceful_shutdown=10,
        )
    finally:
        pid_path.unlink(missing_ok=True)


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

    log_path = run_dirs.logs / "server.log"
    with open(log_path, "ab") as log_file:
        proc = subprocess.Popen(
            [sys.executable, "-m", "kraft", "admin", "start"],
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=log_file,
            start_new_session=True,
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
    for _ in range(150):
        if _read_pid(pid_path) is None:
            print(f"kraft: stopped (pid {pid})")
            return
        time.sleep(0.1)
    print(f"kraft: pid {pid} did not stop within 15s", file=sys.stderr)
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


def _cmd_mcp(ns: argparse.Namespace) -> None:
    from kraft.mcp import serve_stdio

    serve_stdio()


def _cmd_init(ns: argparse.Namespace) -> None:
    from kraft.init import install

    for path in install(repo_scope=ns.repo):
        print(f"kraft: wrote {path}")


def _version() -> str:
    try:
        return _pkg_version("kraft")
    except PackageNotFoundError:
        # A source checkout that was never installed still answers, rather than
        # traceback: `--version` exists to diagnose an install, so it has to
        # survive not being one.
        return "0.0.0+source"


def _cmd_update(ns: argparse.Namespace) -> None:
    from kraft import update

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
    print(f"kraft {release.tag} installed. Restart a running server: kraft admin stop && kraft")


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

    health = subs.add_parser("health", parents=[common], help="the server's own status")
    health.set_defaults(func=_cmd_health)

    doctor_p = subs.add_parser(
        "doctor", parents=[common], help="check the whole install, one line per check"
    )
    doctor_p.set_defaults(func=_cmd_doctor)

    update_p = subs.add_parser("update", help="install the newest released kraft")
    update_p.add_argument("--force", action="store_true", help="install even when already current")
    update_p.set_defaults(func=_cmd_update)

    reindex = subs.add_parser("reindex", parents=[common], help="rescan documents into the index")
    reindex.add_argument("--repo", help="one repo path (default: all)")
    reindex.set_defaults(func=_cmd_reindex)

    reload_p = subs.add_parser(
        "reload", parents=[common], help="reread templates and registry from disk, no restart"
    )
    reload_p.set_defaults(func=_cmd_reload)

    init = subs.add_parser(
        "init", parents=[common], help="register Kraft's MCP server and skills with an agent"
    )
    init.add_argument("--repo", action="store_true", help="install into this repo, not the user")
    init.set_defaults(func=_cmd_init)

    mcp = subs.add_parser("mcp", help="serve the MCP tools over stdio")
    mcp.set_defaults(func=_cmd_mcp)
