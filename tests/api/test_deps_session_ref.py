"""Who is calling, from `X-Kraft-Session-Id`: `<wid>/<sid>` names one
session of one item, matched whole; a bare id is a session from before
per-item ids (spec "Item files and IDs", part 3)."""

from __future__ import annotations

import pytest
from support import permissions
from support.permissions import templates_dir  # noqa: F401 -- the fixture

W1, W2 = "k7m2x9qa", "b4t1z8re"
#: A session id minted before per-item ids.
OLD = "8cbfe6e27c1044b3e445c0f0d726357d"
#: Another, which two items' rows are seeded to share: no draw makes that
#: happen, but the guard does not rely on it.
SHARED = "0f9e8d7c6b5a49382716051f2e3d4c5b"


@pytest.mark.parametrize(
    ("header", "target", "status"),
    [
        (f"{W1}/p3n8w2", W1, 403),
        (f"{W2}/p3n8w2", W1, 200),
        (f"{W2}/s-y", W1, 200),
        (f"{W1}/p3n8w2", W1[:4], 403),
        (OLD, W1, 403),
        (f"{W1}/{OLD}", W1, 403),
        (SHARED, W1, 403),
        ("p3n8w2", W1, 400),
        (OLD[:8], W1, 400),
        (f"{W1[:4]}/p3n8w2", W1, 200),
        (f"{W1.upper()}/p3n8w2", W1, 200),
        (None, W1, 200),
        (f"{W1}/", W1, 400),
        ("/p3n8w2", W1, 400),
        (f"{W1}/p3n8w2/x", W1, 400),
    ],
    ids=[
        "own-item-s-session",
        "same-id-other-item",
        "other-item-s-session",
        "own-item-named-by-its-start",
        "pre-upgrade-bare",
        "pre-upgrade-qualified",
        "bare-id-two-items-share",
        "bare-per-item-id",
        "bare-start-of-a-pre-upgrade-id",
        "header-item-by-its-start-names-no-session",
        "header-item-in-another-case-names-no-session",
        "no-header",
        "malformed-no-session",
        "malformed-no-item",
        "malformed-a-second-slash",
    ],
)
def test_the_calling_session_is_read_with_its_item(client, header, target, status):
    """Both items have a session `p3n8w2`: only the qualified reference says
    whose, and only `W1`'s acting on `W1` is refused. A bare id is taken only
    at the 32 characters of a pre-upgrade one, and one two items share is
    refused if either is the target's; a bare per-item id is a 400, since
    it says nothing of whose it is. The header's item is matched whole,
    never as a start (a prefix in the path is the middleware's,
    `api.item_ids`), and a value that is no reference is a 400, never split
    some way that finds a session."""
    permissions.seed_session(sid="s-x", wid=W1)
    permissions.seed_session(sid="s-y", wid=W2)
    # W2's first: a lookup that took the first row of a bare id would find it.
    permissions.add_session("p3n8w2", W2)
    permissions.add_session("p3n8w2", W1)
    permissions.add_session(OLD, W1)
    permissions.add_session(SHARED, W2)
    permissions.add_session(SHARED, W1)
    headers = {"X-Kraft-Session-Id": header} if header else {}

    r = client.patch(f"/api/work-items/{target}", json={"budget_usd": 5}, headers=headers)

    assert r.status_code == status, r.text
    if status == 403:
        assert f"cannot act on its own work item ({W1})" in r.json()["detail"]
    elif status == 400:
        detail = r.json()["detail"]
        assert "neither <work item id>/<session id> nor a 32-character session id" in detail
