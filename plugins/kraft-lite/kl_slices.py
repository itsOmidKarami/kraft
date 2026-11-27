"""A plan's `## Slices` section, read the way Kraft reads it, plus Lite's one
extension. Stdlib only, like `kl.py`.

The grammar is Kraft's own (`kraft.slices`), kept in step by
`tests/plugins/test_kraft_lite_parity.py`:

    ## Slices

    - **Slice 1: Parser.** Tasks 2-3.
    - **Slice 2: CLI verb.** Tasks 4, 6.

Task numbers name the plan's `## Task N` / `### Task N` headings. A task in no
slice is groundwork: Lite builds it as a first slice, `groundwork`, that every
slice with no `after` of its own comes after.

Lite's extension is a last sentence `Comes after slice 1.` (or `slices 1, 3.`).
Kraft refuses a bullet that says "comes after", so a plan using it runs
unsliced there rather than misread.
"""

from __future__ import annotations

import re

_HEADING = re.compile(r"^#{1,6}[ \t]+\S")
_SLICES_HEADING = re.compile(r"^##[ \t]+Slices[ \t]*$", re.IGNORECASE)
_TASK_HEADING = re.compile(
    r"^#{2,3}[ \t]+Task[ \t]+\d+\b[ \t]*[—:.\-]?[ \t]*(.*)$", re.MULTILINE | re.IGNORECASE
)
_FENCE = re.compile(r"^[ \t]*(`{3,}|~{3,})")
_BULLET = re.compile(r"^[ \t]*[-*][ \t]+(.*)$")
_SLICE_BOLD = re.compile(r"^\*\*Slice[ \t]+(\d+)[ \t]*(?::[ \t]*(.*?))?\.?[ \t]*\*\*(.*)$")
_COMES_AFTER = re.compile(r"\b(?:comes?\s+after|after\s+slice|depends\s+on|needs\s+slice)\b", re.I)
#: Lite's `after`, the bullet's last sentence. Any other dependency wording is refused.
_LITE_AFTER = re.compile(
    r"\bComes[ \t]+after[ \t]+slices?[ \t]+(\d+(?:[ \t]*(?:,|and)[ \t]*\d+)*)[ \t]*\.?[ \t]*$",
    re.I,
)
_TASK_ITEM = re.compile(r"(\d+)(?:[ \t]*[-–][ \t]*(\d+))?")
_DONE_TAG = "[DONE]"
GROUNDWORK = "groundwork"


def _unfenced_lines(text: str) -> list[str]:
    """`text`'s lines with every fenced code block (and its fences) blanked."""
    in_fence = False
    lines = []
    for line in text.split("\n"):
        if _FENCE.match(line):
            in_fence = not in_fence
            lines.append("")
        else:
            lines.append("" if in_fence else line)
    return lines


def parse_tasks(text: str) -> list[tuple[str, bool]]:
    """The `(title, done)` of each task heading, in order; `done` when it ends `[DONE]`."""
    titles = [m[1].strip() for m in _TASK_HEADING.finditer("\n".join(_unfenced_lines(text)))]
    return [
        (t[: -len(_DONE_TAG)].rstrip(), True) if t.endswith(_DONE_TAG) else (t, False)
        for t in titles
    ]


def _section(text: str) -> list[str] | None:
    lines = _unfenced_lines(text)
    for i, line in enumerate(lines):
        if _SLICES_HEADING.match(line):
            end = next((j for j in range(i + 1, len(lines)) if _HEADING.match(lines[j])), None)
            return lines[i + 1 : end]
    return None


def _task_numbers(rest: str) -> list[int] | str:
    numbers: list[int] = []
    for m in _TASK_ITEM.finditer(rest):
        low = int(m[1])
        high = int(m[2]) if m[2] else low
        if high < low:
            return f"the range {m[0].strip()} runs backwards"
        numbers.extend(range(low, high + 1))
    return numbers


def parse_slices(plan_text: str) -> dict | str | None:
    """`{"slices": [{"key", "title", "tasks", "after"}], "prelude": [N]}` with
    Kraft's integer keys, a reason the section is refused, or None with no
    section. The reasons are Kraft's own words, but for a refused `after`."""
    body = _section(plan_text)
    if body is None:
        return None
    tasks = parse_tasks(plan_text)
    parsed: list[dict] = []
    for line in body:
        bullet = _BULLET.match(line)
        if bullet is None:
            continue
        m = _SLICE_BOLD.match(bullet[1].strip())
        if m is None:
            return f"not a slice bullet: {bullet[1].strip()[:60]!r}"
        key, title, rest = int(m[1]), (m[2] or "").strip().rstrip(".").strip(), m[3]
        if not title:
            return f"slice {key} has no title"
        after: list[int] = []
        lite = _LITE_AFTER.search(rest)
        if lite:
            after = [int(n) for n in re.findall(r"\d+", lite[1])]
            rest = rest[: lite.start()]
        if _COMES_AFTER.search(rest):
            return f"slice {key} names what it comes after other than as `Comes after slice N.`"
        numbers = _task_numbers(rest)
        if isinstance(numbers, str):
            return f"slice {key}: {numbers}"
        if not numbers:
            return f"slice {key} is empty: it names no task"
        parsed.append({"key": key, "title": title, "tasks": numbers, "after": after})
    if [s["key"] for s in parsed] != list(range(1, len(parsed) + 1)):
        return "slice keys must be 1..n, consecutive and unique"
    if len(parsed) < 2:
        return "a split needs at least two slices"
    owner: dict[int, int] = {}
    for spec in parsed:
        for n in spec["tasks"]:
            if not 1 <= n <= len(tasks):
                return f"slice {spec['key']} names task {n}, which the plan does not have"
            if tasks[n - 1][1]:
                return f"slice {spec['key']} names task {n}, which is tagged {_DONE_TAG}"
            if n in owner:
                return f"task {n} is in two slices ({owner[n]} and {spec['key']})"
            owner[n] = spec["key"]
        for a in spec["after"]:
            if a == spec["key"] or not 1 <= a <= len(parsed):
                return f"slice {spec['key']} comes after slice {a}, which is not another slice"
        spec["tasks"] = sorted(set(spec["tasks"]))
    prelude = [n for n in range(1, len(tasks) + 1) if n not in owner and not tasks[n - 1][1]]
    return {"slices": parsed, "prelude": prelude}


def proposal_slices(split: dict) -> list[dict]:
    """`split`'s slices as a proposal's `slices` list: string keys, and the
    prelude as a first `groundwork` slice that every slice with no `after`
    comes after. `kl.py approve --slices` checks the rest of the graph."""
    out = [
        {
            "key": str(s["key"]),
            "title": s["title"],
            "tasks": s["tasks"],
            "after": [str(a) for a in s["after"]] or ([GROUNDWORK] if split["prelude"] else []),
        }
        for s in split["slices"]
    ]
    if split["prelude"]:
        out.insert(
            0, {"key": GROUNDWORK, "title": "Groundwork", "tasks": split["prelude"], "after": []}
        )
    return out


def coverage_refusal(plan_text: str, slices: list[dict]) -> str | None:
    """Why `slices` does not hold every task of the plan exactly once, or None.
    A task twice is `kl.py`'s own check; this is the rest."""
    tasks = parse_tasks(plan_text)
    named: set[int] = set()
    for spec in slices:
        for n in spec.get("tasks") or []:
            if not 1 <= n <= len(tasks):
                return (
                    f"slice {spec['key']!r} names task {n}, which the plan does not have "
                    f"(its tasks are its {len(tasks)} `### Task N` headings)"
                )
            if tasks[n - 1][1]:
                return f"slice {spec['key']!r} names task {n}, which is tagged {_DONE_TAG}"
            named.add(n)
    missing = [n for n in range(1, len(tasks) + 1) if n not in named and not tasks[n - 1][1]]
    if missing:
        listed = ", ".join(str(n) for n in missing)
        return (
            f"task{'s' if len(missing) > 1 else ''} {listed} in no slice: add "
            f"{'them' if len(missing) > 1 else 'it'} to one, or to a `{GROUNDWORK}` slice "
            "the others come after"
        )
    return None
