"""Per-repo and per-hook standards, injected through the system prompt.

The context-injection boundary (`00_overview.md` glossary) bans `CLAUDE.md`,
`AGENTS.md` and any repo file as a context channel. That rule governs the
*channel*, not the existence of standards — this is the other half: authored,
Kraft-owned files under `$KRAFT_HOME/templates/steering/`, reaching the agent
through the per-invocation system prompt the adapter already builds. Kraft never
reads or writes a steering file inside a target repository, which is why a name
containing a path separator is rejected outright.

`validate` and `read` are separate because the text must never be written back
into `registry.yaml` or `repos.yaml`: those dicts round-trip through
`GET /registry` and the Settings screens, so resolving into them would inline the
bodies into the operator's config and delete the names.
"""

from __future__ import annotations

from pathlib import Path

#: Total injected budget, counting the header and separators, not just bodies.
#: An oversized system prompt degrades every launch and costs money on each.
MAX_BYTES = 8192

#: What `adapters/agent.py` wraps the bodies in. Defined here so the budget and
#: the injection cannot drift apart; Task 5 asserts the two stay equal.
HEADING = "\n\n## Project standards\n\n"
_OVERHEAD = len(HEADING.encode())

#: Separator `adapters/agent.py` joins bodies with (`"\n\n".join(...)`). Bodies
#: contribute N-1 of these to the assembled block, which `_OVERHEAD` alone
#: does not count — `assembled_bytes` is what makes the MAX_BYTES comment above
#: (header *and separators*) true rather than aspirational.
_SEP = "\n\n"


class SteeringError(Exception):
    pass


def _path(steering_dir: Path, name: str, where: str) -> Path:
    if "/" in name or "\\" in name or name in ("", ".", "..") or name.startswith("."):
        raise SteeringError(
            f"{where}: steering name {name!r} must be a bare file name — Kraft "
            "never reads a steering file from outside its own templates directory"
        )
    return Path(steering_dir) / f"{name}.md"


def assembled_bytes(bodies: list[str] | tuple[str, ...]) -> int:
    """Bytes of the actual injected block for these bodies: `HEADING` plus the
    bodies joined by `_SEP` — exactly what `adapters/agent.py` builds. Used both
    by `validate` (one config file's own names) and by `resolve_invocation`
    (the repo names and hook names it combines), so a union that blows the
    shared budget is caught wherever it is actually assembled.
    """
    if not bodies:
        return 0
    return _OVERHEAD + len(_SEP.encode()) * (len(bodies) - 1) + sum(len(b.encode()) for b in bodies)


def validate(steering_dir: Path, names: list[str], *, where: str) -> None:
    """Every name resolves and the total fits the budget, or raise.

    Runs at config load, against the *real* steering directory — not against
    whatever directory a candidate file was written into for validation.
    """
    bodies: list[str] = []
    for name in names:
        path = _path(steering_dir, name, where)
        if not path.is_file():
            raise SteeringError(f"{where}: steering {name!r} not found at {path}")
        try:
            bodies.append(path.read_text())
        except (OSError, ValueError) as exc:
            raise SteeringError(f"{where}: cannot read steering {name!r} at {path}: {exc}") from exc
    total = assembled_bytes(bodies)
    if names and total > MAX_BYTES:
        raise SteeringError(
            f"{where}: steering totals {total} bytes, over the {MAX_BYTES} byte budget"
        )


def read(steering_dir: Path, names: list[str]) -> tuple[str, ...]:
    """Bodies for `names`, in order. Called at dispatch, after `validate`.

    Assumes `validate` already passed for these names — it does no checking of
    its own. A file deleted, chmod'd or corrupted between validation (config
    load) and this call (dispatch) is reported as a `SteeringError` naming the
    file, so a caller catching `SteeringError` around dispatch sees it as the
    steering problem it is rather than as a bare `FileNotFoundError` (Kraft-fza).

    The budget is a different story: this function does not measure it, but
    `resolve_invocation` re-checks the total over the concatenation it
    assembles, on every dispatch. A file that grew past the budget after
    `validate` ran is therefore still caught — at launch rather than at save.
    """
    bodies: list[str] = []
    for name in names:
        path = _path(steering_dir, name, "steering")
        try:
            bodies.append(path.read_text())
        except (OSError, ValueError) as exc:
            raise SteeringError(f"steering: cannot read {name!r} at {path}: {exc}") from exc
    return tuple(bodies)
