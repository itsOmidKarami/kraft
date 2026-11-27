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
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from kraft import events, inplace, item_paths, store, waits
from kraft import findings as _findings
from kraft import forges as forges_mod
from kraft.adapters.forge import run as forge_run
from kraft.adapters.forge.models import ForgeError, Posted
from kraft.adapters.forge.mr import same_login
from kraft.adapters.forge.replies import body, facts, gate, owed, record, send
from kraft.config import git_read
from kraft.executor import dispatch, reply_round
from kraft.executor import round_prompts as prompts
from kraft.executor.reply_round import ReplyRound, latest_triage
from kraft.executor.reply_round import own as _own
from kraft.executor.reply_round import run_start as _run_start
from kraft.executor.reply_round import triage_task as _triage
from kraft.policy import (
    DEFAULT_REPLY_CAP_PER_THREAD,
    DEFAULT_REPLY_ROUNDS_PER_HEAD,
    DEFAULT_REPLY_TIMEOUT_MINUTES,
)
from kraft.templates.models import AgentTask, ForgeAction, ForgeTask
from kraft.vocab import ADVANCING, ChainEvent, ForgeEvent
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


def repair_replies(result_path, threads: Collection[str]) -> dict[str, str]:
    """The repair's `replies`, thread to body, for the threads it was handed."""
    out: dict[str, str] = {}
    for r in _rows(_result(result_path), "replies"):
        tid = r.get("thread")
        if isinstance(tid, str) and tid in threads and tid not in out and _text(r):
            out[tid] = r["body"]
    return out


def summary_replies(result_path, reviewers: Collection[str]) -> list[dict]:
    """The triage's one-line declines of top-level comments, each naming a reviewer
    of the collection (`bot:LOGIN`)."""
    return [
        # One line each: a body with a line break would render as another bot's bullet.
        {"reviewer": r["reviewer"], "body": " ".join(r["body"].split())}
        for r in _rows(_result(result_path), "summary_replies")
        if r.get("reviewer") in reviewers and _text(r)
    ]


def report_path(run_dirs, work_item_id: str) -> Path:
    return item_paths.item_dir(run_dirs, work_item_id) / item_paths.REPLY_REPORT


def _drop_report(path: Path, node_id: str) -> None:
    """Remove the report when it is this node's: one item may have two review nodes."""
    try:
        if json.loads(path.read_text()).get("node_id") == node_id:
            path.unlink()
    except (OSError, ValueError, AttributeError):
        pass


def _review_wait(node):
    return next(
        (
            t
            for t in _own(node)
            if isinstance(t.task, ForgeTask) and t.task.target is ForgeAction.MR_AUTOMATED_REVIEW
        ),
        None,
    )


@dataclass(frozen=True)
class _Cycle:
    cycle: int
    seq: int  # its `fix_cycle_started`
    started_at: str
    base: str | None  # the head it started from
    threads: dict  # thread id -> fingerprint
    head: str | None = None  # its commit, once finished
    committed: bool = False


def _cycles(c, work_item_id: str, node_id: str, run_seq: int) -> list[_Cycle]:
    """The node's fix cycles in its current run, each with its finish if it has one."""
    out: list[_Cycle] = []
    rows = c.execute(
        "SELECT seq, type, payload, created_at FROM events WHERE work_item_id = ? AND seq > ? "
        "AND type IN (?, ?) ORDER BY seq",
        (work_item_id, run_seq, ChainEvent.FIX_CYCLE_STARTED, ChainEvent.FIX_CYCLE_FINISHED),
    )
    for r in rows:
        p = json.loads(r["payload"])
        if p.get("node_id") != node_id:
            continue
        if r["type"] == ChainEvent.FIX_CYCLE_STARTED:
            out.append(
                _Cycle(
                    p["cycle"],
                    r["seq"],
                    r["created_at"],
                    p.get("head_sha"),
                    p.get("threads") or {},
                )
            )
        elif out and out[-1].cycle == p.get("cycle") and out[-1].head is None:
            out[-1] = replace(out[-1], head=p.get("head_sha"), committed=bool(p.get("committed")))
    return out


def _repair_session(c, work_item_id: str, node, cycle: _Cycle):
    """The cycle's own fix session: the newest of the fix loop's agent task at the
    cycle's round since its start (a refunded cycle's number is reused)."""
    hook = next((t for s in node.fix_loop for t in s.tasks if isinstance(t.task, AgentTask)), None)
    if hook is None:
        return None
    return c.execute(
        "SELECT id, result_path FROM worker_sessions WHERE work_item_id = ? AND node_id = ? "
        f"AND hook_point = ? AND round = ? AND status IN ({marks(ADVANCING)}) "
        "AND created_at >= ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (work_item_id, node.id, hook.path, cycle.cycle, *ADVANCING, cycle.started_at),
    ).fetchone()


def _pushes_since(c, work_item_id: str, repo: str, seq: int) -> list[str]:
    """The heads Kraft pushed for `repo` after event `seq`, oldest first
    (`run._record_push` records one only when the head changed)."""
    rows = c.execute(
        "SELECT json_extract(payload, '$.sha') AS sha FROM events WHERE work_item_id = ? "
        "AND type = ? AND json_extract(payload, '$.repo') = ? AND seq > ? ORDER BY seq",
        (work_item_id, ForgeEvent.BRANCH_PUSHED, repo, seq),
    ).fetchall()
    return [r["sha"] for r in rows]


def fix_pending(db, run_dirs, work_item_id: str, node, head: str) -> dict[str, str]:
    """Each thread a fix cycle of this run took and committed on `head` whose
    "Fixed in" has not gone out or ended: the triage's copy marks it `fix_pending`,
    so a triage that finds the fix holds writes nothing for it."""
    run_seq, _ = db.read(lambda c: _run_start(c, work_item_id, node.id))
    held = db.read(lambda c: record.read(c, work_item_id))
    out: dict[str, str] = {}
    for cyc in db.read(lambda c: _cycles(c, work_item_id, node.id, run_seq)):
        if not (cyc.committed and cyc.head == head and cyc.threads):
            continue
        # Every thread the cycle took, whether or not the repair wrote it a reply: a
        # fix with no reply is still the fix loop's, and the report says so.
        for tid in cyc.threads:
            done = any(
                i.payload.get("kind") == "fixed"
                and i.payload.get("run") == run_seq
                and i.payload.get("cycle") == cyc.cycle
                and i.payload.get("thread") == tid
                and i.state != "pending"
                for i in held.values()
            )
            if not done:
                out[tid] = head[:7]
    return out


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
    db,
    run_dirs,
    work_item_id: str,
    node,
    row,
    worktree,
    launch,
    *,
    completing: bool = False,
    loop_key: str | None = None,
) -> ReplyRound | None:
    """One posting point of `node`: after a measurement returns, or, `completing`,
    before the node is recorded complete. At a completion it also decides the reply round
    and returns it (already started: its event is written), else None. Never raises:
    posting never stops the item, and a failure here completes the node."""
    if worktree is None:
        return None
    try:
        return await _post(
            db, run_dirs, work_item_id, node, row, Path(worktree), launch, completing, loop_key
        )
    except Exception:  # noqa: BLE001 -- a reply that could not go is listed, never a stop
        logger.exception("posting replies for %s at %s failed", work_item_id, node.id)
        return None


async def _post(
    db, run_dirs, wid, node, row, worktree, launch, completing, loop_key
) -> ReplyRound | None:
    wait = _review_wait(node)
    if wait is None:
        return None
    reviewers = dispatch._reviewers(wait.task, node.id, row, launch)
    bots = tuple(r.bot for r in reviewers if r.bot is not None)
    on = tuple(r.bot for r in reviewers if r.bot is not None and r.replies)
    resolves = tuple(
        r.bot for r in reviewers if r.bot is not None and r.replies and r.resolve_fixed
    )
    report = report_path(run_dirs, wid)
    if not on:
        if completing:
            _drop_report(report, node.id)  # replies off now: this node's old report misleads
        return None
    run_seq, run_at = db.read(lambda c: _run_start(c, wid, node.id))
    if (reason := _inert_reason(db, wid, node)) is not None:
        await _record_inert(db, wid, node.id, reason, run_seq)
        if completing:
            _drop_report(report, node.id)
        return None
    point = _Point(
        db,
        run_dirs,
        wid,
        node,
        row,
        worktree,
        launch,
        bots,
        on,
        run_seq,
        run_at,
        loop_key=loop_key,
        resolves=resolves,
    )
    started = None
    try:
        if await point.has_work(False):
            await point.post(False)  # what is ready now: a waiting fix reply stays waiting
        if completing:
            try:
                started = await point.round()
            except Exception:  # noqa: BLE001 -- an error here completes the node: no round
                logger.exception("deciding a reply round for %s at %s failed", wid, node.id)
            if started is None and await point.has_work(True):
                await point.post(True)  # no round: end what waits, the summary
    finally:
        if completing and started is None:
            await point.write_report(report)
    return started


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
    #: The loop counter's key, for the round the posting point may start.
    loop_key: str | None = None
    #: The bots whose fixed threads Kraft resolves at a completion (`resolve_fixed`).
    resolves: tuple[str, ...] = ()
    forge: object = None
    viewer: str | None = None
    pp: send.PostingPoint | None = None
    secrets: frozenset[str] = frozenset()
    _mr: object = None
    _threads: dict = field(default_factory=dict)
    _latest: object = None
    _ready: bool | None = None
    _sent: bool = False
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

    @property
    def cap(self) -> int:
        """Kraft's triage replies per thread: the node's `reply_cap_per_thread`."""
        try:
            return dispatch.scope_policy(self.row, self.node).reply_cap_per_thread
        except LookupError:
            return DEFAULT_REPLY_CAP_PER_THREAD

    @property
    def rounds(self) -> int:
        """Reply rounds per head: the node's `reply_rounds_per_head`."""
        try:
            return dispatch.scope_policy(self.row, self.node).reply_rounds_per_head
        except LookupError:
            return DEFAULT_REPLY_ROUNDS_PER_HEAD

    @property
    def timeout(self) -> timedelta:
        """How long a thread waits for an answer: the *review task's* `reply_timeout_minutes`,
        the value the reply wait uses (the node's own could disagree and spend a round)."""
        try:
            minutes = dispatch.scope_policy(self.row, _review_wait(self.node)).reply_timeout_minutes
        except LookupError:
            minutes = DEFAULT_REPLY_TIMEOUT_MINUTES
        return timedelta(minutes=minutes)

    def intents(self) -> dict[str, record.Intent]:
        return self.db.read(lambda c: record.read(c, self.wid))

    def kraft(self, intents) -> facts.Kraft:
        return facts.Kraft(
            self.viewer, self.wid, record.posted_hashes(intents), record.thread_ids(intents)
        )

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

    async def has_work(self, completing: bool) -> bool:
        """Whether this posting point needs the forge: from the database and files only."""
        held = self.intents()
        if any(i.state == "unclear" for i in held.values()):
            return True
        if any(
            i.state == "pending"
            and i.payload.get("run") == self.run_seq
            and i.payload.get("node_id") == self.node.id
            and (completing or i.payload.get("op") != "resolve")  # a resolve goes only at the end
            for i in held.values()
        ):
            return True
        if any(
            i.ever_unclear and i.state == "posted" and i.payload.get("op") != "resolve"
            for i in self.window(held)
        ):
            return True  # a copy of an unclear write may still land: look for it
        return bool(
            self.triage_candidates()
            or await self.fix_ready(completing)
            or (completing and self.summary_wanted())
            or (completing and bool(self.resolves) and bool(self.talked()))
        )

    async def fix_ready(self, completing: bool) -> bool:
        held = self.intents()
        return any(
            verdict != "wait"
            and text is not None
            and self._held(held, self._fix_key(cyc, tid)) is None
            for cyc, tid, _, text, verdict in await self.fix_candidates(completing)
        )

    def _summary_keys(self, latest) -> set[str]:
        """The reviewers of the latest triage's collection that take replies."""
        coll = prompts.collection(self.run_dirs, self.wid, latest["id"])
        return {r.get("reviewer") for r in coll.get("reviewers") or () if r.get("replies")}

    def summary_wanted(self) -> bool:
        latest = self.latest()
        keys = self._summary_keys(latest) if latest else set()
        lines = summary_replies(latest["result_path"], keys) if latest else []
        return bool(lines) or any(i.payload.get("op") == "summary" for i in self.intents().values())

    async def write_report(self, path: Path) -> None:
        """What this node run posted and did not, for the brief: every reply intent
        of the run by state, every summary entry the walk noted, and every finding
        from bot threads that never reached a fix cycle."""
        latest = self.latest()
        entries = []
        for i in self.intents().values():
            p = i.payload
            mine = p.get("node_id") == self.node.id and p.get("run") == self.run_seq
            if p.get("op") != "reply" or not mine:
                continue
            ended = i.state in ("posted", "refused", "superseded", "given_up")
            entries.append(
                {
                    **self.where(p["thread"]),
                    "kind": p.get("kind"),
                    "outcome": i.state if ended else "unsent",
                    "why": i.why if ended else (i.failure or {}).get("failure"),
                }
            )
        entries += self._resolve_entries()
        for _, tid, _, text, verdict in await self.fix_candidates(True):
            if text is None and verdict == "send":
                entries.append(
                    {
                        **self.where(tid),
                        "kind": "fixed",
                        "outcome": "no_reply",
                        "why": "confirmed; the repair wrote no reply",
                    }
                )
        entries += self.notes
        entries += self._never_fixed()
        entries += self._ci_pending()
        path.parent.mkdir(parents=True, exist_ok=True)
        report = {
            "node_id": self.node.id,
            "head": latest["head_sha"] if latest else None,
            "entries": entries,
        }
        path.write_text(json.dumps(report, indent=2) + "\n")

    def _never_fixed(self) -> list[dict]:
        """Threads the run's last measurement still raises as findings with no
        "Fixed in" posted: taken by a cycle and raised again, declined by the judge,
        or never handed to a cycle (below the loop's severities)."""

        def read(c):
            rows = c.execute(
                "SELECT type, payload FROM events WHERE work_item_id = ? AND seq > ? "
                "AND type IN (?, ?) ORDER BY seq",
                (self.wid, self.run_seq, ChainEvent.FINDINGS_MEASURED, ChainEvent.JUDGE_VERDICT),
            ).fetchall()
            return [(r["type"], json.loads(r["payload"])) for r in rows]

        rows = [(t, p) for t, p in self.db.read(read) if p.get("node_id") == self.node.id]
        measured = [p for t, p in rows if t == ChainEvent.FINDINGS_MEASURED]
        if not measured:
            return []
        declined = {
            _findings.from_payload(f).fingerprint
            for t, p in rows
            if t == ChainEvent.JUDGE_VERDICT and p.get("verdict") == "stop_downgrade"
            for f in p.get("findings") or ()
        }
        cycles = self.db.read(lambda c: _cycles(c, self.wid, self.node.id, self.run_seq))
        taken = {tid: cyc.cycle for cyc in cycles for tid in cyc.threads}
        fixed = {
            i.payload.get("thread")
            for i in self.intents().values()
            if i.payload.get("kind") == "fixed"
            and i.state == "posted"
            and i.payload.get("run") == self.run_seq
        }
        out = []
        # What the last measurement did not re-read (`unmeasured`) is still raised: a round whose
        # triage was skipped must not lose what an earlier measurement found.
        raised = [*(measured[-1].get("findings") or ()), *(measured[-1].get("unmeasured") or ())]
        for f in map(_findings.from_payload, raised):
            for tid in f.threads:
                if tid in fixed:
                    continue
                if f.fingerprint in declined:
                    why = "the judge declined it"
                elif tid in taken:
                    why = f"still raised after cycle {taken[tid]}"
                else:
                    why = "never reached a fix"
                out.append(
                    {**self.where(tid), "kind": "finding", "outcome": "not_fixed", "why": why}
                )
        return out

    async def ready(self) -> bool:
        """The forge, the viewer and the posting point, resolved once per posting point;
        False when the forge cannot be resolved, the viewer is unreadable or it is a bot."""
        if self._ready is None:
            self._ready = await self._resolve()
        return self._ready

    async def _resolve(self) -> bool:
        forges, forges_error = self.forges()
        try:
            self.forge = await forge_run.item_forge(
                self.db, self.row, self.entry, forges, forges_error
            )
        except ForgeError:
            await _record_inert(self.db, self.wid, self.node.id, "no_forge", self.run_seq)
            return False
        try:
            self.viewer = await self.forge.viewer(repo=self.worktree)
        except ForgeError:
            return False  # unreadable: nothing is posted; the next posting point reads it again
        if any(same_login(self.viewer, b) for b in self.bots):
            await _record_inert(self.db, self.wid, self.node.id, "viewer_is_a_bot", self.run_seq)
            return False
        self.secrets = body.known_secrets(self.entry)
        self.pp = send.PostingPoint(self.db, self.wid, account=await self.account())
        return True

    async def post(self, completing: bool) -> None:
        if not await self.ready():
            return
        if not self._sent:  # a completion's second pass has already reconciled and replied
            self._sent = True
            await self.reconcile()
            await self.triage_replies()
        await self.fix_replies(completing)
        if completing:
            try:
                await self.summary()
            except ForgeError as exc:  # the comment waits; the report says nothing posted
                logger.info("summary comment waits: %s", exc)
            try:
                await self.resolve()
            except ForgeError as exc:  # a read failed: what was not resolved is listed open
                logger.info("resolving waits: %s", exc)

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
                [
                    i
                    for i in window
                    if i.payload["op"] in ("reply", "summary")
                    and (i.payload["op"] == "reply") == in_thread
                ],
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

    async def summary(self) -> None:
        """At completion, from the latest triage on the current head: one comment
        for the top-level declines of bots with replies on, created or edited in
        place, found only by its marker on the viewer's comment."""
        latest = self.latest()
        if latest is None or await self.stale(latest) is not None:
            return  # no triage judged this head: the comment stays as it is
        coll = prompts.collection(self.run_dirs, self.wid, latest["id"])
        keys = self._summary_keys(latest)
        notes = [
            s.get("body") or ""
            for r in coll.get("reviewers") or ()
            if r.get("reviewer") in keys
            for s in r.get("summaries") or ()
        ]
        kept = []
        for line in summary_replies(latest["result_path"], keys):
            refused = await body.check(
                line["body"],
                quotes=body.RepoQuotes(self.worktree, latest["head_sha"], notes),
                secrets=self.secrets,
            )
            if refused is None:
                kept.append(line)
            else:
                self.notes.append(
                    {
                        **self.where(None),
                        "reviewer": line["reviewer"],
                        "kind": "summary",
                        "outcome": "refused",
                        "why": f"body: {refused.why}",
                    }
                )
        comments = await self.forge.mr_comments(repo=self.worktree, branch=self.branch)
        mine = [
            n
            for n in comments
            if same_login(n.author, self.viewer) and facts.has_summary_marker(n.body, self.wid)
        ]
        held = self.intents()
        posted_before = any(
            i.payload.get("op") == "summary" and i.state == "posted" for i in held.values()
        )
        if not mine and posted_before:
            self.notes.append(
                {
                    **self.where(None),
                    "kind": "summary",
                    "outcome": "deleted",
                    "why": "someone deleted it; not posted again",
                }
            )
            return
        if not kept and not mine:
            return
        head = latest["head_sha"]
        text = "\n".join(
            f"- {line['reviewer'].removeprefix('bot:')}: {line['body']}" for line in kept
        )
        rendered = body.render(
            text or "Nothing is declined.",
            lead=f"Declined review-bot comments on {head[:7]}:",
            work_item_id=self.wid,
            marker=facts.summary_marker(self.wid),
        )
        if mine and facts.body_hash(mine[0].body) == facts.body_hash(rendered):
            return
        intent = await self.intend(
            {
                "op": "summary",
                "run": self.run_seq,
                "head": head,
                "body_sha256": facts.body_hash(rendered),
            },
            {
                "op": "summary",
                "thread": None,
                "kind": "summary",
                "head": head,
                "round": latest["round"],
                "session": latest["id"],
            },
            lambda _iid: rendered,
        )

        async def check(_intent):
            if self._mr is None or self._mr.state != "open":
                return ("refused", "mr_not_open")
            return None

        async def write():
            return await self.forge.upsert_comment(
                repo=self.worktree,
                branch=self.branch,
                note_id=mine[0].rest_id if mine else None,
                body=rendered,
            )

        await self.pp.send(intent, gate=check, write=write)

    def seen(self) -> gate.Latest | None:
        """What the latest triage read and wrote: when it read the bots and the threads it
        wrote a reply for; None when there is no triage, or its collection has no read time."""
        latest = self.latest()
        if latest is None:
            return None
        coll = prompts.collection(self.run_dirs, self.wid, latest["id"])
        read_at = reply_round.when(coll.get("read_at"))
        if read_at is None:
            return None
        threads = prompts.collected_threads(self.run_dirs, self.wid, latest["id"])
        replied = frozenset(r["thread"] for r in triage_replies(latest["result_path"], threads))
        return gate.Latest(latest["id"], read_at, replied)

    def outstanding(self) -> tuple[set[str], bool]:
        """The threads a finding of this run still names, and whether a record carries
        `dropped`: the last measurement's findings and unmeasured findings (what is below
        the loop's severities), what the judge declined, and what the latest triage's own
        result file raises: a record keeps a finding's thread only on that thread's file
        (`findings.on_their_files`), and the file still names it."""
        rows = self.db.read(
            lambda c: [
                (e["type"], e["payload"])
                for e in events.read_after(c, self.run_seq, self.wid)
                if e["payload"].get("node_id") == self.node.id
                and e["type"] in (ChainEvent.FINDINGS_MEASURED, ChainEvent.JUDGE_VERDICT)
            ]
        )
        records = [p for t, p in rows if t == ChainEvent.FINDINGS_MEASURED]
        last = records[-1] if records else {}
        raw = [*(last.get("findings") or ()), *(last.get("unmeasured") or ())]
        raw += [
            f
            for t, p in rows
            if t == ChainEvent.JUDGE_VERDICT and p.get("verdict") == "stop_downgrade"
            for f in p.get("findings") or ()
        ]
        out = {tid for f in raw for tid in _findings.from_payload(f).threads}
        latest = self.latest()
        if latest is not None:
            out |= {tid for f in _findings.parse(latest["result_path"]) for tid in f.threads}
        return out, any(p.get("dropped") for p in records)

    def _left_open(self, tid: str, why: str) -> None:
        self.notes.append(
            {**self.where(tid), "kind": "resolve", "outcome": "left_open", "why": why}
        )

    async def resolve(self) -> None:
        """At a completion with no round: resolve each thread whose fix Kraft confirmed, for a
        bot with `resolve_fixed`. Read fresh; a pending resolve of the run goes first; every
        refusal is listed in the report with its reason."""
        if not self.resolves or not await self.ready():
            return
        self._mr = None  # the merge request is read afresh, as each thread is
        latest = self.latest()
        seen = self.seen() if latest is not None else None
        outstanding, dropped = self.outstanding()
        for intent in self._pending("resolve", ("resolve",)):
            await self._send_resolve(
                intent, intent.payload["thread"], seen, outstanding, dropped, latest
            )
        for tid in self.talked():
            held = self.intents()
            if self._held(held, {"op": "resolve", "run": self.run_seq, "thread": tid}) is not None:
                continue  # already an intent for this run (sent above, or ended)
            if not self.pp.in_budget():
                self._left_open(tid, "not_read")
                continue
            try:
                thread = await self.thread(tid)
            except ForgeError as exc:
                self._left_open(tid, "not_read")
                logger.info("resolve %s waits: %s", tid, exc)
                continue
            if thread is not None and thread.resolved:
                continue  # the bot resolved it: left alone
            first = thread.notes[0] if thread is not None and thread.notes else None
            if thread is None or first is None:
                self._left_open(tid, "not_found")
                continue
            if not any(same_login(first.author, b) for b in self.resolves):
                continue  # a thread of a bot whose resolving is not asked for
            try:
                stale = await self.stale(latest) if latest is not None else None
            except ForgeError:
                self._left_open(tid, "not_read")
                continue
            if latest is None:
                why = "no_latest_triage"
            elif stale is not None:
                why = "mr_not_open" if stale[1] == "mr_not_open" else "no_latest_triage"
            else:
                why = gate.resolve_refusal(
                    thread,
                    bots=self.bots,
                    on=self.on,
                    kraft=self.kraft(held),
                    intents=held,
                    run=self.run_seq,
                    node_id=self.node.id,
                    latest=seen,
                    outstanding=outstanding,
                    dropped=dropped,
                )
            if why is not None:
                self._left_open(tid, why)
                continue
            intent = await self.intend(
                {"op": "resolve", "run": self.run_seq, "thread": tid},
                {
                    "op": "resolve",
                    "thread": tid,
                    "kind": "resolve",
                    "head": latest["head_sha"],
                    "round": latest["round"],
                    "session": latest["id"],
                },
                lambda _iid: "",
            )
            await self._send_resolve(intent, tid, seen, outstanding, dropped, latest)

    async def _send_resolve(self, intent, tid, seen, outstanding, dropped, latest) -> None:
        async def check(_intent):
            self._mr = None  # a push since the candidate read must be seen
            if latest is None:
                return ("refused", "no_latest_triage")
            if (why := await self.stale(latest)) is not None:
                return why
            thread = await self.thread(tid)
            held = self.intents()
            why = gate.resolve_refusal(
                thread,
                bots=self.bots,
                on=self.on,
                kraft=self.kraft(held),
                intents=held,
                run=self.run_seq,
                node_id=self.node.id,
                latest=seen,
                outstanding=outstanding,
                dropped=dropped,
            )
            return ("refused", why) if why is not None else None

        async def write():
            await self.forge.resolve_thread(repo=self.worktree, thread=self._threads[tid])
            return Posted((), datetime.now(UTC).isoformat(), "")

        try:
            await self.pp.send(intent, gate=check, write=write)
        except ForgeError as exc:
            # A read in the gate failed: nothing was sent; the next posting point tries again.
            logger.info("resolve %s waits: %s", intent.id, exc)

    def _resolve_entries(self) -> list[dict]:
        """This run's resolve intents as report entries: resolved, or left open and why."""
        out = []
        for i in self.intents().values():
            p = i.payload
            if p.get("op") != "resolve" or p.get("node_id") != self.node.id:
                continue
            if p.get("run") != self.run_seq:
                continue
            found = (
                i.state == "refused" and i.why == "resolved"
            )  # it was resolved by the time of the gate
            done = i.state == "posted" or found
            out.append(
                {
                    **self.where(p["thread"]),
                    "kind": "resolve",
                    "outcome": "resolved" if done else "left_open",
                    "why": None
                    if i.state == "posted"
                    else "found resolved"
                    if found
                    else i.why or (i.failure or {}).get("failure") or "unsent",
                }
            )
        return out

    def talked(self) -> list[str]:
        """The threads the record holds a posted reply of this node in, any run: the candidates."""
        return sorted(
            {
                i.payload["thread"]
                for i in self.intents().values()
                if i.state == "posted"
                and i.payload.get("op") == "reply"
                and i.payload.get("node_id") == self.node.id
                and i.payload.get("thread")
            }
        )

    async def round(self) -> ReplyRound | None:
        """At a completion: the reply round to run instead, or None. Read fresh, never from
        what the last pass posted. Nothing is read without a posted reply of this node."""
        candidates = self.talked()
        if not candidates:
            return None  # nothing was ever posted: no forge call
        head = await asyncio.to_thread(reply_round.item_head, self.row, self.worktree)
        if head is None:
            return None
        if not await self.ready():
            self.not_read(candidates, "the forge or the viewer could not be read")
            return None
        try:
            mr = await self.forge.mr_details(repo=self.worktree, branch=self.branch)
        except ForgeError:
            self.not_read(candidates, "the merge request could not be read")
            return None
        if mr is None or mr.state != "open":
            return None  # nothing to read, nothing to answer
        owed_now = await self.owed(candidates)  # [(thread id, the ForgeThread just read)]
        if not owed_now:
            return None
        count = self.db.read(lambda c: reply_round.started_count(c, self.wid, self.node.id, head))
        if count >= self.rounds:
            self.notes += [
                self.thread_entry(
                    t,
                    "owed",
                    "owed_at_limit",
                    f"reply round limit {self.rounds} reached on {head[:7]}",
                )
                for _, t in owed_now
            ]
            return None
        counter = self.db.read(lambda c: store.read_counter(c, self.wid, self.loop_key or ""))
        started = ReplyRound(
            0,
            head,
            counter["count"] if counter else 0,
            tuple(tid for tid, _ in owed_now),
            waits.now() + self.timeout,
        )
        await reply_round.begin(self.db, self.wid, started, self.node.id)
        return started

    def not_read(self, tids, why: str) -> None:
        """Threads the owed check could not read: listed, never a reason to stop or to wait."""
        self.notes += [
            {**self.where(tid), "kind": "owed", "outcome": "not_read", "why": why} for tid in tids
        ]

    async def owed(self, candidates: list[str]) -> list[tuple[str, object]]:
        """The candidates owed a look, each read once with the forge (`read_thread`) inside the
        posting point's budget. One that cannot be read (a failed read, a spent budget) is not
        owed, and is listed as `not_read`."""
        latest = self.latest()
        coll = prompts.collection(self.run_dirs, self.wid, latest["id"]) if latest else {}
        read_at = reply_round.when(coll.get("read_at"))
        out, unread = [], []
        for tid in candidates:
            if not self.pp.in_budget():
                unread.append(tid)
                continue
            try:
                thread = await self.forge.read_thread(
                    repo=self.worktree, branch=self.branch, thread_id=tid
                )
            except ForgeError:
                unread.append(tid)
                continue
            if thread is None:
                continue
            stand = owed.standing(
                thread.notes,
                on=self.on,
                bots=self.bots,
                kraft=self.kraft(self.intents()),
                timeout=self.timeout,
                resolved=thread.resolved,
            )
            if stand is not None and owed.is_owed(stand, read_at=read_at, now=waits.now()):
                out.append((tid, thread))
        if unread:
            self.not_read(unread, "the thread could not be read in time")
        return out

    def thread_entry(self, thread, kind: str, outcome: str, why: str) -> dict:
        """A report entry from the thread as just read: its last word is the bot's newest note,
        the unread answer that made the thread owed (the latest triage's copy cannot hold it)."""
        first = thread.notes[0]
        last = next((n for n in reversed(thread.notes) if same_login(n.author, first.author)), None)
        return {
            "thread": thread.id,
            "reviewer": self.where(thread.id)["reviewer"],
            "file": thread.file,
            "line": thread.line,
            "bot_last_word": last.body[:300] if last else None,
            "kind": kind,
            "outcome": outcome,
            "why": why,
        }

    def _ci_pending(self) -> list[dict]:
        """A CI look that was still pending when a reply round's time ran out: its `mr.ci` wait
        ended `timed_out` (a settle) after the node's latest round began."""
        ci = {
            t.path
            for t in _own(self.node)
            if isinstance(t.task, ForgeTask) and t.task.target is ForgeAction.MR_CI
        }
        rows = self.db.read(lambda c: list(events.read_after(c, self.run_seq, self.wid)))
        began = max(
            (
                e["seq"]
                for e in rows
                if e["type"] == ChainEvent.REPLY_ROUND_STARTED
                and e["payload"].get("node_id") == self.node.id
            ),
            default=None,
        )
        if began is None or not any(
            e["seq"] > began
            and e["type"] == ForgeEvent.EXTERNAL_WAIT_ENDED
            and e["payload"].get("outcome") == "timed_out"
            and e["payload"].get("task") in ci
            for e in rows
        ):
            return []
        return [
            {
                "thread": None,
                "reviewer": None,
                "file": None,
                "line": None,
                "bot_last_word": None,
                "kind": "ci",
                "outcome": "ci_pending",
                "why": "CI was still pending when a reply round's time ran out; the node "
                "completed on the earlier green measurement of the same head",
            }
        ]

    def where(self, tid: str | None) -> dict:
        """A report entry's thread fields, from the latest triage's collection."""
        latest = self.latest()
        seen = prompts.collected_threads(self.run_dirs, self.wid, latest["id"]) if latest else {}
        t = seen.get(tid) or {}
        last = next(
            (n.get("body") for n in reversed(t.get("notes") or []) if n.get("by") == "bot"), None
        )
        return {
            "thread": tid,
            "reviewer": t.get("reviewer"),
            "file": t.get("file"),
            "line": t.get("line"),
            "bot_last_word": last[:300] if last else None,
        }

    async def fix_candidates(self, completing: bool):
        """(cycle, thread, session, the repair's text, verdict) for every fix reply of
        this run: verdict "send", "wait" (the triage on the commit has not run), or
        ("superseded", why)."""
        cycles = self.db.read(lambda c: _cycles(c, self.wid, self.node.id, self.run_seq))
        out = []
        for cyc in cycles:
            if not (cyc.committed and cyc.threads and cyc.head):
                continue
            sess = self.db.read(lambda c, cyc=cyc: _repair_session(c, self.wid, self.node, cyc))
            if sess is None:
                continue
            texts = repair_replies(sess["result_path"], cyc.threads)
            for tid in cyc.threads:  # a thread the repair wrote no reply for: text None
                verdict = await self._fix_verdict(cyc, tid, cycles, completing)
                out.append((cyc, tid, sess["id"], texts.get(tid), verdict))
        return out

    async def _fix_verdict(self, cyc, tid, cycles, completing):
        """Whether the triage on the cycle's commit confirmed the fix of `tid`."""
        if any(o.seq > cyc.seq for o in cycles):
            return ("superseded", "later_cycle")
        repo = str(Path(self.row["repo"]))
        pushed = self.db.read(lambda c: _pushes_since(c, self.wid, repo, cyc.seq))
        if pushed and pushed[-1] != cyc.head:
            return ("superseded", "head_moved")  # another commit went out after the fix
        if not pushed:
            # Not pushed yet (the next measurement's first step pushes it).
            return ("superseded", "not_pushed") if completing else "wait"
        latest = self.latest()
        if latest is None or latest["head_sha"] != cyc.head:
            # The commit exists only since the cycle: a triage on it ran after the fix.
            return ("superseded", "not_confirmed") if completing else "wait"
        seen = prompts.collected_threads(self.run_dirs, self.wid, latest["id"])
        if tid not in seen:
            return ("superseded", "not_collected")
        fp = cyc.threads[tid]
        found = _findings.parse(latest["result_path"])
        if any(tid in f.threads or _findings.prose_fingerprint(f) == fp for f in found):
            return ("superseded", "raised_again")
        if any(r["thread"] == tid for r in triage_replies(latest["result_path"], seen)):
            return ("superseded", "triage_replied")  # one reply per thread per head
        file = seen[tid].get("file")
        # Off the event loop: a slow git must not stall every other work item.
        changed = file and await asyncio.to_thread(
            git_read,
            self.worktree,
            "diff",
            "--name-only",
            cyc.base or cyc.head,
            cyc.head,
            "--",
            file,
        )
        if file and not changed:
            return ("superseded", "file_unchanged")
        return "send"

    def _fix_key(self, cyc, tid) -> dict:
        return {
            "op": "reply",
            "kind": "fixed",
            "run": self.run_seq,
            "cycle": cyc.cycle,
            "thread": tid,
        }

    async def fix_replies(self, completing: bool) -> None:
        for cyc, tid, session, text, verdict in await self.fix_candidates(completing):
            if verdict == "wait":
                continue
            if text is None:
                continue  # fixed with no reply: nothing to post; the report names it
            lead = f"Fixed in {cyc.head[:7]}:"
            intent = await self.intend(
                self._fix_key(cyc, tid),
                {
                    "op": "reply",
                    "kind": "fixed",
                    "thread": tid,
                    "cycle": cyc.cycle,
                    "finding": cyc.threads[tid],
                    "head": cyc.head,
                    "round": cyc.cycle,
                    "session": session,
                },
                lambda iid, text=text, lead=lead: body.render(
                    text, lead=lead, work_item_id=self.wid, marker=facts.reply_marker(iid)
                ),
            )
            try:
                await self._send_fix(intent, cyc, text, lead, verdict)
            except ForgeError as exc:
                # A read in the gate failed: nothing was sent; it waits for the next
                # posting point, or is listed `unsent` at completion.
                logger.info("fix reply %s waits: %s", intent.id, exc)

    async def _send_fix(self, intent, cyc, text: str, lead: str, verdict) -> None:
        rendered: dict[str, str] = {}

        async def check(_intent):
            if verdict != "send":
                return verdict  # ("superseded", why): recorded, listed in the report
            if self._mr is None:
                self._mr = await self.forge.mr_details(repo=self.worktree, branch=self.branch)
            if self._mr is None or self._mr.state != "open":
                return ("refused", "mr_not_open")
            if self._mr.head_sha != cyc.head:
                return ("superseded", "head_moved")  # a person's force-push dropped the fix
            thread = await self.thread(intent.payload["thread"])
            held = self.intents()
            why = gate.fix_refusal(
                thread,
                bots=self.bots,
                on=self.on,
                kraft=self.kraft(held),
                intents=held,
                finding=intent.payload["finding"],
            )
            if why is not None:
                return ("refused", why)
            refused = await body.check(
                text,
                quotes=body.RepoQuotes(self.worktree, cyc.head, [n.body for n in thread.notes]),
                secrets=self.secrets,
            )
            if refused is not None:
                return ("refused", f"body: {refused.why}")
            rendered["body"] = body.render(
                text, lead=lead, work_item_id=self.wid, marker=facts.reply_marker(intent.id)
            )
            if facts.body_hash(rendered["body"]) != intent.payload["body_sha256"]:
                return ("superseded", "body_changed")
            return None

        async def write():
            return await self.forge.reply_to_thread(
                repo=self.worktree,
                thread=self._threads[intent.payload["thread"]],
                body=rendered["body"],
            )

        await self.pp.send(intent, gate=check, write=write)

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
            why = gate.triage_refusal(
                thread, bots=self.bots, on=self.on, kraft=kraft, intents=held, cap=self.cap
            )
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
