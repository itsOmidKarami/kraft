"""The body of the twin proof both parity files run: a CLI backend and its API backend
answer one `Row` alike. A plain module, so no `test_` function is collected twice."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from kraft.adapters import forge
from kraft.adapters.forge.models import Forge
from kraft.automated_review import AutomatedReview

BR = "kraft/abc"
SINCE = datetime(2026, 1, 1, tzinfo=UTC)


def review(**kw):
    return lambda f, r: f.automated_review(repo=r, branch=BR, reviewers=(AutomatedReview(**kw),))


def feedback(**kw):
    return lambda f, r: f.reviewer_feedback(
        repo=r, branch=BR, reviewers=(AutomatedReview(**kw),), since=SINCE
    )


@dataclass
class Row:
    method: str
    call: Any  # (forge, repo) -> awaitable
    routes: dict = field(default_factory=dict)
    git: dict = field(default_factory=dict)


async def outcome(call, f, repo):
    """The result, or what an error says about itself (not its text: the CLI and HTTP word it
    alike only where both read the same body)."""
    try:
        return await call(f, repo)
    except forge.ForgeError as exc:
        return (type(exc), exc.status, exc.reason)


async def assert_alike(cli, tmp_path, row: Row, api: SimpleNamespace) -> None:
    """`row` through `api.twin` and `api`, against one stub: the same result, and the same
    requests in the stub's call log."""
    name = api.twin.name
    cli.stub(name, routes=row.routes, default="{}")
    cli.stub("git", routes={**row.git, **api.git_routes}, default="")

    twin = await outcome(row.call, api.twin.cls(), tmp_path)
    twin_calls = cli.calls(name)
    for ext in ("calls", "argv"):
        (cli.bindir / f"{name}.{ext}").unlink(missing_ok=True)
    got = await outcome(row.call, api.cls(), tmp_path)

    assert twin_calls, f"the twin never reached {name}"
    assert got == twin
    assert cli.calls(name) == twin_calls


def methods_without_a_row(rows: dict[str, Row]) -> set[str]:
    """`Forge` methods (but `push`) that no row covers."""
    return (
        {m for m in vars(Forge) if not m.startswith("_")}
        - {"push"}
        - {r.method for r in rows.values()}
    )
