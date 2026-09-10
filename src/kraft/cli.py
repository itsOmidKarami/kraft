"""The `kraft` command: seed a home if there isn't one, then serve.

`python -m kraft` and the installed console script are the same code path, so a
checkout and an install can only ever differ in where their paths point.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from pathlib import Path

import argcomplete
import uvicorn

from kraft import client, config, render
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


def emit(value, renderer, as_json: bool) -> None:
    """One place decides human-or-JSON, so no verb can forget the contract.

    `--json` prints exactly what `client.py` returned. The CLI must never become
    a second definition of what a work item is — the MCP door reads the same
    value, and the two are only guaranteed identical if neither reshapes.
    """
    if as_json:
        print(json.dumps(value, indent=2))
    else:
        print(renderer(value))


def _json_flag() -> argparse.ArgumentParser:
    """A parent parser so `--json` works on every verb without eight copies."""
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument(
        "--json", action="store_true", help="print the raw API payload instead of a table"
    )
    return parent


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
    emit(payload, _render_health, ns.json)
    if payload.get("status") != "ok":
        # exit 1 so `kraft health && ...` works; the reasons are already on stdout
        raise SystemExit(1)


def _cmd_doctor(ns: argparse.Namespace) -> None:
    from kraft import doctor

    rows = asyncio.run(doctor.run_checks())
    emit(rows, render.doctor_block, ns.json)
    if any(not row["ok"] for row in rows):
        # exit 1 so `kraft doctor && ...` works; the failures are already on stdout
        raise SystemExit(1)


def _render_reindex(result: dict) -> str:
    scope = result.get("repo") or "all repos"
    counts = ", ".join(f"{k} {v}" for k, v in result.get("stats", {}).items())
    return f"reindexed {scope}: {counts}"


def _cmd_reindex(ns: argparse.Namespace) -> None:
    emit(asyncio.run(client.reindex(ns.repo)), _render_reindex, ns.json)


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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kraft",
        description="Kraft: run it with no arguments to serve; subcommands talk to a server.",
    )
    # An install can silently fall behind the checkout it was built from — the
    # build that predated this file's argparse answered every subcommand by
    # serving, and nothing said so (Kraft-krd).
    parser.add_argument("--version", action="version", version=f"kraft {_version()}")
    subs = parser.add_subparsers(dest="group", required=True)
    common = _json_flag()

    for name, help_text, adder in _GROUPS:
        group = subs.add_parser(name, help=help_text)
        adder(group.add_subparsers(dest="verb", required=True), common)
    return parser


_LIST_COLUMNS = [
    ("ID", "id"),
    ("STATUS", "status"),
    ("GATE", "pending_gate"),
    ("NODE", "current_node_id"),
    ("TITLE", "title"),
]


def _render_list(items: list[dict]) -> str:
    painted = [
        {
            **item,
            "status": render.paint(item["status"], render.STATUS_COLORS.get(item["status"], "")),
        }
        for item in items
    ]
    return render.table(painted, _LIST_COLUMNS)


def _render_show(item: dict) -> str:
    return render.kv([(key, str(value)) for key, value in item.items()])


def _render_search(payload: dict) -> str:
    rows = [
        {"kind": hit.get("kind"), "repo": hit.get("repo"), "path": hit.get("path")}
        for hit in payload.get("results", [])
    ]
    return render.table(rows, [("KIND", "kind"), ("REPO", "repo"), ("PATH", "path")])


def _render_action(result: dict) -> str:
    """An act response is small and shapeless; a kv block beats inventing a table."""
    return render.kv([(key, str(value)) for key, value in result.items()]) or "ok"


def _repo_scope(ns: argparse.Namespace) -> str | None:
    """Explicit --repo, then the cwd's connected repo, then nothing.

    The one precedence rule every verb shares. `--all` opts out of the implicit
    half, for when the scoping is what surprised you.
    """
    if getattr(ns, "all", False):
        return None
    if getattr(ns, "repo", None):
        return ns.repo
    return asyncio.run(client.resolve_repo())


def _cmd_list(ns: argparse.Namespace) -> None:
    repo = _repo_scope(ns)
    items = asyncio.run(client.list_work_items(ns.status, include_abandoned=ns.include_abandoned))
    if repo:
        items = [item for item in items if item["repo"] == repo]
    emit(items, _render_list, ns.json)


def _cmd_show(ns: argparse.Namespace) -> None:
    emit(asyncio.run(client.get_work_item(ns.id)), _render_show, ns.json)


def _cmd_search(ns: argparse.Namespace) -> None:
    emit(asyncio.run(client.search(ns.query, ns.limit)), _render_search, ns.json)


def _cmd_create(ns: argparse.Namespace) -> None:
    # Resolved here rather than sent as typed: the server joins the path onto a
    # candidate root (the repo, or the worktree we are standing in), never onto
    # the cwd, so `--spec specs/x.md` from a subdirectory of either would miss
    # both. Absolute, it is still accepted only if it lands inside one of them.
    attachments = [
        {"kind": kind, "path": str(Path(value).expanduser().resolve())}
        for kind, value in (("spec", ns.spec), ("plan", ns.plan))
        if value
    ]
    emit(
        asyncio.run(
            client.create_work_item(
                ns.title, _repo_scope(ns), ns.chain, ns.description, attachments or None
            )
        ),
        _render_action,
        ns.json,
    )


def _cmd_approve(ns: argparse.Namespace) -> None:
    emit(asyncio.run(client.approve_gate(ns.gate, ns.id)), _render_action, ns.json)


def _cmd_reject(ns: argparse.Namespace) -> None:
    emit(
        asyncio.run(client.reject_gate(ns.note, ns.gate, ns.id, ns.node)),
        _render_action,
        ns.json,
    )


def _cmd_pause(ns: argparse.Namespace) -> None:
    emit(asyncio.run(client.pause(ns.id)), _render_action, ns.json)


def _cmd_abandon(ns: argparse.Namespace) -> None:
    if not ns.yes:
        raise ValueError("abandon destroys the worktree and anything uncommitted in it; pass --yes")
    emit(asyncio.run(client.abandon(ns.id)), _render_action, ns.json)


def _cmd_resume(ns: argparse.Namespace) -> None:
    emit(asyncio.run(client.resume(ns.steer, ns.id)), _render_action, ns.json)


def _cmd_retry(ns: argparse.Namespace) -> None:
    emit(asyncio.run(client.retry(ns.steer, ns.id)), _render_action, ns.json)


def _cmd_mr_label(ns: argparse.Namespace) -> None:
    emit(asyncio.run(client.mr_labels(ns.labels, ns.id)), _render_action, ns.json)


def _print_log(entry: dict, as_json: bool) -> None:
    """NDJSON under --json: one object per line, because a stream has no end to
    close an array on. `flush` because a follow that buffers is not a follow."""
    print(json.dumps(entry) if as_json else render.log_line(entry), flush=True)


def _print_event(event: dict, as_json: bool) -> None:
    """One event, one line. `_print_log`'s contract, for the other stream.

    NDJSON under --json because a stream has no end to close an array on, and
    `flush` because a followed stream through a pipe is block-buffered
    otherwise — `emit` knows neither (Kraft-tom2, Kraft-owea).
    """
    print(json.dumps(event) if as_json else render.event_line([event], headers=False), flush=True)


def _cmd_logs(ns: argparse.Namespace) -> None:
    async def run() -> None:
        session_id = ns.session or (await client.latest_session(ns.id))["id"]
        lines = await client.log_backlog(session_id, ns.n)
        for entry in lines:
            _print_log(entry, ns.json)
        if ns.follow:
            seen = lines[-1]["n"] + 1 if lines else 0
            async for entry in client.stream_log(session_id, after_line=seen):
                _print_log(entry, ns.json)

    asyncio.run(run())


#: The events after which a chain produces no more of them. A follow that
#: outlives the item is worse than no follow -- it is a monitor that stays
#: armed forever on work that finished.
_CHAIN_ENDED = ("work_item_completed", "work_item_abandoned")


def _cmd_events(ns: argparse.Namespace) -> None:
    async def run() -> None:
        wid = await client.resolve_work_item(ns.id)
        rows = await client.events(wid, ns.after)
        shown = [row for row in rows if row.get("type") == ns.type] if ns.type else rows
        emit(shown, render.event_line, ns.json)
        if not ns.follow:
            return
        # An item that has already ended carries its terminal event in the
        # backlog, not in the stream, so following it would wait for a frame
        # that is never coming. The cursor is taken from the unfiltered rows
        # for the same reason a --type follow must not replay: what was
        # printed and what was seen are different questions.
        if any(row["type"] in _CHAIN_ENDED for row in rows):
            return
        after = rows[-1]["seq"] if rows else ns.after
        async for ev in client.stream_events(after):
            if ev["work_item_id"] != wid:
                continue
            if not ns.type or ev.get("type") == ns.type:
                _print_event(ev, ns.json)
            if ev["type"] in _CHAIN_ENDED:
                return

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass  # matches watch: Ctrl-C ends a follow cleanly, not with a traceback


def _cmd_watch(ns: argparse.Namespace) -> None:
    if ns.json:
        raise ValueError(
            "watch has no --json; use `kraft events --json` to stream structured output"
        )
    if not sys.stdout.isatty():
        raise ValueError("watch needs a terminal to redraw in — try `kraft events` in a pipe")

    async def run() -> None:
        repo = ns.repo or await client.resolve_repo()
        drawn = 0

        async def frame(drawn: int) -> tuple[int, int]:
            """Draw the board, and report the event cursor it reflects.

            The cursor comes back with the board so the stream can start from
            *now*: connecting at seq 0 would replay every event the server has
            ever committed, redrawing the board once per historical row.
            """
            items, cursor = await client.board()
            if repo:
                items = [item for item in items if item["repo"] == repo]
            return render.redraw(_render_list(items), drawn), cursor

        drawn, cursor = await frame(drawn)
        async for _event in client.stream_events(cursor):
            drawn, cursor = await frame(drawn)

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass  # the frame stays on screen; that is the point of not using an alt screen


_DOC_COLUMNS = [("ID", "document_id"), ("KIND", "kind"), ("TITLE", "title"), ("PATH", "path")]


def _render_docs(rows: list[dict]) -> str:
    return render.table(rows, _DOC_COLUMNS)


def _cmd_diff(ns: argparse.Namespace) -> None:
    payload = asyncio.run(client.diff(ns.id))
    if ns.json:
        emit(payload, str, True)
        return
    if ns.name_only:
        if payload.get("base_ref") is None:
            # empty and unknown are different answers, in every view
            print("no baseline recorded for this work item")
            return
        # landed paths too: --name-only that answered only for the in-flight
        # side would drop committed work from the CLI, which is Kraft-nceo from
        # the other direction. dict.fromkeys dedupes a path that is on both
        # sides while keeping in-flight-first order.
        landed = (payload.get("landed") or {}).get("files") or []
        paths = list(
            dict.fromkeys(
                [f["path"] for f in payload.get("files", [])]
                + [f["path"] for f in landed]
                + list(payload.get("untracked", []))
            )
        )
        print("\n".join(paths))
        return
    text = render.diff_stat(payload) if ns.stat else render.diff_body(payload)
    render.page(text, force_plain=ns.no_pager)


def _cmd_docs(ns: argparse.Namespace) -> None:
    emit(asyncio.run(client.documents(ns.id)), _render_docs, ns.json)


def _cmd_doc(ns: argparse.Namespace) -> None:
    if ns.open is not None:
        editor = ns.open or None  # `--open` alone means the server's default
        result = asyncio.run(client.open_document(ns.doc_id, editor))
        emit(result, _render_action, ns.json)
        return
    doc = asyncio.run(client.document(ns.doc_id))
    if ns.json:
        emit(doc, str, True)
        return
    render.page(doc.get("content", ""), force_plain=ns.no_pager)


def _cmd_artifact(ns: argparse.Namespace) -> None:
    payload = asyncio.run(client.artifact(ns.id))
    if ns.json:
        emit(payload, str, True)
        return
    render.page(render.artifact_body(payload), force_plain=ns.no_pager)


_REPO_COLUMNS = [
    ("", "here"),
    ("NAME", "name"),
    ("STATE", "state"),
    ("CHAIN", "default_chain_template"),
    ("PATH", "path"),
]

#: Printed by `kraft path --shell`. A subprocess cannot change its parent's
#: directory, so the real `cd` has to be a function in the user's shell.
SHELL_WRAPPER = """\
# Add to ~/.zshrc or ~/.bashrc:
kcd() { cd "$(kraft path "$@")" || return; }
"""


def _render_repos(rows: list[dict], here: str | None) -> str:
    """The cwd's repo gets a `*` in the first column: it is the answer to "why
    did my last command pick that repo"."""
    shaped = [
        {
            **row,
            "here": "*" if row["path"] == here else " ",
            # The word, not just dim paint: spec D §3 lists enabled as output,
            # and paint alone vanishes into a pipe or under NO_COLOR. Dim moves
            # onto this cell so there is one signal with colour as an accent.
            "state": "enabled"
            if row.get("enabled", True)
            else render.paint("disabled", render.DIM),
        }
        for row in rows
    ]
    return render.table(shaped, _REPO_COLUMNS)


def _cmd_repos(ns: argparse.Namespace) -> None:
    async def run():
        return await client.repos(), await client.resolve_repo()

    rows, here = asyncio.run(run())
    emit(rows, lambda value: _render_repos(value, here), ns.json)


def _cmd_connect(ns: argparse.Namespace) -> None:
    result = asyncio.run(client.ensure_repo(ns.path))
    if ns.json:
        emit(result, str, True)
        return
    verb = "already connected" if result.get("already_connected") else "connected"
    print(f"{verb}: {result['path']}")


def _cmd_disconnect(ns: argparse.Namespace) -> None:
    result = asyncio.run(client.disconnect_repo(ns.path))
    if ns.json:
        emit(result, str, True)
        return
    print(f"disconnected: {result['path']}")


def _cmd_path(ns: argparse.Namespace) -> None:
    if ns.json:
        # Inherited from the shared parent parser, and meaningless here: one bare
        # line is the contract that makes `cd "$(kraft path ID)"` work. Rejected
        # rather than ignored, the way `watch` rejects it.
        raise ValueError("path has no --json; it prints one line — use `kraft show --json`")
    if ns.shell:
        print(SHELL_WRAPPER, end="")
        return
    item = asyncio.run(client.get_work_item(ns.id))
    # exactly one line: this is consumed by cd "$(kraft path ID)"
    print(item["worktree_path"])


def _cmd_open(ns: argparse.Namespace) -> None:
    emit(asyncio.run(client.open_worktree(ns.id, ns.editor)), _render_action, ns.json)


def _add_item(subs, common: argparse.ArgumentParser) -> None:
    """The verbs that change a work item."""
    create = subs.add_parser("create", parents=[common], help="file a work item (starts paused)")
    create.add_argument("title")
    create.add_argument(
        "--description",
        help="the brief: what the work actually is, which the spec is written from",
    )
    create.add_argument("--repo", help="default: the repo you are standing in")
    create.add_argument("--chain", default="default", help="chain template (default `default`)")
    # Two flags rather than a repeatable `--attach kind=path`: there are exactly
    # two kinds, the server refuses a duplicate kind, and these document
    # themselves in --help.
    create.add_argument("--spec", help="attach a spec that already exists; skips the spec node")
    create.add_argument("--plan", help="attach a plan that already exists; skips the plan node")
    create.set_defaults(func=_cmd_create, all=False)

    approve = subs.add_parser("approve", parents=[common], help="approve the pending gate")
    approve.add_argument("id", nargs="?")
    approve.add_argument("--gate", help="default: whichever gate is pending")
    approve.set_defaults(func=_cmd_approve)

    reject = subs.add_parser("reject", parents=[common], help="reject the pending gate")
    reject.add_argument("id", nargs="?")
    reject.add_argument("--note", required=True, help="what is wrong; a rejection needs a reason")
    reject.add_argument("--gate", help="default: whichever gate is pending")
    reject.add_argument(
        "--node", help="re-enter the chain at this node; default: the chain's own reject_to"
    )
    reject.set_defaults(func=_cmd_reject)

    pause = subs.add_parser("pause", parents=[common], help="stop the running attempt")
    pause.add_argument("id", nargs="?")
    pause.set_defaults(func=_cmd_pause)

    resume = subs.add_parser("resume", parents=[common], help="start or restart a paused item")
    resume.add_argument("id", nargs="?")
    resume.add_argument("--steer", help="carried into the next attempt's prompt")
    resume.set_defaults(func=_cmd_resume)

    retry = subs.add_parser(
        "retry", parents=[common], help="re-run the node a stopped item stopped on"
    )
    retry.add_argument("id", nargs="?")
    retry.add_argument("--steer", help="carried into the retry's prompt")
    retry.set_defaults(func=_cmd_retry)

    mr_label = subs.add_parser(
        "mr-label",
        parents=[common],
        help="label this item's merge request and re-create its pipeline",
    )
    # `--id`, not a positional: a positional `id` ahead of `labels` (nargs="+")
    # is ambiguous the moment two labels are given with no id — argparse's
    # greedy match eats the first label as the id.
    mr_label.add_argument("--id", help="default: the work item you are standing in")
    mr_label.add_argument("labels", nargs="+", help="e.g. release::patch")
    mr_label.set_defaults(func=_cmd_mr_label)

    abandon = subs.add_parser(
        "abandon", parents=[common], help="drop an item and reclaim its worktree"
    )
    abandon.add_argument("id", nargs="?")
    abandon.add_argument(
        "--yes", action="store_true", help="required: this destroys uncommitted work"
    )
    abandon.set_defaults(func=_cmd_abandon)


def _add_view(subs, common: argparse.ArgumentParser) -> None:
    """The verbs that only read: the board, one item, its documents and its streams."""
    listing = subs.add_parser("list", parents=[common], help="the board")
    listing.add_argument("--status", help="active, needs_human, paused or completed")
    listing.add_argument("--repo", help="only this repo (default: the repo you are standing in)")
    listing.add_argument("--all", action="store_true", help="every repo, ignoring the cwd")
    # Not folded into --all: that one widens the *repo* scope, and abandoning is
    # a different axis. Overloading it would make `--all` mean two things.
    listing.add_argument(
        "--include-abandoned", action="store_true", help="also show abandoned items"
    )
    listing.set_defaults(func=_cmd_list)

    show = subs.add_parser("show", parents=[common], help="one work item")
    show.add_argument("id", nargs="?", help="default: the work item this session is standing in")
    show.set_defaults(func=_cmd_show)

    search = subs.add_parser("search", parents=[common], help="specs, plans and session summaries")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=20)
    search.set_defaults(func=_cmd_search)

    logs = subs.add_parser("logs", parents=[common], help="a worker session's log")
    logs.add_argument("id", nargs="?", help="default: the work item you are standing in")
    logs.add_argument("-f", "--follow", action="store_true", help="follow until the session stops")
    logs.add_argument("--session", help="default: the most recent session of the work item")
    logs.add_argument(
        "-n", type=int, default=50, help="backlog lines before following (0 for none)"
    )
    logs.set_defaults(func=_cmd_logs)

    events_p = subs.add_parser("events", parents=[common], help="the chain's own history")
    events_p.add_argument("id", nargs="?")
    events_p.add_argument("--after", type=int, default=0, help="only events after this seq")
    events_p.add_argument("--type", help="only this event type")
    events_p.add_argument(
        "-f",
        "--follow",
        action="store_true",
        help="stream until the item completes or is abandoned",
    )
    events_p.set_defaults(func=_cmd_events)

    watch = subs.add_parser("watch", parents=[common], help="a live board, redrawn on each event")
    watch.add_argument("--repo", help="default: the repo you are standing in")
    watch.set_defaults(func=_cmd_watch, all=False)

    diff = subs.add_parser("diff", parents=[common], help="what the agent changed")
    diff.add_argument("id", nargs="?")
    diff.add_argument("--stat", action="store_true", help="per-file counts only")
    diff.add_argument("--name-only", action="store_true", help="changed and untracked paths")
    diff.add_argument("--no-pager", action="store_true")
    diff.set_defaults(func=_cmd_diff)

    docs = subs.add_parser("docs", parents=[common], help="documents linked to a work item")
    docs.add_argument("id", nargs="?")
    docs.set_defaults(func=_cmd_docs)

    doc = subs.add_parser("doc", parents=[common], help="print one document, or open it")
    doc.add_argument("doc_id")
    doc.add_argument(
        "--open",
        nargs="?",
        const="",
        metavar="EDITOR",
        help="open in an editor on the server (code, cursor, zed, obsidian; default: system)",
    )
    doc.add_argument("--no-pager", action="store_true")
    doc.set_defaults(func=_cmd_doc)

    artifact = subs.add_parser(
        "artifact", parents=[common], help="the document the pending gate is about"
    )
    artifact.add_argument("id", nargs="?")
    artifact.add_argument("--no-pager", action="store_true")
    artifact.set_defaults(func=_cmd_artifact)


def _add_repo(subs, common: argparse.ArgumentParser) -> None:
    """Connected repositories, and getting into their worktrees."""
    repos = subs.add_parser("list", parents=[common], help="connected repositories")
    repos.set_defaults(func=_cmd_repos)

    connect = subs.add_parser("connect", parents=[common], help="connect a repo (idempotent)")
    connect.add_argument("path", nargs="?", help="default: the current directory")
    connect.set_defaults(func=_cmd_connect)

    disconnect = subs.add_parser(
        "disconnect", parents=[common], help="forget a repo (work items are untouched)"
    )
    disconnect.add_argument("path", nargs="?", help="default: the current directory")
    disconnect.set_defaults(func=_cmd_disconnect)

    path = subs.add_parser(
        "path", aliases=["cd"], parents=[common], help="print a work item's worktree path"
    )
    path.add_argument("id", nargs="?")
    path.add_argument("--shell", action="store_true", help="print a shell function that cds")
    path.set_defaults(func=_cmd_path)

    open_p = subs.add_parser("open", parents=[common], help="open the worktree in an editor")
    open_p.add_argument("id", nargs="?")
    open_p.add_argument("--editor", help="code, cursor, zed, obsidian (default: system)")
    open_p.set_defaults(func=_cmd_open)


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

    init = subs.add_parser(
        "init", parents=[common], help="register Kraft's MCP server and skills with an agent"
    )
    init.add_argument("--repo", action="store_true", help="install into this repo, not the user")
    init.set_defaults(func=_cmd_init)

    mcp = subs.add_parser("mcp", help="serve the MCP tools over stdio")
    mcp.set_defaults(func=_cmd_mcp)


#: The four groups, in help order. `build_parser` walks this, so adding a group
#: is one tuple rather than a second place that has to agree with the first.
_GROUPS = (
    ("item", "act on a work item", _add_item),
    ("view", "read a work item, or the board", _add_view),
    ("repo", "connected repositories and their worktrees", _add_repo),
    ("admin", "this machine's server and its install", _add_admin),
)


#: The verbs that were top-level before the groups, and where each one went.
#: Data, not aliases: nothing here dispatches. It is checked before argparse
#: sees argv because argparse's own "invalid choice" prints the four group
#: names and says nothing about where `list` has gone.
MOVED = {
    "create": "item create",
    "approve": "item approve",
    "reject": "item reject",
    "pause": "item pause",
    "resume": "item resume",
    "retry": "item retry",
    "abandon": "item abandon",
    "list": "view list",
    "show": "view show",
    "search": "view search",
    "logs": "view logs",
    "events": "view events",
    "watch": "view watch",
    "diff": "view diff",
    "docs": "view docs",
    "doc": "view doc",
    "artifact": "view artifact",
    "repos": "repo list",
    "connect": "repo connect",
    "disconnect": "repo disconnect",
    "path": "repo path",
    "cd": "repo cd",
    "open": "repo open",
    "serve": "admin start",
    "health": "admin health",
    "doctor": "admin doctor",
    "reindex": "admin reindex",
    "init": "admin init",
    "mcp": "admin mcp",
}


def main(argv: list[str] | None = None) -> None:
    """Bare `kraft` serves, as it always has. Subcommands are the two front doors.

    The zero-argument check happens before argparse sees anything: serving must
    stay the default, and argparse would print usage for an empty argv. (This
    file used to avoid argparse entirely, on the grounds that one string compare
    was the whole dispatch. That stopped being true at eight verbs with flags.)
    """
    args = sys.argv[1:] if argv is None else list(argv)
    parser = build_parser()
    # No-ops unless COMP_LINE etc are set, i.e. unless a shell completion
    # script (see `register-python-argcomplete kraft`) is asking for
    # completions; in that case it prints them and exits, never reaching the
    # code below. Built before the zero-arg short-circuit so `kraft <TAB>`
    # completes rather than serving.
    argcomplete.autocomplete(parser)
    if not args:
        _serve()
        return
    if args[0] in MOVED:
        print(f"kraft: '{args[0]}' moved to `kraft {MOVED[args[0]]}`", file=sys.stderr)
        raise SystemExit(2)
    ns = parser.parse_args(args)
    try:
        ns.func(ns)
    except (ValueError, PermissionError) as exc:
        # ValueError is what client.py raises for every API and context failure;
        # PermissionError is the worker self-action guard.
        print(f"kraft: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except KeyboardInterrupt:
        raise SystemExit(130) from None
