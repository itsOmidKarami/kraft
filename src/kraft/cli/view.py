"""The verbs that only read: the board, one item, its documents and its streams."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from kraft import client, render, usage
from kraft.cli import common
from kraft.vocab import StopKind, WorkItemEvent, WorkItemStatus

#: argparse choices; a typo answers an error, not an empty board. Plain strings,
#: not members: argparse prints `repr(choice)` in its error.
STATUSES = tuple(s.value for s in WorkItemStatus)

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
            "current_node_id": (
                f"{item['current_node_id']} {p['current']}/{p['total']}"
                if (p := item.get("progress"))
                else item["current_node_id"]
            ),
        }
        for item in items
    ]
    return render.table(painted, _LIST_COLUMNS)


_PROGRESS_MARKS = {"done": "✓", "current": "▸", "pending": "·"}


def _progress_text(p: dict) -> str:
    lines = [f"{p['current']} of {p['total']} · {p['title']}"]
    lines += [f"{_PROGRESS_MARKS[t['state']]} {t['n']}. {t['title']}" for t in p.get("tasks", [])]
    return "\n".join(lines)


#: The one command each `suggested_action` names (Kraft-s7c04.27).
_SUGGESTED_VERBS = {"skip": "skip", "retry": "retry", "abandon": "abandon --yes"}


def _suggestion_text(item: dict) -> str:
    s = item["suggested_action"]
    head = f"{s['action']}: {s['reason']}" if s["reason"] else s["action"]
    return f"{head}\nrun: kraft item {_SUGGESTED_VERBS[s['action']]} {item['id']}"


def _usage_text(u: dict) -> str:
    """Every kind of token apart (Ruling 211). A session from before the split
    counts its cache use under `in`, and the line says so. So does an output
    count that is missing a session's: a floor, or not known at all."""
    total = sum(u.get(k, 0) for k in usage.KINDS)
    out = f"{u['tokens_out']:,} out"
    if not u.get("out_complete", True):
        out = f"at least {out}" if u["tokens_out"] else "out not known"
    parts = [
        f"{total:,} tokens",
        f"{u['tokens_in']:,} in"
        + ("" if u.get("split_complete", True) else " (cache not split on older sessions)"),
        f"{u.get('tokens_cache_write', 0):,} cache write",
        f"{u.get('tokens_cache_read', 0):,} cache read",
        out,
    ]
    if u.get("cost_usd"):
        # As the stop reasons and the web UI's meter print it (`format.ts` `usd`).
        cost = usage.usd(u["cost_usd"])
        if u.get("cost_estimated"):
            parts.append(f"~{cost} (est.)")
        else:
            parts.append(cost if u.get("cost_complete", True) else f"at least {cost}")
    return " · ".join(parts)


def _show_value(item: dict, key: str, value) -> str:
    if key == "progress" and value:
        return _progress_text(value)
    if key == "usage" and value:
        return _usage_text(value)
    if key == "suggested_action" and value:
        return _suggestion_text(item)
    if key == "title" and value:
        # Every 2.0 door refuses an escape or a bidi override in a title, but
        # 1.4 stored one as typed: printed raw it reaches the terminal. `view
        # list` drops them (`render._cell`); so does this. `--json` is the row.
        return "\n".join(render.plain_text(line) for line in str(value).splitlines())
    if key == "dependencies":
        return ", ".join(f"{d['id']} ({d['status']})" for d in value or []) or "none"
    if key == "queued" and value:
        return f"for {value['verb']}, since {value['since']}"
    if key == "concerns" and value:
        # An agent wrote these, so like a title they are printed as plain text.
        return "\n".join(
            render.plain_text(line) for concern in value for line in str(concern).splitlines()
        )
    return str(value)


def _render_show(item: dict, full: dict | None = None) -> str:
    pairs = [(key, _show_value(item, key, value)) for key, value in item.items()]
    if full is not None and (hint := _raise_hint(full)) and not item.get("suggested_action"):
        at = next((i + 1 for i, (key, _) in enumerate(pairs) if key == "stop_reason"), len(pairs))
        pairs.insert(at, ("next", hint))
    return render.kv(pairs)


def _raise_hint(item: dict) -> str | None:
    """The command for a budget stop the item can raise: its own cap, or its
    policy's item-wide `budget_usd`. The board's Raise cap button, which a CLI
    reader was never pointed at (R10a-10). A node's, a token or the daily cap
    is not one `raise-budget` takes (`item/status.ts`'s `budgetRaise`)."""
    stop = item.get("stop") or {}
    if item.get("status") != WorkItemStatus.NEEDS_HUMAN or stop.get("kind") != StopKind.BUDGET:
        return None
    if not (stop.get("limit") or stop.get("scope") == "work_item"):
        return None
    return (
        f"kraft item raise-budget {item['id']} --usd N (raises the cap that stopped it, "
        "and retries it; --usd none lifts it)"
    )


def _render_search(payload: dict) -> str:
    rows = [
        {"kind": hit.get("kind"), "repo": hit.get("repo"), "path": hit.get("path")}
        for hit in payload.get("results", [])
    ]
    table = render.table(rows, [("KIND", "kind"), ("REPO", "repo"), ("PATH", "path")])
    # A hybrid search that fell back to text because the model is failing.
    return f"{table}\nnote: {payload['note']}" if payload.get("note") else table


def _cmd_list(ns: argparse.Namespace) -> None:
    repo = common.repo_scope(ns)
    items = asyncio.run(client.list_work_items(ns.status, include_abandoned=ns.include_abandoned))
    if repo:
        items = [item for item in items if item["repo"] == repo]
    common.emit(items, _render_list, ns.json)


def _cmd_show(ns: argparse.Namespace) -> None:
    # The whole detail either way, one request: the table prints the trimmed
    # fields, and reads the stop's kind off the rest.
    item = asyncio.run(client.get_work_item(ns.id, full=True))
    if ns.json:
        common.emit(item, str, True)
        return
    print(_render_show(client.trim_work_item(item), item))


def _cmd_search(ns: argparse.Namespace) -> None:
    common.emit(asyncio.run(client.search(ns.query, ns.limit)), _render_search, ns.json)


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


async def _logs_session(work_item_id: str | None, follow: bool) -> str:
    """The session `view logs` reads with no `--session`: the newest one, or,
    while the item is stopped, the one that stopped it, as the board's stop
    card names it. An auto-escalation starts the moment an item stops, so the
    newest log was the escalation's and not why it stopped (R12a-03). `-f`
    still follows the newest, the one that can still write. Whichever of the
    two it passes over is named on stderr, with how to read it."""
    item = await client.get_work_item(work_item_id, full=True)
    sessions = item.get("worker_sessions") or []
    if not sessions:
        raise ValueError("no worker session has run for this work item yet")
    newest = sessions[-1]
    stop = item.get("stop") if item.get("status") == WorkItemStatus.NEEDS_HUMAN else None
    stopped = newest
    if stop:
        named = (stop.get("facts") or {}).get("session_id")
        stopped = next((s for s in sessions if s["id"] == named), None) or next(
            (
                s
                for s in reversed(sessions)
                if s["node_id"] == stop.get("node") and s["hook_point"] != "escalation"
            ),
            newest,
        )
    if stopped is newest:
        return newest["id"]
    shown, other = (newest, stopped) if follow else (stopped, newest)
    print(
        f"showing {shown['hook_point']} ({shown['status']}); "
        f"the session that stopped the item is {stopped['hook_point']} ({stopped['status']}), "
        f"the newest is {newest['hook_point']} ({newest['status']}): "
        f"kraft view logs --session {other['id']}",
        file=sys.stderr,
    )
    return shown["id"]


def _cmd_logs(ns: argparse.Namespace) -> None:
    async def run() -> None:
        session_id = ns.session or await _logs_session(ns.id, ns.follow)
        lines = await client.log_backlog(session_id, ns.n)
        for entry in lines:
            _print_log(entry, ns.json)
        if ns.follow:
            seen = lines[-1]["n"] + 1 if lines else await client.log_next_line(session_id)
            async for entry in client.stream_log(session_id, after_line=seen):
                _print_log(entry, ns.json)

    asyncio.run(run())


#: The events after which a chain produces no more of them. A follow that
#: outlives the item is worse than no follow -- it is a monitor that stays
#: armed forever on work that finished.
_CHAIN_ENDED = (WorkItemEvent.COMPLETED, WorkItemEvent.ABANDONED)


def _cmd_events(ns: argparse.Namespace) -> None:
    async def run() -> None:
        wid = await client.resolve_work_item(ns.id)
        rows = await client.events(wid, ns.after)
        shown = [row for row in rows if row.get("type") == ns.type] if ns.type else rows
        if ns.follow and ns.json:
            # One stream, one shape: the stream below is NDJSON, so the backlog
            # is too. An indented array first made the whole output neither.
            for row in shown:
                _print_event(row, True)
        else:
            common.emit(shown, render.event_line, ns.json)
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
    if not sys.stdout.isatty():
        raise ValueError(
            "watch needs a terminal to redraw in — try `kraft view events -f` in a pipe"
        )

    async def run() -> None:
        repo = client.absolute_path(ns.repo) if ns.repo else await client.resolve_repo()
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


def _ws(ns: argparse.Namespace) -> dict:
    return {"ignore_whitespace": True} if ns.ignore_whitespace else {}


def _cmd_diff(ns: argparse.Namespace) -> None:
    payload = asyncio.run(client.diff(ns.id, **_ws(ns)))
    if ns.json:
        common.emit(payload, str, True)
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


def _cmd_threads(ns: argparse.Namespace) -> None:
    payload = asyncio.run(client.threads(ns.id, ns.open_only))
    common.emit(payload, render.threads, ns.json)


def _cmd_compare(ns: argparse.Namespace) -> None:
    payload = asyncio.run(client.compare(ns.id, ns.from_, ns.to, ns.nodes, **_ws(ns)))
    if ns.json:
        common.emit(payload, str, True)
        return
    if ns.name_only:
        paths = list(
            dict.fromkeys(
                [f["path"] for f in payload.get("files", [])] + list(payload.get("untracked", []))
            )
        )
        print("\n".join(paths))
        return
    text = render.compare_stat(payload) if ns.stat else render.compare_body(payload)
    render.page(text, force_plain=ns.no_pager)


def _cmd_docs(ns: argparse.Namespace) -> None:
    if ns.attachment:
        payload = asyncio.run(client.attachment(ns.attachment, ns.id))
        if ns.json:
            common.emit(payload, str, True)
            return
        render.page(payload.get("content", ""), force_plain=ns.no_pager)
        return
    docs = asyncio.run(client.documents(ns.id))
    if ns.json:
        common.emit(docs, str, True)
        return
    print(_docs_text(docs, asyncio.run(client.get_work_item(ns.id))))


def _docs_text(docs: list[dict], item: dict) -> str:
    """The indexed documents, then what was attached at filing that the index
    does not hold yet: before an item starts, that is all it has, and a bare
    "(nothing)" read as though it had no spec (R10F-06)."""
    indexed = {d.get("path") for d in docs}
    attached = [a for a in item.get("attachments") or [] if a.get("path") not in indexed]
    if not attached:
        return _render_docs(docs)
    blocks = [_render_docs(docs)] if docs else []
    blocks.append(
        "attached when it was filed:\n"
        + render.table(attached, [("KIND", "kind"), ("PATH", "path")])
        + f"\nread one: kraft view docs {item['id']} --attachment {attached[0]['kind']}"
    )
    return "\n\n".join(blocks)


def _cmd_doc(ns: argparse.Namespace) -> None:
    if ns.open is not None:
        editor = ns.open or None  # `--open` alone means the server's default
        result = asyncio.run(client.open_document(ns.doc_id, editor))
        common.emit(result, common._render_action, ns.json)
        return
    doc = asyncio.run(client.document(ns.doc_id))
    if ns.json:
        common.emit(doc, str, True)
        return
    render.page(doc.get("content", ""), force_plain=ns.no_pager)


def _cmd_artifact(ns: argparse.Namespace) -> None:
    payload = asyncio.run(client.artifact(ns.id))
    if ns.json:
        common.emit(payload, str, True)
        return
    render.page(render.artifact_body(payload), force_plain=ns.no_pager)


def _cmd_storage(ns: argparse.Namespace) -> None:
    common.emit(asyncio.run(client.storage_usage()), render.storage_block, ns.json)


def _add_view(subs, common: argparse.ArgumentParser) -> None:
    """The verbs that only read: the board, one item, its documents and its streams."""
    listing = subs.add_parser("list", parents=[common], help="the board")
    listing.add_argument("--status", choices=STATUSES, metavar="STATUS", help="one of: %(choices)s")
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
    logs.add_argument(
        "--session",
        help="default: the newest; while the item is stopped (and no -f), the one that stopped it",
    )
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

    # No `--json` (no parent parser): a redrawn board has no payload to print;
    # `view events -f --json` is the structured stream.
    watch = subs.add_parser("watch", help="a live board, redrawn on each event")
    watch.add_argument("--repo", help="default: the repo you are standing in")
    watch.set_defaults(func=_cmd_watch, all=False)

    diff = subs.add_parser("diff", parents=[common], help="what the agent changed")
    diff.add_argument("id", nargs="?")
    diff.add_argument("--stat", action="store_true", help="per-file counts only")
    diff.add_argument("--name-only", action="store_true", help="changed and untracked paths")
    diff.add_argument(
        "--ignore-whitespace", "-w", action="store_true", help="leave out whitespace-only changes"
    )
    diff.add_argument("--no-pager", action="store_true")
    diff.set_defaults(func=_cmd_diff)

    threads = subs.add_parser("threads", parents=[common], help="review threads on a work item")
    threads.add_argument("id", nargs="?")
    threads.add_argument(
        "--open", dest="open_only", action="store_true", help="only unresolved threads"
    )
    threads.set_defaults(func=_cmd_threads)

    compare = subs.add_parser("compare", parents=[common], help="diff two review targets")
    compare.add_argument("id", nargs="?")
    compare.add_argument(
        "--from", dest="from_", default="base", help="base|attempt:N|last_review|latest"
    )
    compare.add_argument("--to", default="latest", help="base|attempt:N|last_review|latest")
    compare.add_argument("--nodes", help="comma-separated node ids: only files they touched")
    compare.add_argument("--stat", action="store_true", help="per-file counts only")
    compare.add_argument("--name-only", action="store_true", help="changed and untracked paths")
    compare.add_argument(
        "--ignore-whitespace", "-w", action="store_true", help="leave out whitespace-only changes"
    )
    compare.add_argument("--no-pager", action="store_true")
    compare.set_defaults(func=_cmd_compare)

    docs = subs.add_parser(
        "docs",
        parents=[common],
        help="documents linked to a work item, and the spec or plan it was filed with",
    )
    docs.add_argument("id", nargs="?")
    docs.add_argument(
        "--attachment",
        choices=["spec", "plan"],
        help="print the spec or plan attached when the item was filed, as Kraft stored it",
    )
    docs.add_argument("--no-pager", action="store_true")
    docs.set_defaults(func=_cmd_docs)

    doc = subs.add_parser("doc", parents=[common], help="print one document, or open it")
    doc.add_argument("doc_id")
    doc.add_argument(
        "--open",
        nargs="?",
        const="",
        metavar="EDITOR",
        help=(
            "open in an editor on the server (code, cursor, zed, obsidian, or system for "
            "the OS opener; default: the Default editor in Settings, else KRAFT_EDITOR, "
            "else system)"
        ),
    )
    doc.add_argument("--no-pager", action="store_true")
    doc.set_defaults(func=_cmd_doc)

    artifact = subs.add_parser(
        "artifact", parents=[common], help="the document the pending gate is about"
    )
    artifact.add_argument("id", nargs="?")
    artifact.add_argument("--no-pager", action="store_true")
    artifact.set_defaults(func=_cmd_artifact)

    storage = subs.add_parser(
        "storage", parents=[common], help="what the worktrees use against the quota and limit"
    )
    storage.set_defaults(func=_cmd_storage)
