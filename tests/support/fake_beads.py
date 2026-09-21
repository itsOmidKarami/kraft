"""An in-memory stand-in for `kraft.adapters.beads`, installed for every test by
`tests/conftest.py` (Kraft-qmhfc).

Most tests need a work item to exist, not a bead: the real adapter spawns `bd`
(Dolt) at ~0.5s a call, which was ~44% of the suite's wall time. This fakes the
adapter's own functions, not the `bd` binary, so there is no second
implementation of bd's CLI to keep in step. What bd's CLI actually does is
pinned by the `e2e` tests that opt out of this fake and run the real binary.

It remembers enough for callers to work: unique ids, intake/complete per
workspace, and `search`/`ready`/`blocked_by` answered from that state.
`block()` adds an edge for a unit test that wants a blocked bead.

A workspace is found the way bd finds one: the nearest `.beads/` at or above
`cwd`. Where there is none, `intake` fails the way `bd create` does, and the
readers answer `[]` the way the adapter does when bd exits non-zero -- so a
unit test cannot pass on a bead bd would never have filed.
`tests/test_support_harness.py` runs one scenario against this and real bd.
"""

from __future__ import annotations

import itertools
import os
from pathlib import Path


class FakeBeads:
    def __init__(self) -> None:
        self._ids = itertools.count(1)
        #: workspace (realpath of cwd) -> bead id -> bead
        self.workspaces: dict[str, dict[str, dict]] = {}

    @staticmethod
    def _root(cwd: str | None) -> str | None:
        """The directory holding the nearest `.beads/` at or above `cwd`."""
        here = Path(os.path.realpath(cwd or os.getcwd()))
        return next((str(d) for d in (here, *here.parents) if (d / ".beads").is_dir()), None)

    def _ws(self, cwd: str | None) -> dict[str, dict]:
        root = self._root(cwd)
        return self.workspaces.setdefault(root, {}) if root else {}

    def all(self) -> dict[str, dict]:
        """Every bead, whichever workspace it was filed in."""
        return {k: v for ws in self.workspaces.values() for k, v in ws.items()}

    async def intake(
        self, title: str, *, description: str | None = None, cwd: str | None = None
    ) -> str:
        if self._root(cwd) is None:
            # What the adapter raises for bd's own refusal (Kraft-ibwj).
            raise RuntimeError("bd create failed (exit 1): Error: no beads database found")
        bead_id = f"TEST-fake{next(self._ids)}"
        self._ws(cwd)[bead_id] = {
            "id": bead_id,
            "title": title,
            "description": description or "Created by the Kraft orchestrator.",
            "status": "open",
            "priority": 2,
            "issue_type": "task",
            "blocked_by": [],
        }
        return bead_id

    async def complete(self, bead_id: str, *, cwd: str | None = None) -> None:
        bead = self._ws(cwd).get(bead_id)
        if bead is None:
            # `bd close` on an unknown id exits non-zero, and `complete` runs it
            # with check=True.
            raise RuntimeError(f"fake bd close: no bead {bead_id!r} in {cwd!r}")
        bead["status"] = "closed"

    async def search(self, q: str, *, cwd: str | None = None, limit: int = 5) -> list[dict]:
        rows = [b for b in self._ws(cwd).values() if q.lower() in b["title"].lower()]
        return [{k: b[k] for k in ("id", "title", "status", "issue_type")} for b in rows[:limit]]

    async def ready(self, *, cwd: str | None = None) -> list[dict]:
        ws = self._ws(cwd)
        return [
            {k: b[k] for k in ("id", "title", "priority", "issue_type", "description")}
            for b in ws.values()
            if b["status"] == "open" and not self._open_blockers(ws, b)
        ]

    async def blocked_by(self, bead_ids: list[str], *, cwd: str | None = None) -> list[str]:
        ws = self._ws(cwd)
        seen: list[str] = []
        for bead_id in bead_ids:
            bead = ws.get(bead_id)
            for blocker in self._open_blockers(ws, bead) if bead else []:
                if blocker not in seen:
                    seen.append(blocker)
        return seen

    @staticmethod
    def _open_blockers(ws: dict[str, dict], bead: dict) -> list[str]:
        return [b for b in bead["blocked_by"] if ws.get(b, {}).get("status") != "closed"]

    def block(self, bead_id: str, blocker_id: str, *, cwd: str | None = None) -> None:
        """`bd dep add bead_id blocker_id --type blocks`."""
        self._ws(cwd)[bead_id]["blocked_by"].append(blocker_id)
