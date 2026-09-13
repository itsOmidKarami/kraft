"""Parse and check the intent tree.

The tree states what Kraft is meant to do, one file per capability, each
requirement pinned to the tests that enforce it. See
docs/superpowers/specs/2026-09-10-intent-tree-design.md.
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

REQ_HEADING = re.compile(r"^##\s+REQ\s+(?P<id>\S.*?)\s*$")
VALID_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")


@dataclass(frozen=True)
class Requirement:
    id: str
    text: str
    enforced_by: tuple[str, ...]
    origin: str | None
    path: Path
    line: int


@dataclass
class Report:
    total: int = 0
    broken: list[tuple[Requirement, str]] = field(default_factory=list)
    unpinned: list[Requirement] = field(default_factory=list)
    duplicates: list[Requirement] = field(default_factory=list)
    malformed: list[Requirement] = field(default_factory=list)
    #: Frontend pins, which this checker counts but cannot resolve (Kraft-fxyz).
    #: Reported so "not verified here" never reads as "not pinned".
    unverified: list[tuple[Requirement, str]] = field(default_factory=list)

    @property
    def pinned(self) -> int:
        return self.total - len(self.unpinned)

    @property
    def ok(self) -> bool:
        return not self.broken and not self.duplicates and not self.malformed


def _build(path: Path, req_id: str, line: int, body: list[str]) -> Requirement:
    text: list[str] = []
    pins: list[str] = []
    origin: str | None = None

    for raw in body:
        stripped = raw.strip()
        if stripped.startswith("enforced-by:"):
            value = stripped.removeprefix("enforced-by:")
            pins.extend(p.strip() for p in value.split(",") if p.strip())
        elif stripped.startswith("origin:"):
            origin = stripped.removeprefix("origin:").strip() or None
        elif stripped:
            text.append(stripped)

    return Requirement(
        id=req_id,
        text=" ".join(text),
        enforced_by=tuple(pins),
        origin=origin,
        path=path,
        line=line,
    )


def parse_file(path: Path) -> list[Requirement]:
    """Read one capability file into its requirements, in document order."""
    requirements: list[Requirement] = []
    req_id: str | None = None
    start = 0
    body: list[str] = []

    for number, raw in enumerate(path.read_text().splitlines(), start=1):
        match = REQ_HEADING.match(raw)
        if match:
            if req_id is not None:
                requirements.append(_build(path, req_id, start, body))
            req_id, start, body = match.group("id"), number, []
        elif raw.startswith("## ") and req_id is not None:
            # A non-REQ heading ends the current requirement.
            requirements.append(_build(path, req_id, start, body))
            req_id, body = None, []
        elif req_id is not None:
            body.append(raw)

    if req_id is not None:
        requirements.append(_build(path, req_id, start, body))
    return requirements


def check(requirements: list[Requirement], node_ids: set[str]) -> Report:
    """Compare requirements against the test node ids that actually exist."""
    report = Report()
    seen: set[tuple[Path, str]] = set()

    for req in requirements:
        if not VALID_ID.match(req.id):
            report.malformed.append(req)
            continue

        report.total += 1
        key = (req.path, req.id)
        if key in seen:
            report.duplicates.append(req)
        seen.add(key)

        if not req.enforced_by:
            report.unpinned.append(req)
            continue
        for pin in req.enforced_by:
            if is_frontend_pin(pin):
                if "::" not in pin:
                    # The one thing still checkable about a pin nothing here can
                    # resolve. A pytest pin's typo surfaces as BROKEN because it
                    # is matched against real node ids; a frontend pin is matched
                    # against nothing, so without this `enforced-by: frontend/`
                    # would read as coverage forever.
                    report.broken.append((req, pin))
                    continue
                # Kraft-fxyz: `collect_node_ids` asks pytest, which knows nothing
                # about `frontend/src/**/*.test.tsx`, so a capability covered by a
                # vitest test used to read as UNPINNED — two of eleven `gates`
                # requirements were false gaps for exactly this reason. Counting
                # it as pinned and reporting it separately is the honest answer:
                # a pin this checker cannot resolve is not the same as no pin.
                #
                # Deliberately not resolved by shelling out to vitest. CI runs
                # `uv run python -m kraft.intent` in a Python image with no node
                # and no `frontend/node_modules` (.gitlab-ci.yml, lint-and-test),
                # so a vitest call would fail the job for every repo without a
                # frontend toolchain present. Verifying these pins belongs
                # wherever vitest already runs — `just test-ui` — not here.
                report.unverified.append((req, pin))
                continue
            if pin not in node_ids:
                report.broken.append((req, pin))

    return report


DEFAULT_TREE = Path("docs/intent")


#: A pin this checker counts but does not resolve: a vitest test, addressed the
#: same way a pytest node id is (`path::test name`) but rooted in `frontend/`.
#: Kept as a prefix test rather than a suffix one (`.test.tsx`) so a pin naming a
#: file that was renamed still reads as a frontend pin rather than silently
#: becoming a broken pytest pin.
FRONTEND_PIN_ROOT = "frontend/"


def is_frontend_pin(pin: str) -> bool:
    return pin.startswith(FRONTEND_PIN_ROOT)


def parse_collect_output(text: str) -> set[str]:
    """Pull node ids out of `pytest --collect-only -q` stdout."""
    return {line.strip() for line in text.splitlines() if "::" in line}


def collect_node_ids(root: Path) -> set[str]:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    node_ids = parse_collect_output(result.stdout)
    # A collection error means we cannot tell a broken pin from a broken suite.
    # Say so loudly rather than reporting every pin as broken.
    if not node_ids:
        raise RuntimeError(
            f"pytest collected nothing under {root}:\n{result.stdout}{result.stderr}"
        )
    return node_ids


def render(report: Report) -> str:
    lines: list[str] = []

    for req in report.malformed:
        lines.append(f"MALFORMED {req.path}:{req.line}  REQ {req.id}")
    for req in report.duplicates:
        lines.append(f"DUPLICATE {req.path}:{req.line}  REQ {req.id}")
    for req, pin in report.broken:
        lines.append(f"BROKEN    {req.path}:{req.line}  REQ {req.id} -> {pin}")
    for req in report.unpinned:
        lines.append(f"UNPINNED  {req.path}:{req.line}  REQ {req.id}")
    for req, pin in report.unverified:
        lines.append(f"FRONTEND  {req.path}:{req.line}  REQ {req.id} -> {pin} (not checked here)")

    summary = (
        f"intent: {report.total} requirements, {report.pinned} pinned, "
        f"{len(report.unpinned)} unpinned, {len(report.broken)} broken"
    )
    if report.unverified:
        summary += f", {len(report.unverified)} frontend pins not checked here"
    lines.append(summary)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    tree = Path(args[0]) if args else DEFAULT_TREE

    files = sorted(tree.glob("*.md")) if tree.is_dir() else []
    if not files:
        print(f"intent: no intent tree at {tree}, nothing to check.")
        return 0

    requirements = [req for path in files for req in parse_file(path)]
    report = check(requirements, collect_node_ids(Path.cwd()))
    print(render(report))
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
