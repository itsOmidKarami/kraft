"""The VS Code extension reads these board fields; the board must keep them."""

from __future__ import annotations

import json
from pathlib import Path

FIELDS = json.loads(
    (
        Path(__file__).resolve().parents[2] / "vscode" / "contract" / "work-item-fields.json"
    ).read_text()
)


def test_the_board_carries_every_field_the_extension_reads(client, tmp_path):
    client.post("/api/work-items", json={"title": "contract", "repo": str(tmp_path)})
    [item] = client.get("/api/work-items").json()["items"]
    assert set(FIELDS) <= set(item), sorted(set(FIELDS) - set(item))
