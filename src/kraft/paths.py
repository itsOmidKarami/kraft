from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def kraft_home() -> Path:
    """The directory an installed Kraft keeps everything in.

    One home, not a path guessed from `__file__`: the source tree is the install
    only while Kraft runs from a checkout, and under site-packages counting
    parent directories lands somewhere meaningless. `KRAFT_HOME` moves it, which
    is how `just dev` gets an instance that cannot touch the real one.
    """
    return Path(os.environ.get("KRAFT_HOME") or Path.home() / ".kraft").expanduser()


def default_run_dir() -> Path:
    return kraft_home() / "run"


def default_templates_dir() -> Path:
    return kraft_home() / "templates"


def default_skills_dir() -> Path:
    """Where an operator may override a bundled method file.

    Not seeded by `cli.seed_home` and usually absent: the shipped skills live in
    the package (`kraft.skill.BUNDLED`), and this directory exists only when
    someone has deliberately overridden one.
    """
    return kraft_home() / "skills"


#: Built SPA and default config, copied in by `just install`. Absent in a plain
#: source checkout — the API then serves no SPA, and `just dev` points
#: KRAFT_FRONTEND_DIST at vite's output instead.
BUNDLED = Path(__file__).parent / "_bundled"


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
