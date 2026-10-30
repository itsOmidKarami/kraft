"""A one-off chain's review: what to read before starting it, and the approval."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Literal

from fastapi import HTTPException, Request
from pydantic import BaseModel

from kraft import store
from kraft.api import api_router, deps
from kraft.api.routes import drafts, gates
from kraft.vocab import GateEvent


class ApproveOneOff(BaseModel):
    #: The digest the reviewer was shown (`GET .../one-off`).
    digest: str
    door: Literal["web", "phone"] = "web"


def _record_or_404(row) -> dict:
    record = store.one_off_of(row)
    if record is None:
        raise HTTPException(404, "this work item has no one-off chain")
    return record


@api_router.get("/work-items/{wid}/one-off")
async def get_one_off(wid: str, request: Request):
    """The one-off chain as it was filed, what in it is custom, and whether a
    person has approved it. `provenance` is what the base chain looked like when
    the item was filed; a later change to the base does not show here."""
    st = request.app.state
    row = deps._work_item_row(st, wid)
    record = _record_or_404(row)
    provenance = record["provenance"]
    added = []
    if provenance:
        chain = store.materialized_chain_of(row)
        nodes = {n.id: n for n in chain.chain.nodes} if chain is not None else {}
        added = [
            drafts.node_check(st, nodes[id])
            for id, origin in provenance["nodes"].items()
            if origin == "added" and id in nodes
        ]
    redrafted = [
        json.loads(r["payload"])
        for r in st.db.read(
            lambda c: c.execute(
                "SELECT payload FROM events WHERE work_item_id = ? AND type = ? ORDER BY seq",
                (wid, GateEvent.CHAIN_REVISED),
            ).fetchall()
        )
        if json.loads(r["payload"]).get("source") == "draft"
    ]
    return {
        "extends": provenance["parent"] if provenance else None,
        "authored": record["authored"],
        "custom": record["custom"],
        "provenance": provenance,
        "digest": store.review_digest(row),
        "approval": json.loads(row["one_off_approval"]) if row["one_off_approval"] else None,
        "needs_review": store.one_off_unapproved(row),
        "added": added,
        "redrafted": redrafted,
    }


@api_router.post("/work-items/{wid}/one-off/approve")
async def approve_one_off(wid: str, body: ApproveOneOff, request: Request):
    """Approve the custom content of an item's one-off chain, as shown. Only a
    person does; the digest ties the approval to the chain they read, so a
    chain that changed since is refused (409) and nothing is approved."""
    st = request.app.state
    if gates._decided_by(request) != "human":
        raise HTTPException(
            403, "only a person approves a one-off chain's custom content, in the web UI"
        )
    row = deps._work_item_row(st, wid)
    record = _record_or_404(row)
    if not record["custom"]:
        raise HTTPException(409, "nothing to approve: this one-off chain is library-only")
    if row["current_node_id"] is not None:
        raise HTTPException(409, "work item has already started")
    approval = {
        "digest": None,
        "by": "human",
        "door": body.door,
        "at": datetime.now(UTC).isoformat(),
    }
    if not await st.db.write(lambda c: store.approve_one_off(c, wid, approval, body.digest)):
        raise HTTPException(409, "the one-off chain changed since it was reviewed; review it again")
    return {"id": wid, "approval": approval}
