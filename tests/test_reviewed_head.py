"""`kraft.reviewed_head`: the pin the final review leaves, and the only thing
that moves it, Kraft's own clean rewrite of that exact head. The merge's
check against it is walked in `tests/adapters/forge/test_run_reviewed_head.py`."""

from __future__ import annotations

import pytest

from kraft import events, reviewed_head, store

A, B, C = "a" * 40, "b" * 40, "c" * 40


@pytest.mark.parametrize(
    ("pin", "before", "after", "pinned"),
    [
        ({"/wt": A}, {"/wt": A}, {"/wt": B}, {"/wt": B}),
        ({"/wt": A, "/m": C}, {"/wt": A, "/m": B}, {"/wt": B, "/m": A}, {"/wt": B, "/m": C}),
        ({"/wt": A, "/m": C}, {"/wt": A, "/m": C}, {"/wt": A, "/m": B}, {"/wt": A, "/m": B}),
        ({"/wt": A}, {"/wt": B}, {"/wt": C}, {"/wt": A}),
        ({"/wt": A}, {"/wt": A}, {"/wt": None}, {"/wt": A}),
        (None, {"/wt": A}, {"/wt": B}, None),
    ],
    ids=[
        "the-approved-head-rebased",
        "only-the-approved-checkout-moves",
        "a-member-rebased",
        "an-unreviewed-head-rebased",
        "unreadable-after",
        "nothing-pinned",
    ],
)
async def test_only_a_rewrite_of_the_approved_head_moves_the_pin(
    database, pin, before, after, pinned
):
    """A rebase of the head the final review approved is that head, rebased:
    the pin follows it, checkout by checkout. A head nobody approved, rebased,
    is still one nobody approved, and a tip that cannot be read is never
    pinned. With nothing pinned, nothing is."""
    await database.write(
        lambda c: store.create_work_item(
            c, id="w1", repo="/r", bead_id="B", title="t", chain_template="t", chain_definition="{}"
        )
    )
    if pin is not None:
        approved = {"gate": "final", "by": "human", "tips": pin}
        await database.write(lambda c: events.append(c, "w1", "gate_approved", approved))

    await reviewed_head.repin(database, "w1", before, after, cause="rebase")

    assert database.read(lambda c: reviewed_head.pinned(c, "w1")) == pinned
    repinned = database.read(lambda c: events.read_after(c, 0, "w1"))
    moved = [e for e in repinned if e["type"] == "reviewed_head_repinned"]
    assert len(moved) == (1 if pin is not None and pinned != pin else 0)
