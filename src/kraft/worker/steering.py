"""Standards injected through the system prompt, and the one-time migration
of the pre-1.0 steering files into the library.

The context-injection boundary (`00_overview.md` glossary) bans `CLAUDE.md`,
`AGENTS.md` and any repo file as a context channel. That rule governs the
*channel*, not the existence of standards -- this is the other half: named
`steering:` profiles in Kraft's own `library.yaml`, selected by a task or by a
repository's `repos.yaml` entry, frozen into the work item's snapshot at
intake, and reaching the agent through the per-invocation system prompt.
Kraft never reads a steering text from inside a target repository.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import ClassVar

import yaml
from pydantic import BaseModel, ConfigDict

logger = logging.getLogger(__name__)

#: Where 0.x and the 1.0 release candidates kept repository steering files.
LEGACY_DIR = "steering"


class SteeringError(ValueError):
    """A steering selection that cannot be supplied. A `ValueError`, so every
    intake door already answering a refusal as a 422 answers this one too."""


class Steering(BaseModel):
    """The block steering texts make, and whether it fits the byte budget. The
    block is built here and measured here, so what is counted and what is
    injected cannot drift."""

    model_config = ConfigDict(frozen=True)

    #: Total injected budget, counting the header and separators, not just
    #: bodies. An oversized system prompt degrades every launch and costs
    #: money on each.
    MAX_BYTES: ClassVar[int] = 8192
    HEADING: ClassVar[str] = "\n\n## Project standards\n\n"
    _SEP: ClassVar[str] = "\n\n"

    @classmethod
    def block(cls, bodies: Sequence[str]) -> str:
        """The text a launch's context carries for these bodies, `""` for none."""
        return cls.HEADING + cls._SEP.join(bodies) if bodies else ""

    @classmethod
    def check_budget(cls, bodies: Sequence[str], *, where: str) -> None:
        """Raise when `block(bodies)` is over `MAX_BYTES`. Called over one
        repository's own profiles when it is saved and at intake, and again at
        launch over the repository's and the task's combined, since two lists
        that each fit can still blow the shared budget once joined."""
        total = len(cls.block(bodies).encode())
        if total > cls.MAX_BYTES:
            raise SteeringError(
                f"{where}: steering totals {total} bytes, over the {cls.MAX_BYTES} byte budget"
            )


def select(names: Sequence[str], profiles: Mapping[str, str], *, where: str) -> dict[str, str]:
    """`names`' texts from `profiles` (library steering, name to
    instructions), in order and within budget, or raise naming the first name
    the library does not define."""
    missing = [n for n in names if n not in profiles]
    if missing:
        raise SteeringError(
            f"{where}: steering {missing[0]!r} is not a steering profile in "
            "templates/library.yaml; define it under `steering:` there "
            "(Settings > Library), or remove the name"
        )
    selected = {n: profiles[n] for n in names}
    Steering.check_budget(list(selected.values()), where=where)
    return selected


def for_repository(
    entry,
    frozen: Mapping[str, Mapping[str, str]] | None,
    live: Mapping[str, str] | None,
    *,
    item_repo: str | None = None,
) -> tuple[str, ...]:
    """The repository steering one launch injects, for the repository entry
    it runs in (`None` for none).

    `frozen` is the item's snapshot's `repository_steering`: the texts it was
    filed with, keyed by repository path, and the answer whatever `repos.yaml`
    or the library say now. `None` is a snapshot stored before that was
    frozen, whose steering came from files read at each launch; those files
    are library profiles now (`migrate_files`), so it reads the entry's names
    against the live library, `live`, and raises naming a name it lacks.

    A repos.yaml `path:` can be hand-edited while an item is in flight
    (Kraft-jzdyp), so `entry.path` -- read live, at launch -- is not on its
    own a reliable key into `frozen`, which was built at intake. `item_repo`
    is the item's own repo as recorded at intake (the work item row's `repo`
    column): stable for the item's own repository regardless of what
    `repos.yaml` says today, so a root launch (`item_repo` given) is looked
    up by it alone -- present or not, that answers the question, since the
    root was simply unsteered at intake if it is absent. `entry.path` is
    used only for a fanned-out member repository (`item_repo` is `None`
    there): a member's path moving mid-flight still silently loses its
    steering -- pre-existing, and Kraft-ku1um's to fix, not this one's."""
    if entry is None:
        return ()
    if frozen is not None:
        key = item_repo if item_repo is not None else entry.path
        return tuple(frozen.get(key, {}).values())
    if not entry.steering:
        return ()
    where = f"repos.yaml: {entry.path} (an item filed before repository steering was frozen)"
    return tuple(select(entry.steering, live or {}, where=where).values())


def _indented(entries: Mapping[str, dict]) -> str:
    dumped = yaml.safe_dump(dict(entries), sort_keys=False, allow_unicode=True, width=10**6)
    return "".join(f"  {line}" if line.strip() else line for line in dumped.splitlines(True))


def migrate_files(templates_dir: Path) -> list[str]:
    """Fold `templates/steering/*.md` into `library.yaml`'s `steering:` and
    move the directory aside. Returns the names it added.

    Runs at every start, and does nothing once the directory is gone. Each
    file whose name the library does not define yet becomes
    `steering: {<name>: {instructions: <file text>}}`, so a `repos.yaml`
    naming it keeps resolving to the same text. A name the library already
    defines keeps the library's text; an empty or unreadable file is skipped.
    Both are logged, and nothing is deleted: the whole directory moves to
    `steering.pre-1.0/` beside it.

    The new entries are inserted as text, so the file's comments survive. If
    that text does not parse back to exactly the old library plus the new
    entries (an unusual layout), the merged mapping is written instead, with
    the original kept as `library.yaml.pre-1.0`.
    """
    src = templates_dir / LEGACY_DIR
    if not src.is_dir():
        return []
    library_path = templates_dir / "library.yaml"
    try:
        text = library_path.read_text() if library_path.is_file() else ""
        data = yaml.safe_load(text) or {}
    except (OSError, ValueError, yaml.YAMLError) as exc:
        data, text = exc, ""
    if not isinstance(data, dict) or not isinstance(data.get("steering") or {}, dict):
        # The library is not loadable either, so nothing would resolve; keep
        # the files where they are and try again at the next start.
        logger.error("steering migration: %s is not a mapping, left %s in place", library_path, src)
        return []
    existing = data.get("steering") or {}
    added: dict[str, dict] = {}
    for path in sorted(src.glob("*.md")):
        try:
            body = path.read_text()
        except (OSError, ValueError) as exc:
            logger.warning("steering migration: skipped unreadable %s: %s", path, exc)
            continue
        if path.stem in existing:
            logger.warning(
                "steering migration: library.yaml already defines steering %r; kept it, "
                "and %s is only in the moved-aside copy",
                path.stem,
                path.name,
            )
        elif body.strip():
            added[path.stem] = {"instructions": body}
        else:
            logger.warning("steering migration: skipped empty %s", path)
    if added:
        merged = {**data, "steering": {**existing, **added}}
        if "steering" in data:
            head = re.search(r"^steering:[ \t]*(#.*)?$", text, re.M)
            new_text = (
                text[: head.end()] + "\n" + _indented(added).rstrip("\n") + text[head.end() :]
                if head
                else ""
            )
        else:
            kept = text.rstrip("\n")
            new_text = (kept + "\n\n" if kept else "") + "steering:\n" + _indented(added)
        try:
            ok = yaml.safe_load(new_text) == merged
        except yaml.YAMLError:
            ok = False
        if not ok:
            if library_path.is_file():
                library_path.replace(library_path.with_name("library.yaml.pre-1.0"))
            new_text = yaml.safe_dump(merged, sort_keys=False, allow_unicode=True)
        from kraft.config import write_text  # here: kraft.config imports this module

        write_text(library_path, new_text)
    aside = src.with_name(f"{LEGACY_DIR}.pre-1.0")
    if aside.exists():
        aside = src.with_name(f"{LEGACY_DIR}.pre-1.0-{int(time.time())}")
    src.rename(aside)
    logger.warning(
        "steering files moved into library.yaml as steering profiles %s; the files are kept in %s",
        sorted(added),
        aside,
    )
    return list(added)
