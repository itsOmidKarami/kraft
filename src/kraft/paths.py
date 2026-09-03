from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RunDirs:
    base: Path

    @property
    def db(self) -> Path:
        return self.base / "orchestrator.db"

    @property
    def index_db(self) -> Path:
        return self.base / "index.db"

    @property
    def logs(self) -> Path:
        return self.base / "logs"

    @property
    def results(self) -> Path:
        return self.base / "results"

    @property
    def worktrees(self) -> Path:
        return self.base / "worktrees"

    def ensure(self) -> RunDirs:
        for d in (self.logs, self.results, self.worktrees):
            d.mkdir(parents=True, exist_ok=True)
        return self
