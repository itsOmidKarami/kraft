"""The review bots' open threads and the base branch's "resolve conversations"
rule: read for the final review's brief, and named when a merge stops on them.
Read-only, bounded, and never raises: what cannot be read is unknown."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from kraft.adapters.forge.models import OPEN_THREADS_LINE, Forge
from kraft.automated_review import Reviewers

logger = logging.getLogger(__name__)

#: Each read, or group of reads before them, gets this long: none can hold up the item.
READ_LIMIT_S = 20
#: The most threads a stop names; the rest are counted.
_NAMED = 10
#: A refusal in the forge's own words about unresolved conversations.
_SAYS_UNRESOLVED = re.compile(
    r"\bunresolved\s+(?:review\s+)?(?:conversation|discussion|thread)s?\b"
    r"|\b(?:conversation|discussion|comment|thread)s?\s+must\s+be\s+resolved\b",
    re.IGNORECASE,
)

Rule = Literal["yes", "no", "unknown"]


@dataclass(frozen=True)
class OpenThreads:
    #: `{"reviewer", "file", "line"}` for each open bot thread; None when unread.
    threads: tuple[dict, ...] | None
    rule: Rule


async def read(forge: Forge, *, repo: Path, branch: str, base: str, bots: Reviewers) -> OpenThreads:
    """The open threads of `bots` on the branch's merge request, and the rule on `base`."""

    async def threads() -> tuple[dict, ...] | None:
        try:
            async with asyncio.timeout(READ_LIMIT_S):
                said = await forge.reviewer_feedback(
                    repo=repo, branch=branch, reviewers=bots, since=datetime.now(UTC)
                )
            return tuple(
                {"reviewer": f.reviewer.bot, "file": t.get("file"), "line": t.get("line")}
                for f in said
                for t in f.threads
            )
        except Exception as exc:  # noqa: BLE001 -- a failed read is an unknown count
            logger.warning("could not read the open bot threads on %s: %s", branch, exc)
            return None

    async def rule() -> Rule:
        try:
            async with asyncio.timeout(READ_LIMIT_S):
                required = await forge.requires_resolved_threads(repo=repo, base=base)
        except Exception as exc:  # noqa: BLE001 -- a failed read is an unknown rule
            logger.warning("could not read the conversation rule on %s: %s", base, exc)
            return "unknown"
        return "unknown" if required is None else "yes" if required else "no"

    found, required = await asyncio.gather(threads(), rule())
    return OpenThreads(found, required)


def _where(thread: dict) -> str:
    line = f":{thread['line']}" if thread.get("line") else ""
    return f"{thread.get('file') or '(no file)'}{line} ({thread['reviewer']})"


async def merge_hint(
    forge: Forge,
    *,
    repo: Path,
    branch: str,
    base: str,
    reviewers: Reviewers,
    detail: str,
    message: str = "",
) -> str:
    """What to tell a person when a merge did not go through: the open bot threads,
    when the forge's answer points at them or they are the likely reason. "" for
    any other refusal, and with no bot configured, without asking the forge."""
    bots = tuple(r for r in reviewers if r.bot is not None)
    states = set(detail.split("/"))
    says = "discussions_not_resolved" in states or bool(_SAYS_UNRESOLVED.search(message))
    # GitHub's BLOCKED covers every branch rule, so it is never more than likely.
    blocked = "BLOCKED" in states or "base branch policy prohibits" in message
    if not bots or not (says or blocked):
        return ""
    found = await read(forge, repo=repo, branch=branch, base=base, bots=bots)
    if found.rule == "no" and not says:
        return ""
    if found.threads is None:
        if not says:
            return ""
        return (
            f"{OPEN_THREADS_LINE} (unread). The forge says unresolved threads block this "
            "merge; Kraft could not read them.\n"
        )
    if not found.threads:
        return ""
    if says:
        lead = "The forge says unresolved threads block this merge."
    elif found.rule == "yes":
        lead = "Likely blocker: the base branch requires resolved conversations."
    else:
        lead = (
            "Possible blocker: Kraft could not read whether the base branch requires "
            "resolved conversations."
        )
    named = [_where(t) for t in found.threads[:_NAMED]]
    if (more := len(found.threads) - _NAMED) > 0:
        named.append(f"and {more} more")
    text = f"{OPEN_THREADS_LINE} ({len(found.threads)}). {lead} {', '.join(named)}"
    # One line whatever a file is called, so all of it stays behind the marker.
    return " ".join(text.split()) + "\n"
