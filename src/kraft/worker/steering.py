"""Per-repo and per-hook standards, injected through the system prompt.

The context-injection boundary (`00_overview.md` glossary) bans `CLAUDE.md`,
`AGENTS.md` and any repo file as a context channel. That rule governs the
*channel*, not the existence of standards — this is the other half: authored,
Kraft-owned files under `$KRAFT_HOME/templates/steering/`, reaching the agent
through the per-invocation system prompt the adapter already builds. Kraft never
reads or writes a steering file inside a target repository, which is why a name
containing a path separator is rejected outright.

`Steering.validate` and `Steering.read` are separate because the text must
never be written back into `repos.yaml`: its dicts round-trip through
`GET /repos` and the Settings screens, so resolving into them would inline the
bodies into the operator's config and delete the names.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, ConfigDict


class SteeringError(Exception):
    pass


class Steering(BaseModel):
    """One steering directory: which file a name resolves to, and whether the
    block those files make fits the byte budget. The block is built here and
    measured here, so what is counted and what is injected cannot drift."""

    model_config = ConfigDict(frozen=True)

    #: Total injected budget, counting the header and separators, not just
    #: bodies. An oversized system prompt degrades every launch and costs
    #: money on each.
    MAX_BYTES: ClassVar[int] = 8192
    HEADING: ClassVar[str] = "\n\n## Project standards\n\n"
    _SEP: ClassVar[str] = "\n\n"

    dir: Path

    @classmethod
    def block(cls, bodies: Sequence[str]) -> str:
        """The text a launch's context carries for these bodies, `""` for none."""
        return cls.HEADING + cls._SEP.join(bodies) if bodies else ""

    @classmethod
    def check_budget(cls, bodies: Sequence[str], *, where: str) -> None:
        """Raise when `block(bodies)` is over `MAX_BYTES`. Called over one
        config file's own names at load, and again at launch over the repo's
        and the task's combined, since two lists that each fit can still blow
        the shared budget once joined."""
        total = len(cls.block(bodies).encode())
        if total > cls.MAX_BYTES:
            raise SteeringError(
                f"{where}: steering totals {total} bytes, over the {cls.MAX_BYTES} byte budget"
            )

    def path(self, name: str, *, where: str) -> Path:
        """The file `name` resolves to, or raise. The Settings editor goes
        through this too, so it cannot accept a name a config load refuses."""
        if "/" in name or "\\" in name or name in ("", ".", "..") or name.startswith("."):
            raise SteeringError(
                f"{where}: steering name {name!r} must be a bare file name — Kraft "
                "never reads a steering file from outside its own templates directory"
            )
        return self.dir / f"{name}.md"

    def validate(self, names: Sequence[str], *, where: str) -> None:
        """Every name resolves and the total fits the budget, or raise.

        Runs at config load, against the *real* steering directory — not
        against whatever directory a candidate file was written into for
        validation.
        """
        bodies: list[str] = []
        for name in names:
            path = self.path(name, where=where)
            if not path.is_file():
                raise SteeringError(f"{where}: steering {name!r} not found at {path}")
            try:
                bodies.append(path.read_text())
            except (OSError, ValueError) as exc:
                raise SteeringError(
                    f"{where}: cannot read steering {name!r} at {path}: {exc}"
                ) from exc
        self.check_budget(bodies, where=where)

    def read(self, names: Sequence[str]) -> tuple[str, ...]:
        """Bodies for `names`, in order. Called at dispatch, after `validate`.

        A file deleted, chmod'd or corrupted between validation (config load)
        and this call (dispatch) is reported as a `SteeringError` naming the
        file, so a caller catching `SteeringError` around dispatch sees it as
        the steering problem it is rather than as a bare `FileNotFoundError`
        (Kraft-fza). The budget is the launch's to re-check, over everything
        it assembles (`agent.resolve_invocation`), so a file that grew past it
        after `validate` ran is still caught — at launch rather than at save.
        """
        bodies: list[str] = []
        for name in names:
            path = self.path(name, where="steering")
            try:
                bodies.append(path.read_text())
            except (OSError, ValueError) as exc:
                raise SteeringError(f"steering: cannot read {name!r} at {path}: {exc}") from exc
        return tuple(bodies)
