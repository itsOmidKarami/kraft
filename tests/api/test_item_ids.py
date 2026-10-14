"""A work item named by the start of its id. The API makes the id whole before
any route runs, so every handler, and the self-action guard, sees the full id
(`kraft.api.item_ids`)."""

from __future__ import annotations

import pytest
from support import permissions
from support.api import _paused

# Two long ids sharing their first six characters, and a short one that is
# the start of both.
FIRST = "abcd1111" + "0" * 24
SECOND = "abcd1122" + "0" * 24
SHORT = "abcd"
# An id with a character outside `[0-9a-z]`: Kraft mints none, and its start
# is no start of an id.
MIXED = "Wxyz3333" + "0" * 24


def _items():
    for sid, wid in (
        ("s-first", FIRST),
        ("s-second", SECOND),
        ("s-short", SHORT),
        ("s-mixed", MIXED),
    ):
        permissions.seed_session(sid=sid, wid=wid)


@pytest.mark.parametrize(
    ("given", "status", "named"),
    [
        (FIRST, 200, FIRST),
        ("abcd111", 200, FIRST),
        (SHORT, 200, SHORT),
        ("abcd11", 409, None),
        ("abc", 404, None),
        ("abcd9", 404, None),
        ("ABCD111", 404, None),
        (MIXED, 200, MIXED),
        ("Wxyz333", 404, None),
    ],
    ids=[
        "whole",
        "unique-start",
        "exact-wins",
        "ambiguous",
        "short",
        "unknown",
        "uppercase",
        "outside-chars-whole",
        "outside-chars-start",
    ],
)
def test_an_id_or_its_unique_start_names_the_item(client, given, status, named):
    _items()

    r = client.get(f"/api/work-items/{given}")

    assert r.status_code == status, r.text
    if status == 200:
        assert r.json()["id"] == named
    elif status == 409:
        assert r.json()["detail"] == f"'abcd11' is the start of 2 work items: {FIRST}, {SECOND}"
    else:
        assert r.json()["detail"] == "unknown work item"


def test_an_action_on_the_start_of_an_id_acts_on_the_whole_item(client, repo):
    """Every handler after the lookup goes on using its `wid` argument
    (`store.budget_spend(c, wid)` and the like), so the path itself must
    carry the whole id, not only the lookup succeed."""
    wid = _paused(client, repo)

    r = client.patch(f"/api/work-items/{wid[:8]}", json={"budget_usd": 5})

    assert r.status_code == 200, r.text
    assert r.json()["id"] == wid
    assert client.get(f"/api/work-items/{wid}").json()["budget_usd"] == 5


def test_a_worker_naming_its_own_item_by_its_start_is_refused(client):
    """`deps.forbid_self_action` runs before the handler's lookup and compares
    strings: without the rewrite, a start of the worker's own id passed it."""
    permissions.seed_session(sid="s-own", wid=FIRST)

    r = client.patch(
        f"/api/work-items/{FIRST[:8]}",
        json={"budget_usd": 5},
        headers={"X-Kraft-Session-Id": f"{FIRST}/s-own"},
    )

    assert r.status_code == 403, r.text
    assert f"cannot act on its own work item ({FIRST})" in r.json()["detail"]
