"""The VS Code extension reads these board fields; the board must keep them."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from support.api import _in_state

FIELDS = json.loads(
    (
        Path(__file__).resolve().parents[2] / "vscode" / "contract" / "work-item-fields.json"
    ).read_text()
)
DOORS = json.loads((Path(__file__).parent / "lifecycle_doors.json").read_text())

# The extension's list call: cancelled items stay on its Done group.
LIST = "/api/work-items?include_abandoned=true"


def test_the_board_carries_every_field_the_extension_reads(client, tmp_path):
    client.post(
        "/api/work-items", json={"autostart": True, "title": "contract", "repo": str(tmp_path)}
    )
    [item] = client.get(LIST).json()["items"]
    assert set(FIELDS) <= set(item), sorted(set(FIELDS) - set(item))


@pytest.mark.parametrize(
    ("state", "display", "stop"),
    [
        (
            "question",
            "needs_you",
            {
                "kind": "question",
                "node": "implementation",
                "reason": "needs_context: Keep the header?",
                "resume_at": None,
            },
        ),
        ("abandoned", "cancelled", None),
    ],
    ids=["needs-human-carries-its-stop", "cancelled-stays-listed"],
)
def test_the_extensions_list_call_carries_the_state_it_shows(client, repo, state, display, stop):
    wid = _in_state(client, repo, DOORS["states"][state])
    row = next(i for i in client.get(LIST).json()["items"] if i["id"] == wid)
    assert row["display_status"] == display
    assert row["stop"] == stop


def test_the_detail_the_extension_refreshes_a_row_from_carries_the_same_state(client, repo):
    """The extension replaces a row with the detail on every event: the detail
    must say what the list row said, `step` and `fallback` included."""
    wid = _in_state(client, repo, DOORS["states"]["question"])
    row = next(i for i in client.get(LIST).json()["items"] if i["id"] == wid)
    detail = client.get(f"/api/work-items/{wid}").json()
    assert detail["display_status"] == row["display_status"] == "needs_you"
    assert {k: detail["stop"][k] for k in ("kind", "node", "reason", "resume_at")} == row["stop"]
    assert {k: detail[k] for k in ("step", "fallback")} == {"step": None, "fallback": None}
