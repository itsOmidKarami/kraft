"""Kraft's replies to the review bots, posted at the two posting points of a node
with a fix loop (`walk`): each time a measurement of it returns, and when it
would complete.

Each posting point reads what the node's latest triage and its fix cycles wrote,
creates each reply's intent once (found again by its key at every later posting
point, so a wait parked and woken ten times still posts once), and sends it
through the reply record, the gate and the body gate (`adapters.forge.replies`).
It never parks, stops or raises into the walk: what is not sent waits for the
next posting point of the same node run, and what is left when the node completes
is listed in the reply report the brief reads (part 2b).

A node whose review wait names no bot with replies on returns before anything
else: no forge call, no event."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Collection
from dataclasses import dataclass, field
from pathlib import Path

from kraft import events, inplace, store
from kraft import findings as _findings
from kraft import forges as forges_mod
from kraft.adapters.forge import run as forge_run
from kraft.adapters.forge.models import ForgeError
from kraft.adapters.forge.mr import same_login
from kraft.adapters.forge.replies import body, facts, gate, record, send
from kraft.ci_reruns import node_run_start
from kraft.config import git_read
from kraft.executor import dispatch
from kraft.executor import round_prompts as prompts
from kraft.templates.models import AgentInput, AgentTask, ForgeAction, ForgeTask
from kraft.vocab import ADVANCING, ForgeEvent
from kraft.vocab.sql import marks
from kraft.worker.worktree_read import read_no_follow

logger = logging.getLogger(__name__)

_LEADS = {"declined": "Declined:", "answered": "Answer:"}


def _result(path) -> dict:
    """A worker's result file, read without following a link; {} when unreadable."""
    try:
        data = json.loads(read_no_follow(Path(path)))
    except (OSError, ValueError, RecursionError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _rows(data: dict, key: str) -> list[dict]:
    raw = data.get(key)
    return [r for r in raw if isinstance(r, dict)] if isinstance(raw, list) else []


def _text(row: dict) -> str | None:
    body = row.get("body")
    return body if isinstance(body, str) and body.strip() else None


def triage_replies(result_path, threads: Collection[str]) -> list[dict]:
    """The triage's `replies`, one per collected thread (the first wins), each with
    an allowed `kind` and a body."""
    out: dict[str, dict] = {}
    for r in _rows(_result(result_path), "replies"):
        tid = r.get("thread")
        if (
            isinstance(tid, str)
            and tid in threads
            and tid not in out
            and r.get("kind") in gate.TRIAGE_KINDS
            and _text(r)
        ):
            out[tid] = {"thread": tid, "kind": r["kind"], "body": r["body"]}
    return list(out.values())


def _own(node) -> list:
    """The node's own tasks: never its recovery's or its fix loop's."""
    return [t for step in node.steps for t in step.tasks]


def _review_wait(node):
    return next(
        (
            t
            for t in _own(node)
            if isinstance(t.task, ForgeTask) and t.task.target is ForgeAction.MR_AUTOMATED_REVIEW
        ),
        None,
    )


def _triage(node):
    return next(
        (
            t
            for t in _own(node)
            if isinstance(t.task, AgentTask) and AgentInput.BOT_FEEDBACK in t.task.inputs
        ),
        None,
    )


def _run_start(c, work_item_id: str, node_id: str) -> tuple[int, str]:
    """Where the node's current run began: its event's seq, and its time, since a
    session carries only a time (the comparison `bot_feedback_source` makes too)."""
    seq = node_run_start(c, work_item_id, node_id)
    row = (
        c.execute("SELECT created_at FROM events WHERE seq = ?", (seq,)).fetchone() if seq else None
    )
    return seq, row["created_at"] if row else ""


def latest_triage(c, work_item_id: str, node, since: str):
    """The node's newest triage session since `since` that started an agent and
    finished with a result: a head and an advancing status, `last_review_session`'s
    filter. A skipped session has no head, so it is never the latest triage."""
    hook = _triage(node)
    if hook is None:
        return None
    return c.execute(
        "SELECT id, head_sha, result_path, round FROM worker_sessions "
        "WHERE work_item_id = ? AND node_id = ? AND hook_point = ? AND head_sha IS NOT NULL "
        f"AND status IN ({marks(ADVANCING)}) AND created_at >= ? "
        "ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (work_item_id, node.id, hook.path, *ADVANCING, since),
    ).fetchone()


def _inert_reason(db, work_item_id: str, node) -> str | None:
    if not node.fix_loop:
        return "no_fix_loop"
    if _triage(node) is None:
        return "no_triage"
    multi = db.read(
        lambda c: c.execute(
            "SELECT 1 FROM work_item_repos WHERE work_item_id = ? LIMIT 1", (work_item_id,)
        ).fetchone()
    )
    return "multi_repo" if multi else None


async def _record_inert(db, work_item_id: str, node_id: str, reason: str, run_seq: int) -> None:
    """`replies_inert`, once per node run and reason. Read first, so a node that
    fails the shape rule opens no write at every wake; checked again in the write."""

    def seen(c) -> bool:
        return (
            c.execute(
                "SELECT 1 FROM events WHERE work_item_id = ? AND type = ? AND seq > ? "
                "AND json_extract(payload, '$.node_id') = ? "
                "AND json_extract(payload, '$.reason') = ?",
                (work_item_id, ForgeEvent.REPLIES_INERT, run_seq, node_id, reason),
            ).fetchone()
            is not None
        )

    def write(c) -> None:
        if not seen(c):
            events.append(
                c, work_item_id, ForgeEvent.REPLIES_INERT, {"node_id": node_id, "reason": reason}
            )

    if not db.read(seen):
        await db.write(write)


async def posting_point(
    db, run_dirs, work_item_id: str, node, row, worktree, launch, *, completing: bool = False
) -> None:
    """One posting point of `node`: after a measurement returns, or, `completing`,
    before the node is recorded complete. Never raises: posting never stops the item."""
    if worktree is None:
        return
    try:
        await _post(db, run_dirs, work_item_id, node, row, Path(worktree), launch, completing)
    except Exception:  # noqa: BLE001 -- a reply that could not go is listed, never a stop
        logger.exception("posting replies for %s at %s failed", work_item_id, node.id)


async def _post(db, run_dirs, wid, node, row, worktree, launch, completing) -> None:
    wait = _review_wait(node)
    if wait is None:
        return
    reviewers = dispatch._reviewers(wait.task, node.id, row, launch)
    bots = tuple(r.bot for r in reviewers if r.bot is not None)
    on = tuple(r.bot for r in reviewers if r.bot is not None and r.replies)
    if not on:
        return
    run_seq, run_at = db.read(lambda c: _run_start(c, wid, node.id))
    if (reason := _inert_reason(db, wid, node)) is not None:
        await _record_inert(db, wid, node.id, reason, run_seq)
        return
    point = _Point(db, run_dirs, wid, node, row, worktree, launch, bots, on, run_seq, run_at)
    if point.has_work(completing):
        await point.post(completing)


@dataclass
class _Point:
    """One posting point's state."""

    db: object
    run_dirs: object
    wid: str
    node: object
    row: object
    worktree: Path
    launch: object
    bots: tuple[str, ...]
    on: tuple[str, ...]
    run_seq: int
    run_at: str
    forge: object = None
    viewer: str | None = None
    pp: send.PostingPoint | None = None
    secrets: frozenset[str] = frozenset()
    _mr: object = None
    _threads: dict = field(default_factory=dict)
    _latest: object = None
    #: Report entries that are not intents (part 2b's summary and report).
    notes: list[dict] = field(default_factory=list)

    @property
    def branch(self) -> str:
        return store.branch_for(self.row)

    @property
    def entry(self):
        return self.launch.repo_entry if self.launch else None

    def forges(self):
        return self.launch.forges() if self.launch and self.launch.forges else (None, None)

    def intents(self) -> dict[str, record.Intent]:
        return self.db.read(lambda c: record.read(c, self.wid))

    def kraft(self, intents) -> facts.Kraft:
        return facts.Kraft(self.viewer, self.wid, record.posted_hashes(intents))

    def latest(self):
        if self._latest is None:
            self._latest = (
                self.db.read(lambda c: latest_triage(c, self.wid, self.node, self.run_at)) or False
            )
        return self._latest or None

    def _held(self, intents, key: dict) -> record.Intent | None:
        return next(
            (i for i in intents.values() if all(i.payload.get(k) == v for k, v in key.items())),
            None,
        )

    def _pending(self, op: str, kinds: Collection[str]) -> list[record.Intent]:
        """This node run's intents still to send: never one from an earlier run."""
        return [
            i
            for i in self.intents().values()
            if i.state == "pending"
            and i.payload.get("run") == self.run_seq
            and i.payload.get("node_id") == self.node.id
            and i.payload.get("op") == op
            and i.payload.get("kind") in kinds
        ]

    async def intend(self, key: dict, payload: dict, rendered_for) -> record.Intent:
        """The intent `key` names, or a new one: the hash recorded is of the body
        rendered for its id, which rendering again later reproduces."""
        held = self._held(self.intents(), key)
        if held is not None:
            return held
        iid = record.new_id(self.wid)
        full = {
            "node_id": self.node.id,
            "repo": str(Path(self.row["repo"])),
            "run": self.run_seq,
            **payload,
            "body_sha256": facts.body_hash(rendered_for(iid)),
        }
        await self.db.write(lambda c: record.intend(c, self.wid, iid, full))
        return self.intents()[iid]

    def triage_candidates(self) -> list[dict]:
        """The latest triage's replies, in threads of bots with replies on, that
        have no intent yet."""
        latest = self.latest()
        if latest is None:
            return []
        seen = prompts.collected_threads(self.run_dirs, self.wid, latest["id"])
        keys = {f"bot:{b}" for b in self.on}
        mine = {tid for tid, t in seen.items() if t.get("reviewer") in keys}
        held = self.intents()
        # At most one of a finding or a reply per thread: a thread a finding names is
        # the fix loop's, answered once it is fixed (part 2b), never declined as well.
        flagged = {t for f in _findings.parse(latest["result_path"]) for t in f.threads}
        return [
            r
            for r in triage_replies(latest["result_path"], mine - flagged)
            if self._held(held, {"op": "reply", "session": latest["id"], "thread": r["thread"]})
            is None
        ]

    def has_work(self, completing: bool) -> bool:
        """Whether this posting point needs the forge: from the database and files only."""
        held = self.intents()
        if any(i.state == "unclear" for i in held.values()):
            return True
        if any(
            i.state == "pending"
            and i.payload.get("run") == self.run_seq
            and i.payload.get("node_id") == self.node.id
            for i in held.values()
        ):
            return True
        if any(i.ever_unclear and i.state == "posted" for i in self.window(held)):
            return True  # a copy of an unclear write may still land: look for it
        return bool(
            self.triage_candidates()
            or self.fix_ready(completing)
            or (completing and self.summary_wanted())
        )

    def fix_ready(self, completing: bool) -> bool:
        return False  # part 2b: fix replies

    def summary_wanted(self) -> bool:
        return False  # part 2b: the summary comment

    def write_report(self, path: Path) -> None:
        pass  # part 2b: the reply report

    async def post(self, completing: bool) -> None:
        forges, forges_error = self.forges()
        try:
            self.forge = await forge_run.item_forge(
                self.db, self.row, self.entry, forges, forges_error
            )
        except ForgeError:
            await _record_inert(self.db, self.wid, self.node.id, "no_forge", self.run_seq)
            return
        try:
            self.viewer = await self.forge.viewer(repo=self.worktree)
        except ForgeError:
            return  # unreadable: nothing is posted; the next posting point reads it again
        if any(same_login(self.viewer, b) for b in self.bots):
            await _record_inert(self.db, self.wid, self.node.id, "viewer_is_a_bot", self.run_seq)
            return
        self.secrets = body.known_secrets(self.entry)
        self.pp = send.PostingPoint(self.db, self.wid, account=await self.account())
        await self.reconcile()
        await self.triage_replies()
        # Part 2b adds: await self.fix_replies(completing); at completion, self.summary().

    async def account(self) -> str:
        """Writes are spaced per forge account: an API account's name, else the
        origin's host (item 1's spacing key)."""
        forges, forges_error = self.forges()
        api = await forge_run.api_account_for(
            self.db, self.wid, "", self.entry, forges, forges_error
        )
        if api:
            return api[0]
        origin = await asyncio.to_thread(
            git_read, Path(self.row["repo"]), "remote", "get-url", "origin", expected_failure=True
        )
        return (forges_mod.remote_host(origin) if origin else None) or "forge"

    async def notes_of(self, intent) -> tuple:
        """Where an intent's copy would be: its thread's notes, or the merge request's
        comments for the summary."""
        if intent.payload["op"] == "summary":
            return await self.forge.mr_comments(repo=self.worktree, branch=self.branch)
        thread = await self.forge.read_thread(
            repo=self.worktree, branch=self.branch, thread_id=intent.payload["thread"]
        )
        return thread.notes if thread is not None else ()

    def window(self, held) -> list[record.Intent]:
        """This node's intents of its current run and the run before (the late-copy
        window item 1 left to the caller): the ones a duplicate sweep reads."""
        mine = [i for i in held.values() if i.payload.get("node_id") == self.node.id]
        earlier = sorted({i.payload.get("run", 0) for i in mine} - {self.run_seq})
        horizon = {self.run_seq, *[r for r in earlier if r < self.run_seq][-1:]}
        return [i for i in mine if i.payload.get("run") in horizon]

    async def reconcile(self) -> None:
        """Every unclear write of the item, whatever node run it was made in, before
        anything is sent; then duplicates, over `window`."""
        await self.pp.reconcile(self.intents().values(), viewer=self.viewer, notes=self.notes_of)
        window = self.window(self.intents())
        for in_thread in (True, False):

            async def delete(note, in_thread=in_thread):
                await self.forge.delete_note(
                    repo=self.worktree, branch=self.branch, note=note, in_thread=in_thread
                )

            await self.pp.sweep_duplicates(
                [i for i in window if (i.payload["op"] == "reply") == in_thread],
                viewer=self.viewer,
                notes=self.notes_of,
                delete=delete,
            )

    async def stale(self, session) -> tuple[str, str] | None:
        """Why `session`'s head is not current, or None: the triage's head, its
        collection's, the checkout's and the forge's are one commit."""
        if self._mr is None:
            self._mr = await self.forge.mr_details(repo=self.worktree, branch=self.branch)
        if self._mr is None or self._mr.state != "open":
            return ("refused", "mr_not_open")
        checkout = await asyncio.to_thread(
            git_read, self.worktree, "rev-parse", inplace.head_ref(self.row)
        )
        heads = {
            session["head_sha"],
            prompts.collection(self.run_dirs, self.wid, session["id"]).get("head"),
            checkout,
            self._mr.head_sha,
        }
        return None if len(heads) == 1 else ("superseded", "head_moved")

    async def thread(self, tid: str):
        """The thread as the forge has it now: every gate reads it afresh."""
        self._threads[tid] = await self.forge.read_thread(
            repo=self.worktree, branch=self.branch, thread_id=tid
        )
        return self._threads[tid]

    async def triage_replies(self) -> None:
        latest = self.latest()
        for r in self.triage_candidates():
            await self.intend(
                {"op": "reply", "session": latest["id"], "thread": r["thread"]},
                {
                    "op": "reply",
                    "thread": r["thread"],
                    "kind": r["kind"],
                    "head": latest["head_sha"],
                    "round": latest["round"],
                    "session": latest["id"],
                },
                lambda iid, r=r: body.render(
                    r["body"],
                    lead=_LEADS[r["kind"]],
                    work_item_id=self.wid,
                    marker=facts.reply_marker(iid),
                ),
            )
        for intent in self._pending("reply", gate.TRIAGE_KINDS):
            await self._send_triage(intent, latest)

    async def _send_triage(self, intent, latest) -> None:
        p = intent.payload
        rendered: dict[str, str] = {}

        async def check(_intent):
            if latest is None or p["session"] != latest["id"]:
                return ("superseded", "not_latest")
            if (why := await self.stale(latest)) is not None:
                return why
            thread = await self.thread(p["thread"])
            held = self.intents()
            kraft = self.kraft(held)
            why = gate.triage_refusal(thread, bots=self.bots, on=self.on, kraft=kraft, intents=held)
            if why is not None:
                return ("refused", why)
            seen = prompts.collected_threads(self.run_dirs, self.wid, latest["id"])
            # A bot or person note the triage never read: its judgement is out of date.
            known = {n.get("id") for n in (seen.get(p["thread"]) or {}).get("notes") or ()}
            if any(n.id not in known and not kraft.wrote(n) for n in thread.notes):
                return ("superseded", "thread_moved")
            # The text is read again from the session's own result file (never stored:
            # a refused text may hold what must not be kept); `latest` is that session.
            text = {
                r["thread"]: r["body"] for r in triage_replies(latest["result_path"], seen)
            }.get(p["thread"])
            if text is None:
                return ("refused", "no_text")
            refused = await body.check(
                text,
                quotes=body.RepoQuotes(
                    self.worktree, latest["head_sha"], [n.body for n in thread.notes]
                ),
                secrets=self.secrets,
            )
            if refused is not None:
                return ("refused", f"body: {refused.why}")
            rendered["body"] = body.render(
                text,
                lead=_LEADS[p["kind"]],
                work_item_id=self.wid,
                marker=facts.reply_marker(intent.id),
            )
            if facts.body_hash(rendered["body"]) != p["body_sha256"]:
                return ("superseded", "body_changed")  # the result file changed since
            return None

        async def write():
            return await self.forge.reply_to_thread(
                repo=self.worktree, thread=self._threads[p["thread"]], body=rendered["body"]
            )

        try:
            await self.pp.send(intent, gate=check, write=write)
        except ForgeError as exc:
            # A read in the gate failed (the thread, the merge request, `git grep`):
            # nothing was sent, so the next posting point tries again.
            logger.info("reply %s waits: %s", intent.id, exc)
