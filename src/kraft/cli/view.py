"""The verbs that only read: the board, one item, its documents and its streams."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from kraft import client, render
from kraft.cli import common

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
    lines = [f"Task {p['current']}/{p['total']} — {p['title']}"]
    lines += [f"{_PROGRESS_MARKS[t['state']]} {t['n']}. {t['title']}" for t in p.get("tasks", [])]
    return "\n".join(lines)


def _render_show(item: dict) -> str:
    return render.kv(
        [
            (key, _progress_text(value) if key == "progress" and value else str(value))
            for key, value in item.items()
        ]
    )


def _render_search(payload: dict) -> str:
    rows = [
        {"kind": hit.get("kind"), "repo": hit.get("repo"), "path": hit.get("path")}
        for hit in payload.get("results", [])
    ]
    return render.table(rows, [("KIND", "kind"), ("REPO", "repo"), ("PATH", "path")])


def _cmd_list(ns: argparse.Namespace) -> None:
    repo = common.repo_scope(ns)
    items = asyncio.run(client.list_work_items(ns.status, include_abandoned=ns.include_abandoned))
    if repo:
        items = [item for item in items if item["repo"] == repo]
    common.emit(items, _render_list, ns.json)


def _cmd_show(ns: argparse.Namespace) -> None:
    common.emit(asyncio.run(client.get_work_item(ns.id)), _render_show, ns.json)


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


def _cmd_docs(ns: argparse.Namespace) -> None:
    common.emit(asyncio.run(client.documents(ns.id)), _render_docs, ns.json)


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
