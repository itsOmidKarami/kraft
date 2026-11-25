"""Kraft in Herdr: one tab per live work item, its state in Herdr's sidebar.

Stdlib only, and Python 3.9 at the floor: Herdr restarts a tab with the first
`python3` it finds, which on macOS is the system's 3.9. It imports nothing from Kraft: it
talks to Kraft through the `kraft` command and to Herdr through the `herdr`
command, so either can change inside and this keeps working.

Entry points (`herdr-plugin.toml`), each `python3 kraft_herdr.py <name>`:

  start     one-shot startup hook: open the bridge pane unless it is open
  bridge    the long-running loop that keeps the tabs and states current
  item      one item's tab: follow its worker sessions' log (reads KRAFT_ITEM)
  takeover  action: open `kraft item takeover` beside the focused item tab
"""

from __future__ import annotations

import fcntl
import json
import os
import queue
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path

#: The Herdr workspace every item tab lives in, and the identity a report
#: carries. `custom:` is the prefix Herdr's `report-agent` gives hook-fed agents.
WORKSPACE = "Kraft"
SOURCE = "custom:kraft"
AGENT = "kraft"
PLUGIN_ID = "kraft"

#: Items whose chain has ended. Their tab stays (a person closes it), idle.
FINISHED = frozenset({"done", "cancelled", "archived"})
#: Moving into one of these is the moment a person is needed.
NEEDS_A_PERSON = frozenset({"needs_you", "failed"})

#: Herdr truncates a tab's name itself; this keeps the label readable first.
LABEL_LIMIT = 60
TITLE_LIMIT, BODY_LIMIT = 80, 240

#: The bridge re-reads the board at least this often, and no more often than
#: DEBOUNCE while events arrive. The stream can drop a slow client (the server
#: closes it with 1011), so the periodic pass is what makes a missed event
#: cost at most a minute.
RECONCILE_EVERY = 60.0
DEBOUNCE = 1.0
#: Seconds between reconnects: doubles from the first to the second, and a
#: stream that stayed up a while starts over.
BACKOFF_MIN, BACKOFF_MAX = 1.0, 30.0

DEFAULT_CONFIG = {"notify": False, "kraft": "kraft"}

#: Kraft's `display_status` (`kraft.vocab.display.DisplayStatus`) to the state
#: Herdr understands. `report-agent` takes only idle, working, blocked and
#: unknown: Herdr derives "done" itself (idle and not yet seen), so a finished
#: item is idle. `blocked` is Herdr's word for "a person is needed", which is
#: Kraft's `needs_you` and `failed`; Kraft's own `blocked` (waiting on another
#: item) needs nobody, so it is idle.
STATES: dict[str, str] = {
    "running": "working",
    "escalated": "working",
    "needs_you": "blocked",
    "failed": "blocked",
    "paused": "idle",
    "waiting": "idle",
    "queued": "idle",
    "blocked": "idle",
    "done": "idle",
    "cancelled": "idle",
    "archived": "idle",
}

#: The label shown beside the state, where the item's own row has nothing
#: better to say.
_LABELS: dict[str, str] = {
    "running": "running",
    "escalated": "helper agent",
    "needs_you": "needs you",
    "failed": "failed",
    "paused": "paused",
    "waiting": "waiting",
    "queued": "queued",
    "blocked": "after another item",
    "done": "merged",
    "cancelled": "cancelled",
    "archived": "archived",
}

#: A status Kraft sends that this file has never heard of reads as unknown, not
#: as a crash: the bridge outlives Kraft upgrades.
UNKNOWN = "unknown"


def state_for(item: dict) -> tuple[str, str]:
    """`(herdr_state, label)` for one row of `kraft view list --json`."""
    status = item.get("display_status") or ""
    state = STATES.get(status, UNKNOWN)
    label = _LABELS.get(status, status or UNKNOWN)
    if status == "running" and item.get("current_node_id"):
        label = str(item["current_node_id"])
    elif status == "needs_you" and item.get("pending_gate"):
        label = str(item["pending_gate"])
    return state, label


def tab_label(item: dict) -> str:
    """The tab's name, and how a tab is found again after Herdr restarts."""
    return f"{item['id'][:8]} {item.get('title') or ''}".strip()[:LABEL_LIMIT]


def log(message: str) -> None:
    """The bridge's pane is its only log, and nothing restarts the bridge, so
    every line is flushed."""
    print(f"{time.strftime('%H:%M:%S')} {message}", flush=True)


class CommandFailed(RuntimeError):
    pass


class Herdr:
    """The `herdr` command, and nothing else of Herdr.

    Most commands answer with JSON of the shape `{"result": {...}}`; the keys
    read below are the ones the socket API schema names.
    """

    def __init__(self, binary: str | None = None) -> None:
        self.binary = binary or os.environ.get("HERDR_BIN_PATH") or "herdr"
        self.plugin = os.environ.get("HERDR_PLUGIN_ID") or PLUGIN_ID

    def run(self, *args: str) -> dict:
        done = subprocess.run([self.binary, *args], capture_output=True, text=True)
        if done.returncode:
            raise CommandFailed(
                f"herdr {' '.join(args[:3])}: {done.stderr.strip() or done.returncode}"
            )
        try:
            return json.loads(done.stdout)["result"] if done.stdout.strip() else {}
        except (ValueError, KeyError, TypeError):
            return {}

    def workspace_id(self, label: str) -> str:
        """The workspace with this label; one is made, without taking focus, when none is."""
        for workspace in self.run("workspace", "list").get("workspaces", []):
            if workspace.get("label") == label:
                return workspace["workspace_id"]
        made = self.run("workspace", "create", "--label", label, "--no-focus")
        return made["workspace"]["workspace_id"]

    def open_item_tab(self, workspace: str, item_id: str) -> str:
        """Open the `item` entrypoint as a tab. It has no argv of its own: the
        item travels in the environment."""
        opened = self.run(
            "plugin", "pane", "open", "--plugin", self.plugin, "--entrypoint", "item",
            "--workspace", workspace, "--placement", "tab",
            "--env", f"KRAFT_ITEM={item_id}", "--no-focus",
        )  # fmt: skip
        return opened["plugin_pane"]["pane"]["pane_id"]

    def rename(self, pane: str, label: str) -> None:
        self.run("pane", "rename", pane, label)

    def close(self, pane: str) -> None:
        self.run("pane", "close", pane)

    def report(self, pane: str, state: str, label: str, resume: list[str] | None = None) -> None:
        """Say what the item is doing. Herdr ignores a `--seq` that is not higher
        than the last it took, across restarts of this bridge too, so the
        sequence is the clock."""
        args = [
            "pane", "report-agent", pane, "--source", SOURCE, "--agent", AGENT,
            "--state", state, "--message", label,
            "--seq", str(time.time_ns() // 1_000_000),
        ]  # fmt: skip
        self.run(*args, *(["--", *resume] if resume else []))
        # The sidebar's label comes from metadata: `--message` only explains a
        # block. Herdr shows an idle agent nobody has looked at yet as `done`,
        # so an idle label is given for both. Each call replaces the whole map.
        shown = (state, "done") if state == "idle" else (state,)
        labels = [part for s in shown for part in ("--state-label", f"{s}={label}")]
        self.run("pane", "report-metadata", pane, "--source", SOURCE, "--agent", AGENT, *labels)

    def panes(self, workspace: str) -> list[dict]:
        return self.run("pane", "list", "--workspace", workspace).get("panes", [])

    def panes_by_label(self, workspace: str) -> dict[str, str]:
        return {p["label"]: p["pane_id"] for p in self.panes(workspace) if p.get("label")}

    def open_bridge_tab(self, workspace: str) -> str:
        opened = self.run(
            "plugin", "pane", "open", "--plugin", self.plugin, "--entrypoint", "bridge",
            "--workspace", workspace, "--placement", "tab", "--no-focus",
        )  # fmt: skip
        return opened["plugin_pane"]["pane"]["pane_id"]

    def split(self, pane: str) -> str:
        """A new shell beside `pane`, focused: a person is about to type there."""
        made = self.run("pane", "split", pane, "--direction", "right", "--focus")
        return made["pane"]["pane_id"]

    def run_in(self, pane: str, command: str) -> None:
        self.run("pane", "run", pane, command)

    def notify(self, title: str, body: str) -> None:
        self.run(
            "notification", "show", title[:TITLE_LIMIT], "--body", body[:BODY_LIMIT],
            "--sound", "request",
        )  # fmt: skip


def acquire_lock(state_dir: Path):
    """Take `bridge.lock`, or None when another bridge holds it. Herdr runs the
    startup hook again on a live handoff, and two bridges would report
    over each other. The lock goes when the process does, so a bridge that
    was killed leaves nothing to clean up."""
    state_dir.mkdir(parents=True, exist_ok=True)
    handle = open(state_dir / "bridge.lock", "w")  # noqa: SIM115 -- held for the life of the process
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


class Bridge:
    """Keeps Herdr's tabs and agent states equal to Kraft's board."""

    def __init__(self, herdr: Herdr, kraft: str, state_dir: Path, *, notify: bool = False) -> None:
        self.herdr, self.kraft, self.state_dir, self.notify = herdr, kraft, state_dir, notify

    # -- the file that outlives a restart ------------------------------------

    @property
    def _panes_file(self) -> Path:
        return self.state_dir / "panes.json"

    def _load(self) -> dict:
        try:
            data = json.loads(self._panes_file.read_text())
            return data if isinstance(data.get("items"), dict) else {"items": {}}
        except (OSError, ValueError, AttributeError):
            return {"items": {}}

    def _save(self, data: dict) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        scratch = self._panes_file.with_suffix(".tmp")
        scratch.write_text(json.dumps(data, indent=2))
        scratch.replace(self._panes_file)

    # -- Kraft ---------------------------------------------------------------

    def _kraft(self, *args: str) -> str:
        done = subprocess.run([self.kraft, *args], capture_output=True, text=True)
        if done.returncode:
            raise CommandFailed(
                f"kraft {' '.join(args[:3])}: {done.stderr.strip() or done.returncode}"
            )
        return done.stdout

    def _board(self) -> list[dict]:
        # --all: without it the list is scoped to the repo the bridge runs in.
        # --include-abandoned: a cancelled item is stored abandoned, and would
        # otherwise vanish from the list as if it were archived.
        return json.loads(self._kraft("view", "list", "--all", "--include-abandoned", "--json"))

    def _stop_reason(self, item_id: str, fallback: str) -> str:
        try:
            shown = json.loads(self._kraft("view", "show", item_id, "--json"))
            return str(shown.get("stop_reason") or fallback)
        except (CommandFailed, ValueError, AttributeError):
            return fallback

    # -- the reconcile -------------------------------------------------------

    def reconcile(self) -> None:
        """Make every unfinished item have a tab, report every tab's state, and
        report an item that left the board finished, once."""
        board = self._board()
        workspace = self.herdr.workspace_id(WORKSPACE)
        panes = self.herdr.panes(workspace)
        by_label = {p["label"]: p["pane_id"] for p in panes if p.get("label")}
        alive = {p["pane_id"] for p in panes}
        data = self._load()
        records: dict[str, dict] = data["items"]
        seen = set()
        for item in board:
            seen.add(item["id"])
            try:
                self._one(item, workspace, by_label, alive, records)
            except CommandFailed as exc:
                log(f"{item['id'][:8]}: {exc}")
        for item_id, record in records.items():
            if item_id not in seen:
                try:
                    self._finish(record, "archived", alive)
                except CommandFailed as exc:
                    log(f"{item_id[:8]}: {exc}")
        self._save(data)

    def _finish(self, record: dict, status: str, alive: set[str]) -> None:
        """Say a tab's item has ended, once. A tab someone closed has nothing to say."""
        if record.get("finished") or not record.get("pane"):
            return
        if record["pane"] in alive:
            self.herdr.report(record["pane"], "idle", state_for({"display_status": status})[1])
        record.update(finished=True, status=status)

    def _one(self, item, workspace, by_label, alive, records) -> None:
        status = item.get("display_status") or ""
        if status in FINISHED:
            # No record means a finished item that never had a tab: it gets none.
            if record := records.get(item["id"]):
                self._finish(record, status, alive)
            return
        record = records.setdefault(item["id"], {})
        label = tab_label(item)
        pane = by_label.get(label)
        if pane is None and record.get("pane") in alive:
            pane = record["pane"]  # the title changed: same tab, new name
            self.herdr.rename(pane, label)
        if pane is None:
            pane = self.herdr.open_item_tab(workspace, item["id"])
            # Written down before the rename: a rename that fails must not leave
            # a tab the next pass cannot find and so opens a second of.
            record["pane"] = pane
            by_label[label] = pane
            alive.add(pane)
            self.herdr.rename(pane, label)
        state, text = state_for(item)
        self.herdr.report(pane, state, text, resume=resume_command(item["id"]))
        before = record.get("status")
        record.update(pane=pane, label=label, status=status, finished=False)
        if self.notify and before and before != status and status in NEEDS_A_PERSON:
            reason = self._stop_reason(item["id"], text)
            self.herdr.notify(item.get("title") or item["id"], reason)

    # -- the loop ------------------------------------------------------------

    def run(self, *, max_cycles: int | None = None) -> None:
        """Hold the stream open for ever. `max_cycles` is for tests only."""
        delay, cycles = BACKOFF_MIN, 0
        while max_cycles is None or cycles < max_cycles:
            cycles += 1
            started = time.monotonic()
            try:
                self._follow_once()
            except Exception as exc:  # nothing restarts the bridge: it survives everything
                log(f"error: {exc}")
            if time.monotonic() - started > BACKOFF_MAX:
                delay = BACKOFF_MIN
            log(f"stream ended; reconnecting in {delay:.0f}s")
            time.sleep(delay)
            delay = min(delay * 2, BACKOFF_MAX)

    def _reconcile_quietly(self) -> None:
        try:
            self.reconcile()
        except Exception as exc:
            log(f"error: {exc}")

    def _follow_once(self) -> None:
        stream = subprocess.Popen(
            [self.kraft, "view", "events", "--all", "-f", "--json"],
            stdout=subprocess.PIPE, text=True,
        )  # fmt: skip
        lines: queue.Queue[str | None] = queue.Queue()

        def pump() -> None:
            for line in stream.stdout:
                lines.put(line)
            lines.put(None)

        threading.Thread(target=pump, daemon=True).start()
        try:
            # After subscribing, so an event between the two is not lost.
            self._reconcile_quietly()
            last, dirty = time.monotonic(), False
            while True:
                now = time.monotonic()
                wait = RECONCILE_EVERY - (now - last)
                if dirty:
                    wait = min(wait, DEBOUNCE - (now - last))
                try:
                    line = lines.get(timeout=max(wait, 0.0))
                except queue.Empty:
                    line = ""
                if line is None:
                    return
                dirty = dirty or bool(line.strip())
                now = time.monotonic()
                if (dirty and now - last >= DEBOUNCE) or now - last >= RECONCILE_EVERY:
                    self._reconcile_quietly()
                    last, dirty = time.monotonic(), False
        finally:
            stream.terminate()
            stream.wait()


def _show(kraft: str, item_id: str) -> dict | None:
    """The whole item, or None (after saying why) when Kraft cannot be asked."""
    done = subprocess.run(
        [kraft, "view", "show", item_id, "--json"], capture_output=True, text=True
    )
    try:
        if done.returncode:
            raise ValueError(done.stderr.strip() or f"exit {done.returncode}")
        return json.loads(done.stdout)
    except ValueError as exc:
        print(f"kraft view show: {exc}", file=sys.stderr, flush=True)
        return None


def _follow(kraft: str, item_id: str, session_id: str) -> tuple[int, str]:
    """Show one session's log until it ends: the log goes straight to the pane,
    and stderr is passed on as it comes and kept so the caller can read it."""
    proc = subprocess.Popen(
        [kraft, "view", "logs", item_id, "--session", session_id, "-f"],
        stderr=subprocess.PIPE, text=True,
    )  # fmt: skip
    kept = []
    for line in proc.stderr:
        kept.append(line)
        sys.stderr.write(line)
        sys.stderr.flush()
    return proc.wait(), "".join(kept)


def item_tab(kraft: str, item_id: str, *, poll: float = 5.0, max_rounds: int | None = None) -> None:
    """One item's tab: its newest session's log, then the next one's, for ever.

    It never returns on its own. If this process ended, the pane would fall back
    to a shell and Herdr would drop the agent it was told about, so an item with
    no session yet, a finished one and a stopped one all wait here instead.
    `max_rounds` is for tests only.
    """
    followed = announced = None
    rounds = 0
    while max_rounds is None or rounds < max_rounds:
        rounds += 1
        item = _show(kraft, item_id)
        if item is None:
            time.sleep(poll)
            continue
        sessions = item.get("worker_sessions") or []
        newest = sessions[-1]["id"] if sessions else None
        if newest and newest != followed:
            code, err = _follow(kraft, item_id, newest)
            if code == 0:
                followed = newest
            else:
                # Not a session yet, or Kraft went away mid-stream: ask again.
                time.sleep(poll)
            continue
        status = item.get("display_status") or item.get("status") or ""
        if status in FINISHED or item.get("status") in ("completed", "abandoned"):
            if announced != status:
                print(f"finished: {status}", flush=True)
                announced = status
        else:
            if newest is None and announced != "waiting":
                print("waiting for a worker session to start...", flush=True)
                announced = "waiting"
            elif newest is not None:
                announced = None
        time.sleep(poll)


def item_main(args: list[str]) -> int:
    item_id = args[0] if args else os.environ.get("KRAFT_ITEM", "")
    if not item_id:
        print("kraft_herdr item: no work item (set KRAFT_ITEM)", file=sys.stderr)
        return 2
    config = load_config(_dir("HERDR_PLUGIN_CONFIG_DIR"))
    try:
        item_tab(config["kraft"], item_id)
    except KeyboardInterrupt:
        return 130
    return 0


#: What names the bridge's own tab, so the next `start` finds it.
BRIDGE_LABEL = "kraft bridge"


def start(herdr: Herdr, *, state_dir: Path | None = None) -> int:
    """The startup hook. Herdr runs it once, and again after a live handoff,
    and does not supervise it: so it only makes sure a bridge is running, and
    leaves the loop to that pane. The bridge's lock says whether one is, not the
    tab's name: after a restart of its server Herdr brings the tab back as a
    plain shell, so a free lock means the old tab goes and a new one opens."""
    lock = acquire_lock(state_dir or _dir("HERDR_PLUGIN_STATE_DIR"))
    if lock is None:
        return 0  # a bridge holds it
    lock.close()
    workspace = herdr.workspace_id(WORKSPACE)
    if (stale := herdr.panes_by_label(workspace).get(BRIDGE_LABEL)) is not None:
        herdr.close(stale)
    herdr.rename(herdr.open_bridge_tab(workspace), BRIDGE_LABEL)
    return 0


def _focused_pane(context: dict) -> str:
    """Where the person was when they ran the action. The context names it
    `focused_pane_id`; HERDR_PANE_ID is the same fact from the environment."""
    return str(context.get("focused_pane_id") or os.environ.get("HERDR_PANE_ID") or "")


def takeover(herdr: Herdr, context: dict, *, state_dir: Path | None = None) -> int:
    """Open `kraft item takeover` for the item whose tab has focus, in a pane
    beside it. The tab itself keeps following the log."""
    pane = _focused_pane(context)
    items = Bridge(herdr, "kraft", state_dir or _dir("HERDR_PLUGIN_STATE_DIR"))._load()["items"]
    item_id = next((i for i, r in items.items() if pane and r.get("pane") == pane), None)
    if item_id is None:
        print("Kraft: the focused pane is not a Kraft item tab.", file=sys.stderr)
        return 1
    config = load_config(_dir("HERDR_PLUGIN_CONFIG_DIR"))
    command = " ".join(shlex.quote(part) for part in (config["kraft"], "item", "takeover", item_id))
    herdr.run_in(herdr.split(pane), command)
    return 0


def resume_command(item_id: str) -> list[str]:
    """What Herdr runs to bring an item tab back after a restart of its server.
    Herdr refuses one that starts with a path, so it is the manifest's plain
    `python3`, and the script goes by its full path."""
    return ["python3", str(Path(__file__).resolve()), "item", item_id]


def load_config(config_dir: Path) -> dict:
    """`config.json` over the defaults. A missing or broken file is the defaults:
    the bridge has no one to ask."""
    try:
        data = json.loads((config_dir / "config.json").read_text())
    except (OSError, ValueError):
        return dict(DEFAULT_CONFIG)
    return {**DEFAULT_CONFIG, **{k: v for k, v in data.items() if k in DEFAULT_CONFIG}}


def _dir(name: str) -> Path:
    return Path(os.environ.get(name) or ".")


def bridge_main() -> int:
    state_dir = _dir("HERDR_PLUGIN_STATE_DIR")
    lock = acquire_lock(state_dir)
    if lock is None:
        log("another bridge is running; exiting")
        return 0
    config = load_config(_dir("HERDR_PLUGIN_CONFIG_DIR"))
    Bridge(Herdr(), config["kraft"], state_dir, notify=bool(config["notify"])).run()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    entry = {
        "start": lambda: start(Herdr()),
        "bridge": bridge_main,
        "item": lambda: item_main(args[1:]),
        "takeover": lambda: takeover(Herdr(), _context()),
    }.get(args[0] if args else "")
    if entry is None:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        return entry()
    except CommandFailed as exc:
        print(f"kraft_herdr: {exc}", file=sys.stderr)
        return 1


def _context() -> dict:
    try:
        return json.loads(os.environ.get("HERDR_PLUGIN_CONTEXT_JSON") or "{}")
    except ValueError:
        return {}


if __name__ == "__main__":
    raise SystemExit(main())
