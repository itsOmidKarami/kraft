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


def default_harnesses_dir() -> Path:
    """Where an operator may override or add a harness definition.

    Same shape and same reason as `default_skills_dir`: the shipped harnesses
    live in the package (`kraft.harness.BUNDLED`), and this directory exists
    only when someone has deliberately added or overridden one. Not seeded by
    `cli.seed_home` -- a seeded copy would freeze at whichever version the
    operator first installed, which is the drift measured live on 2026-09-13
    (Kraft-717xy).
    """
    return kraft_home() / "templates" / "harnesses"


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
    def pid(self) -> Path:
        """The running server's pid, for `kraft admin stop`.

        Under the run dir rather than a fixed system path because it belongs to
        one instance: a `just dev` server and an installed one must be able to
        run at once, and they differ only by where this directory points.
        """
        return self.base / "kraft.pid"

    @property
    def worktrees(self) -> Path:
        return self.base / "worktrees"

    @property
    def attachments(self) -> Path:
        """Intake attachments, copied here at intake rather than referenced in
        place (Kraft-eqgn).

        The gate an attachment satisfies is trimmed out of `chain_definition`
        at intake and cannot be put back, so the document that justified the
        trim has to be one Kraft owns from that moment. Referencing a path in
        someone else's working tree meant a file deleted in between left the
        item running with its spec and plan gates gone and nothing said.
        """
        return self.base / "attachments"

    def ensure(self) -> RunDirs:
        for d in (self.logs, self.results, self.worktrees, self.attachments):
            d.mkdir(parents=True, exist_ok=True)
        return self
