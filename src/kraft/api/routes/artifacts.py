from __future__ import annotations

import logging

from fastapi import HTTPException, Request

from kraft import events, review, store
from kraft.api import api_router, deps
from kraft.api.routes import board
from kraft.executor import stops
from kraft.index import ingest as ingest_mod
from kraft.templates import revision
from kraft.worker.worktree_read import read_worktree_file

logger = logging.getLogger(__name__)

#: Diff bodies larger than this are cut at a file boundary. Protects the
#: browser; not a user decision, so not policy.
DIFF_MAX_BYTES = 1_000_000


def _truncate_at_file_boundary(diff: str, limit: int) -> tuple[str, bool]:
    """Cut a unified diff to `limit` bytes on a `diff --git` boundary.

    A single file bigger than `limit` has no boundary to cut at — the whole
    first chunk is kept unconditionally so at least one file always survives
    truncation. That file is then cut at the last newline that fits, so a
    committed lockfile or vendored blob doesn't come back whole.
    """
    if len(diff.encode()) <= limit:
        return diff, False
    kept: list[str] = []
    size = 0
    for chunk in diff.split("\ndiff --git ")[:1] + [
        "\ndiff --git " + c for c in diff.split("\ndiff --git ")[1:]
    ]:
        if size + len(chunk.encode()) > limit and kept:
            break
        kept.append(chunk)
        size += len(chunk.encode())
    result = "".join(kept)
    encoded = result.encode()
    if len(kept) == 1 and len(encoded) > limit:
        cut = encoded[:limit].rfind(b"\n")
        encoded = encoded[: cut if cut != -1 else limit]
        result = encoded.decode(errors="ignore")
    return result, True


@api_router.get("/work-items/{wid}/diff")
async def get_work_item_diff(wid: str, request: Request):
    """The changes an agent made, for a reviewer with no filesystem access.

    Two ranges, kept apart (Kraft-nceo). `landed` is `base_ref..HEAD` -- what
    earlier nodes committed, the chain's own spec and plan documents among it.
    The top level is `HEAD`..working tree, the change actually under review:
    one combined range spent the viewer's open-line budget on paperwork before
    the code was reached.
    """
    st = request.app.state
    row = deps._work_item_row(st, wid)  # 404s on an unknown work item
    base = row["base_ref"]
    if not base:
        # Pre-migration items (and any future template with no env_setup node)
        # never got a base_ref stamped, and are also the likeliest to have had
        # their worktree cleaned up since — check this before the worktree, so
        # that combination degrades to "no diff" rather than a 404.
        return {
            "work_item_id": wid,
            "base_ref": None,
            "files": [],
            "diff": "",
            "untracked": [],
            "truncated": False,
            "landed": {"commits": [], "files": [], "diff": "", "truncated": False},
            "diff_max_bytes": DIFF_MAX_BYTES,
            "worktree_path": str(st.run_dirs.worktrees / wid),
        }
    worktree = st.run_dirs.worktrees / wid
    if not worktree.is_dir():
        raise HTTPException(404, "this work item has no worktree yet")

    try:
        # No host git in a sandboxed worktree while its worker can still
        # write it (Kraft-69rwp): a 409 a reviewer can act on.
        stops.refuse_live_sandboxed_session(
            st.db, row, deps.launch(st, row["repo"]), what="the diff"
        )
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    change = review.read_change(worktree, "HEAD")
    landed = review.read_change(worktree, base, head="HEAD")
    if change is None or landed is None:
        # None means git itself failed (and git_read has already logged the
        # command and stderr). Returning an empty diff here would be
        # indistinguishable from "no changes" to the human approving the gate,
        # and the worktree path is a server filesystem detail a remote
        # reviewer's browser has no business seeing.
        raise HTTPException(500, "git could not read this work item's worktree")

    diff, truncated = _truncate_at_file_boundary(change.diff, DIFF_MAX_BYTES)
    # Each side against the whole cap, independently: the in-flight change is
    # what the reviewer is deciding about and must not be squeezed by the size
    # of the documents ahead of it.
    landed_diff, landed_truncated = _truncate_at_file_boundary(landed.diff, DIFF_MAX_BYTES)
    return {
        "work_item_id": wid,
        "base_ref": base,
        "files": change.files,
        "diff": diff,
        "untracked": change.untracked,
        "truncated": truncated,
        "landed": {
            "commits": landed.commits,
            "files": landed.files,
            "diff": landed_diff,
            "truncated": landed_truncated,
        },
        # A reviewer told the diff is partial and not told how much is missing
        # or where the rest is has been given half a warning (spec F §2.2).
        # `worktree_path` is no new disclosure: GET /work-items/{wid} has always
        # returned it, to the same authenticated caller.
        "diff_max_bytes": DIFF_MAX_BYTES,
        "worktree_path": str(worktree),
    }


async def _refuse_artifact(st, wid: str, rel: str, reason: str) -> None:
    """Record why an artifact read was refused, on the item's own timeline.

    Spec §4's premise is a reviewer with no access to the server's log, so a
    `logger.warning` is not a substitute for this -- it is what this replaces.
    Not called for `rel is None` (`_gate_artifact` found no file): that is the
    ordinary state of a gate whose agent session has not finished yet, and a
    UI that polls the detail endpoint while it waits would turn "no document
    yet" into an event on every poll. Everything past that point means
    `_gate_artifact` *did* find a file and something went wrong reading the
    one it found, which is the actual refusal this exists to explain.
    """
    payload = {"path": rel, "reason": reason}
    await st.db.write(lambda c: events.append(c, wid, "artifact_refused", payload))


async def _read_worktree_artifact(st, wid: str, rel: str) -> tuple[str, bool] | None:
    """Safely read `rel` out of `wid`'s worktree via `worktree_read.read_worktree_file`
    (resolve, check containment, open with no symlink followed anywhere along the
    path, cap the read at `DIFF_MAX_BYTES`), and record why via `_refuse_artifact`
    on any failure -- shared by the reviewer-facing read and gate-approval
    ingestion (`approve_gate`) so both trust the exact same containment walk.
    """
    worktree = st.run_dirs.worktrees / wid
    result, reason = read_worktree_file(worktree, rel, DIFF_MAX_BYTES)
    if result is None:
        if reason == "escaped_containment":
            # A reviewer's browser learns nothing about the server's disk;
            # this warning is for whoever is watching the server's own log.
            logger.warning("artifact for %s resolves outside its worktree: %s", wid, rel)
        await _refuse_artifact(st, wid, rel, reason)
        return None
    return result.text, result.truncated


@api_router.get("/work-items/{wid}/artifact")
async def get_work_item_artifact(wid: str, request: Request):
    """The pending gate's document, for a reviewer with no filesystem access.

    Read off disk rather than out of the index: the index ingests committed
    files on its own schedule, and a reviewer who is looking at the gate right
    now must see what the agent just wrote.
    """
    st = request.app.state
    row = deps._work_item_row(st, wid)  # 404s on an unknown work item
    gate = board._pending_gate(st, wid)
    rel = board._gate_artifact(st, row, gate)
    if rel is None:
        raise HTTPException(404, "this work item's gate has no artifact")
    result = await _read_worktree_artifact(st, wid, rel)
    if result is None:
        raise HTTPException(404, "this work item's gate has no artifact")
    text, truncated = result
    fm, body = ingest_mod.split_front_matter(text)
    chain = store.materialized_chain_of(row)
    shown = None
    if chain is not None and any(
        n.id == gate and n.node.artifact == revision.CHAIN_REVISION for n in chain.chain.nodes
    ):
        # A change set is JSON for Kraft to apply; the person deciding reads
        # it rendered, with the diff it makes and anything that stops it.
        body, revised = revision.render(text, chain, gate, st.library)
        # What an approval must still apply (Kraft-ze1yj).
        if revised is not None and revised is not chain:
            shown = revision.digest(revised)
            await st.db.write(lambda c: store.show_revision(c, wid, gate, shown))
    return {
        "work_item_id": wid,
        "path": rel,
        # A chain revision's approval sends this back (Kraft-ec66w); absent
        # where approving applies nothing.
        **({"digest": shown} if shown else {}),
        "title": ingest_mod.derive_title(rel, fm, body),
        # Front matter stripped: it is the contract's plumbing, not the
        # document, and a reviewer reading a spec should not have to skip it.
        "content": body,
        "truncated": truncated,
        "artifact_max_bytes": DIFF_MAX_BYTES,
    }


async def _ingest_approved_gate_artifact(st, row, gate: str) -> None:
    """Persist the gate's artifact into the index now that it is approved.

    Approval, not the worktree's eventual removal, is the hook: it is the
    first moment the content is accepted rather than still being drafted, and
    it is a point every artifact-carrying gate already passes through --
    unlike worktree teardown, which today only happens on `abandon`. Nothing
    Kraft wrote under `.engineering/` is committed
    (`forge._work_product_pathspec`), so this is the artifact's only durable
    copy once its worktree is eventually gone.

    Best-effort end to end: a missing or unreadable artifact is
    `_read_worktree_artifact` recording why on the item's own timeline, and an
    indexer failure (a locked index, a read-only file) is caught and logged
    here rather than raised -- same contract `ingest_session_summary` gives
    the event-drain loop, just enforced at this call site instead, since
    approval is a synchronous request a reviewer is waiting on, not a
    background drain. A gate the agent already satisfied must not be blocked
    from clearing by the index's own health.
    """
    rel = board._gate_artifact(st, row, gate)
    if rel is None:
        return
    result = await _read_worktree_artifact(st, row["id"], rel)
    if result is None:
        return
    text, _truncated = result
    try:
        await st.indexer.ingest_gate_artifact(
            repo=row["repo"], work_item_id=row["id"], path=rel, content=text
        )
    except Exception:  # noqa: BLE001
        logger.exception("gate artifact ingest failed for %s (%s)", row["id"], rel)
