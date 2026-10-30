"""The forge account and backend an item started with, by mount (`""` is the root)."""

from __future__ import annotations

import json

__all__ = ["forge_pins", "set_forge_pin"]


def forge_pins(conn, work_item_id: str) -> dict[str, dict]:
    row = conn.execute("SELECT forge_pins FROM work_items WHERE id = ?", (work_item_id,)).fetchone()
    return json.loads(row[0]) if row and row[0] else {}


def set_forge_pin(conn, work_item_id: str, mount: str, account: str | None, backend: str) -> None:
    pins = forge_pins(conn, work_item_id)
    pins[mount] = {"account": account, "backend": backend}
    conn.execute(
        "UPDATE work_items SET forge_pins = ? WHERE id = ?", (json.dumps(pins), work_item_id)
    )
