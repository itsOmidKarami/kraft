"""A plan's `## Slices` section: how a plan splits into child work items.

One parser, used everywhere a plan is read for its split (intake, the plan
gate, chain revision, the fork). The grammar:

    ## Slices

    - **Slice 1: Parser.** Tasks 1-3.
    - **Slice 2: CLI verb.** Tasks 4, 6.

Task numbers name the plan's `### Task N:` headings (`progress.parse_tasks`).
A task in no slice is shared groundwork, the *prelude*. A section that is
present but refused means no split, and says why; no section is `None`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from kraft.progress import parse_tasks, unfenced_lines

_HEADING = re.compile(r"^#{1,6}[ \t]+\S")
_SLICES_HEADING = re.compile(r"^##[ \t]+Slices[ \t]*$", re.IGNORECASE)
_TASK_LINE = re.compile(r"^#{2,3}[ \t]+Task[ \t]+\d+\b", re.IGNORECASE)
_BULLET = re.compile(r"^[ \t]*[-*][ \t]+(.*)$")
_SLICE_BOLD = re.compile(r"^\*\*Slice[ \t]+(\d+)[ \t]*(?::[ \t]*(.*?))?\.?[ \t]*\*\*(.*)$")
_COMES_AFTER = re.compile(r"\b(?:comes?\s+after|after\s+slice|depends\s+on|needs\s+slice)\b", re.I)
_TASK_ITEM = re.compile(r"(\d+)(?:[ \t]*[-–][ \t]*(\d+))?")
_DONE_TAG = "[DONE]"


@dataclass(frozen=True)
class Slice:
    key: int
    title: str
    tasks: tuple[int, ...]


@dataclass(frozen=True)
class SliceSplit:
    slices: tuple[Slice, ...]
    #: Tasks in no slice: shared groundwork, built in the parent before the fork.
    prelude: tuple[int, ...]


@dataclass(frozen=True)
class SliceRefusal:
    reason: str


def _section(text: str) -> list[str] | None:
    """The lines under a `## Slices` heading (outside a code fence), up to the
    next heading of any level; None when there is no such heading."""
    lines = unfenced_lines(text)
    for i, line in enumerate(lines):
        if _SLICES_HEADING.match(line):
            end = next((j for j in range(i + 1, len(lines)) if _HEADING.match(lines[j])), None)
            return lines[i + 1 : end]
    return None


def _task_numbers(rest: str) -> list[int] | str:
    """The task numbers a bullet's tail names, or a reason it is unreadable."""
    numbers: list[int] = []
    for m in _TASK_ITEM.finditer(rest):
        low = int(m[1])
        high = int(m[2]) if m[2] else low
        if high < low:
            return f"the range {m[0].strip()} runs backwards"
        numbers.extend(range(low, high + 1))
    return numbers


def parse_slices(plan_text: str, *, max_slices: int) -> SliceSplit | SliceRefusal | None:
    """The split a plan declares, a refusal saying why it cannot be used, or
    None when it has no `## Slices` section."""
    body = _section(plan_text)
    if body is None:
        return None
    tasks = parse_tasks(plan_text)
    parsed: list[tuple[int, str, list[int]]] = []
    for line in body:
        bullet = _BULLET.match(line)
        if bullet is None:
            continue
        m = _SLICE_BOLD.match(bullet[1].strip())
        if m is None:
            return SliceRefusal(f"not a slice bullet: {bullet[1].strip()[:60]!r}")
        key, title, rest = int(m[1]), (m[2] or "").strip().rstrip(".").strip(), m[3]
        if not title:
            return SliceRefusal(f"slice {key} has no title")
        if _COMES_AFTER.search(rest):
            return SliceRefusal(f"slice {key} comes after another; slices run in parallel")
        numbers = _task_numbers(rest)
        if isinstance(numbers, str):
            return SliceRefusal(f"slice {key}: {numbers}")
        if not numbers:
            return SliceRefusal(f"slice {key} is empty: it names no task")
        parsed.append((key, title, numbers))
    if [k for k, _, _ in parsed] != list(range(1, len(parsed) + 1)):
        return SliceRefusal("slice keys must be 1..n, consecutive and unique")
    if len(parsed) < 2:
        return SliceRefusal("a split needs at least two slices")
    if len(parsed) > max_slices:
        return SliceRefusal(f"{len(parsed)} slices is more than the limit of {max_slices}")
    owner: dict[int, int] = {}
    for key, _, numbers in parsed:
        for n in numbers:
            if not 1 <= n <= len(tasks):
                return SliceRefusal(f"slice {key} names task {n}, which the plan does not have")
            if tasks[n - 1][1]:
                return SliceRefusal(f"slice {key} names task {n}, which is tagged {_DONE_TAG}")
            if n in owner:
                return SliceRefusal(f"task {n} is in two slices ({owner[n]} and {key})")
            owner[n] = key
    return SliceSplit(
        slices=tuple(Slice(k, t, tuple(sorted(set(ns)))) for k, t, ns in parsed),
        prelude=tuple(
            n for n in range(1, len(tasks) + 1) if n not in owner and not tasks[n - 1][1]
        ),
    )


def plan_for_slice(plan_text: str, keep: frozenset[int]) -> str:
    """The plan one slice's child reads: every task not in `keep` tagged
    `[DONE]` (already built, or built by another slice), and the `## Slices`
    section removed. Task numbers stay the plan's own."""
    lines = plan_text.split("\n")
    blanked = unfenced_lines(plan_text)
    drop: set[int] = set()
    for i, line in enumerate(blanked):
        if _SLICES_HEADING.match(line):
            end = next(
                (j for j in range(i + 1, len(lines)) if _HEADING.match(blanked[j])), len(lines)
            )
            drop.update(range(i, end))
    out: list[str] = []
    task_no = 0
    for i, line in enumerate(lines):
        if i in drop:
            continue
        if _TASK_LINE.match(blanked[i]):
            task_no += 1
            if task_no not in keep and not line.rstrip().endswith(_DONE_TAG):
                line = f"{line.rstrip()} {_DONE_TAG}"
        out.append(line)
    return "\n".join(out)


def prelude_note(split: SliceSplit) -> str:
    """The implementer's note for the prelude run in the parent, whose approved
    plan file is never rewritten."""
    numbers = ", ".join(str(n) for n in split.prelude)
    return (
        f"Build only the shared groundwork: task {numbers}. Do not build any other task. "
        "The rest of the plan is built by the slices that run after you, each in its own "
        "worktree, from the code you leave."
    )
