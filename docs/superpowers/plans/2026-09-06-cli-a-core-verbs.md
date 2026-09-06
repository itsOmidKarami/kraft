# Kraft CLI Sub-project A — Core Verbs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give `kraft` eight human-usable subcommands over the nine operations `src/kraft/client.py` already has, with human tables by default and `--json` that matches the MCP door byte-for-byte.

**Architecture:** `cli.py` becomes an argparse dispatch table; every handler calls one `client.py` coroutine and hands the result to a renderer in a new `render.py`. No new API endpoints, no new client operations, no business logic in `cli.py`. Two additions to `client.py`: `resolve_repo()` (cwd → connected repo) and one connect-error message both front doors share.

**Tech Stack:** Python 3.14, stdlib `argparse`/`asyncio`/`json`/`shutil`, `httpx` (already a dependency), pytest.

**Spec:** `docs/superpowers/specs/2026-09-06-cli-a-core-verbs-design.md`

**Beads issue:** `Kraft-bbg.1` (epic `Kraft-bbg`). Claim it before Task 1: `bd update Kraft-bbg.1 --claim`.

## Global Constraints

- Python `>=3.14`. No new runtime dependencies — argparse, json, shutil, asyncio are stdlib.
- ruff, `line-length = 100`. `just lint` must pass before every commit.
- `just test` is the gate. Run the whole suite before the final commit of each task; a single test file is enough between steps.
- **Bare `kraft` must keep seeding-and-serving.** The argv-empty check happens before argparse ever sees anything. There is a regression test for this in Task 3 and it must not be deleted.
- `--json` output is `json.dumps` of exactly what `client.py` returned. The CLI never reshapes a payload. This is the anti-drift rule between the CLI and MCP doors.
- Errors print to **stderr** as `kraft: <message>`; stdout stays clean so `kraft list --json | jq` is safe under failure. Exit codes: `0` ok, `1` operation failed, `2` usage error.
- `client.resolve_context()` is **not** modified. The self-action guard depends on its exact semantics.
- No branch-based context resolution. No interactive prompts anywhere.
- Commits stay local. This repo's agent profile is conservative: do not push, do not merge to `main`, do not run `bd dolt push` unless the human asks.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/kraft/render.py` *(create)* | Terminal formatting only: tables, key/value blocks, relative times, colour. Knows nothing about work items or HTTP. |
| `src/kraft/cli.py` *(modify)* | argparse dispatch. One thin handler per verb: parse → call `client` → render. |
| `src/kraft/client.py` *(modify)* | Adds `resolve_repo()` and routes all HTTP through one `_send()` that turns a dead server into a sentence. |
| `tests/test_render.py` *(create)* | `render.py` in isolation — no app, no fixtures. |
| `tests/conftest.py` *(modify)* | The `app` fixture: `client.http()` on the ASGI app with the lifespan entered. Shared by every `test_cli_*.py` in A–E. |
| `tests/test_cli_verbs.py` *(create)* | Every verb through `cli.main([...])` with `client.http()` on the ASGI app, asserting captured stdout. |
| `tests/test_client_repo_context.py` *(create)* | `resolve_repo()` precedence and the connect-error message. |
| `tests/test_cli.py` *(modify)* | Keeps seeding tests; gains the bare-`kraft`-still-serves regression test. |

---

### Task 1: `render.py` — terminal formatting

**Files:**
- Create: `src/kraft/render.py`
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `use_color(stream=sys.stdout) -> bool`
  - `paint(text: str, code: str) -> str`
  - `STATUS_COLORS: dict[str, str]`
  - `relative_time(iso: str | None, now: datetime | None = None) -> str`
  - `table(rows: list[dict], columns: list[tuple[str, str]], width: int | None = None) -> str` — `columns` is `(header, key)` pairs; the last column absorbs the remaining width and truncates with `…`.
  - `kv(pairs: list[tuple[str, str]]) -> str`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_render.py
"""render.py is pure formatting: no app, no HTTP, no fixtures."""

from __future__ import annotations

import io
from datetime import UTC, datetime

from kraft import render


def test_table_aligns_columns_and_prints_a_header():
    rows = [
        {"id": "Kraft-a", "title": "short"},
        {"id": "Kraft-bbbb", "title": "longer title"},
    ]
    out = render.table(rows, [("ID", "id"), ("TITLE", "title")], width=80).splitlines()
    assert out[0].split() == ["ID", "TITLE"]
    # every row starts its second column at the same offset
    assert out[1].index("short") == out[2].index("longer title")


def test_table_truncates_the_last_column_rather_than_wrapping():
    rows = [{"id": "Kraft-a", "title": "x" * 200}]
    out = render.table(rows, [("ID", "id"), ("TITLE", "title")], width=40)
    assert len(out.splitlines()) == 2  # header + one row, never wrapped
    assert out.splitlines()[1].endswith("…")
    assert len(out.splitlines()[1]) <= 40


def test_table_says_so_when_there_is_nothing():
    assert render.table([], [("ID", "id")], width=80) == "(nothing)"


def test_table_renders_a_missing_key_as_a_dash():
    out = render.table([{"id": "Kraft-a"}], [("ID", "id"), ("GATE", "pending_gate")], width=80)
    assert out.splitlines()[1].split() == ["Kraft-a", "-"]


def test_relative_time_is_compact():
    now = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)
    assert render.relative_time("2026-09-06T11:56:00+00:00", now) == "4m ago"
    assert render.relative_time("2026-09-06T11:59:30+00:00", now) == "just now"
    assert render.relative_time("2026-09-04T12:00:00+00:00", now) == "2d ago"
    # unparseable or absent is not a crash: this renders a board, not a report
    assert render.relative_time(None, now) == "-"
    assert render.relative_time("not a date", now) == "-"


def test_color_is_off_when_the_stream_is_not_a_tty():
    assert render.use_color(io.StringIO()) is False


def test_color_is_off_when_no_color_is_set(monkeypatch):
    class Tty(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.delenv("NO_COLOR", raising=False)
    assert render.use_color(Tty()) is True
    monkeypatch.setenv("NO_COLOR", "1")
    assert render.use_color(Tty()) is False


def test_kv_aligns_labels():
    out = render.kv([("id", "Kraft-a"), ("status", "active")]).splitlines()
    assert out[0].index("Kraft-a") == out[1].index("active")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `just test tests/test_render.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'kraft.render'`

- [ ] **Step 3: Write the implementation**

```python
# src/kraft/render.py
"""Terminal formatting for the `kraft` CLI.

Pure presentation: nothing here knows what a work item is, and nothing here
makes a request. `cli.py` chooses a renderer per verb and never formats inline,
so the later CLI sub-projects (a log line, a diff stat block) add functions here
rather than growing the dispatch table.
"""

from __future__ import annotations

import os
import shutil
import sys
from datetime import UTC, datetime
from typing import TextIO

RESET = "\033[0m"
DIM = "\033[2m"

#: Status -> ANSI colour. Anything unlisted renders unpainted.
STATUS_COLORS = {
    "active": "\033[32m",
    "needs_human": "\033[33m",
    "paused": DIM,
    "failed": "\033[31m",
}


def use_color(stream: TextIO | None = None) -> bool:
    """Colour only into a terminal, and never when NO_COLOR is set.

    A piped `kraft list` is consumed by something that wants text, not escapes.
    """
    stream = stream or sys.stdout
    if os.environ.get("NO_COLOR"):
        return False
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError):
        return False


def paint(text: str, code: str, stream: TextIO | None = None) -> str:
    return f"{code}{text}{RESET}" if code and use_color(stream) else text


def relative_time(iso: str | None, now: datetime | None = None) -> str:
    """"4m ago". A board is scanned, not read; an ISO timestamp is neither.

    Never raises: an unparseable or missing timestamp renders "-" rather than
    taking down the whole table over one bad row.
    """
    if not iso:
        return "-"
    try:
        when = datetime.fromisoformat(iso)
    except ValueError:
        return "-"
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    seconds = ((now or datetime.now(UTC)) - when).total_seconds()
    if seconds < 60:
        return "just now"
    for size, suffix in ((86400, "d"), (3600, "h"), (60, "m")):
        if seconds >= size:
            return f"{int(seconds // size)}{suffix} ago"
    return "just now"


def table(rows: list[dict], columns: list[tuple[str, str]], width: int | None = None) -> str:
    """Aligned columns, no borders. The last column truncates rather than wraps.

    A wrapped table stops being scannable, which is the only reason to render a
    table instead of `--json`.
    """
    if not rows:
        return "(nothing)"
    width = width or shutil.get_terminal_size((100, 24)).columns
    cells = [[_cell(row.get(key)) for _header, key in columns] for row in rows]
    headers = [header for header, _key in columns]
    widths = [
        max(len(headers[i]), *(len(row[i]) for row in cells)) for i in range(len(columns))
    ]
    # the last column gets whatever is left, and is cut to it
    last = max(8, width - sum(widths[:-1]) - 2 * (len(columns) - 1))
    widths[-1] = min(widths[-1], last)

    def line(values: list[str]) -> str:
        parts = [
            _fit(value, widths[i]) if i == len(values) - 1 else value.ljust(widths[i])
            for i, value in enumerate(values)
        ]
        return "  ".join(parts).rstrip()

    return "\n".join([paint(line(headers), DIM), *(line(row) for row in cells)])


def kv(pairs: list[tuple[str, str]]) -> str:
    """A detail block: labels right-padded to a common width."""
    label_width = max((len(label) for label, _value in pairs), default=0)
    return "\n".join(f"{label.ljust(label_width)}  {value}" for label, value in pairs)


def _cell(value: object) -> str:
    """None and "" are the same absence to a reader, and both read as "-"."""
    return "-" if value in (None, "") else str(value)


def _fit(value: str, width: int) -> str:
    return value if len(value) <= width else value[: width - 1] + "…"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `just test tests/test_render.py`
Expected: PASS (8 tests)

- [ ] **Step 5: Lint and commit**

```bash
just lint
git add src/kraft/render.py tests/test_render.py
git commit -m "feat(cli): render.py — tables, kv blocks, relative times, colour"
```

---

### Task 2: One connect-error sentence for both front doors

**Files:**
- Modify: `src/kraft/client.py` (add `_send`; route `_get` and `_post` through it)
- Test: `tests/test_client_repo_context.py`

**Interfaces:**
- Consumes: `client.base_url()`, `client.http()` (both exist).
- Produces: `client._send(method: str, path: str, **kwargs) -> httpx.Response` — raises `ValueError` with the "no Kraft server" sentence on `httpx.ConnectError`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_client_repo_context.py
"""resolve_repo() and the dead-server message — the two things A adds to client.py."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from kraft import client


def test_a_dead_server_is_a_sentence_not_a_traceback(monkeypatch, tmp_path):
    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "access.yaml").write_text("bind: 127.0.0.1\nport: 8765\n")
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(templates))
    monkeypatch.delenv("KRAFT_HOST", raising=False)
    monkeypatch.delenv("KRAFT_PORT", raising=False)

    def refuse(*_args, **_kwargs):
        raise httpx.ConnectError("nope")

    monkeypatch.setattr(httpx.AsyncClient, "request", refuse)

    with pytest.raises(ValueError) as caught:
        asyncio.run(client.list_work_items())
    # the address is in the message: KRAFT_PORT means it is not always 8765
    assert "no Kraft server at http://127.0.0.1:8765" in str(caught.value)
    assert "kraft serve" in str(caught.value)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `just test tests/test_client_repo_context.py`
Expected: FAIL — `httpx.ConnectError: nope` escapes instead of a `ValueError`

- [ ] **Step 3: Write the implementation**

Add `_send` to `src/kraft/client.py` directly above `_get`, and rewrite `_get` and `_post` to use it. Everything else in the file is untouched.

```python
async def _send(method: str, path: str, **kwargs) -> httpx.Response:
    """Every request goes through here, so a dead server reads the same at both
    front doors — an agent calling through MCP gets this sentence too, not a
    traceback it will try to reason about."""
    try:
        async with http() as session:
            return await session.request(method, path, **kwargs)
    except httpx.ConnectError as exc:
        raise ValueError(
            f"no Kraft server at {base_url()} — start one with `kraft serve`"
        ) from exc


async def _get(path: str, **params) -> dict | list:
    response = await _send(
        "GET", path, params={k: v for k, v in params.items() if v is not None}
    )
    if response.status_code >= 400:
        # An agent reads this string. "404: work item not found" is actionable;
        # an httpx traceback is not.
        raise ValueError(f"kraft {response.status_code}: {_detail(response)}")
    return response.json()
```

```python
async def _post(path: str, payload: dict | None = None) -> tuple[int, dict]:
    """Status alongside the body: some callers treat a 4xx as a normal outcome
    (a 409 from POST /repos means the repo is already connected)."""
    response = await _send("POST", path, json=payload or {})
    try:
        body = response.json()
    except ValueError:
        body = {"detail": response.text}
    return response.status_code, body
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `just test tests/test_client_repo_context.py tests/test_client_read.py tests/test_client_act.py tests/test_client_write.py tests/test_client_auth.py tests/test_mcp.py`
Expected: PASS — the existing client tests still pass because `_send` only changes how the request is issued, not what is sent.

- [ ] **Step 5: Lint and commit**

```bash
just lint
git add src/kraft/client.py tests/test_client_repo_context.py
git commit -m "feat(cli): one readable message when no Kraft server is listening"
```

---

### Task 3: `resolve_repo()` — cwd to connected repo

**Files:**
- Modify: `src/kraft/client.py` (add `resolve_repo` below `resolve_context`)
- Test: `tests/test_client_repo_context.py`

**Interfaces:**
- Consumes: `client._get`, `config.git_read`.
- Produces: `async def resolve_repo(cwd: Path | None = None) -> str | None` — the connected repo path containing `cwd`, or `None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_client_repo_context.py`:

```python
import httpx as _httpx  # noqa: E402  (kept beside the fixture that uses it)
import os
from pathlib import Path

from support.harness import fake_templates_dir, isolated_bd, make_repo

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FAKE_CLAUDE = _REPO_ROOT / "fixtures" / "fake-claude.sh"


@pytest.fixture
def wired(tmp_path, monkeypatch):
    """The app, with client.http() pointed at it in-process (as test_client_read)."""
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("KRAFT_BD_CWD", str(isolated_bd(tmp_path)))
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(fake_templates_dir(tmp_path, str(_FAKE_CLAUDE))))
    monkeypatch.setenv(
        "KRAFT_FRONTEND_DIST", os.environ.get("KRAFT_FRONTEND_DIST") or str(tmp_path / "no-dist")
    )
    monkeypatch.delenv("KRAFT_WORK_ITEM_ID", raising=False)
    import kraft.api as api

    monkeypatch.setattr(
        client,
        "http",
        lambda: _httpx.AsyncClient(
            transport=_httpx.ASGITransport(app=api.app), base_url="http://kraft"
        ),
    )
    return api


def run_with_app(api, scenario):
    async def wrapper():
        async with api.app.router.lifespan_context(api.app):
            return await scenario()

    return asyncio.run(wrapper())


def test_resolve_repo_finds_the_connected_repo_containing_the_cwd(wired, tmp_path):
    repo = make_repo(tmp_path)

    async def scenario():
        await client.ensure_repo(str(repo))
        return await client.resolve_repo(repo)

    assert run_with_app(wired, scenario) == str(repo)


def test_resolve_repo_walks_up_from_a_subdirectory(wired, tmp_path):
    repo = make_repo(tmp_path)
    deep = repo / "src" / "nested"
    deep.mkdir(parents=True)

    async def scenario():
        await client.ensure_repo(str(repo))
        return await client.resolve_repo(deep)

    assert run_with_app(wired, scenario) == str(repo)


def test_resolve_repo_is_none_for_an_unconnected_repo(wired, tmp_path):
    repo = make_repo(tmp_path, name="stranger")

    async def scenario():
        return await client.resolve_repo(repo)

    assert run_with_app(wired, scenario) is None


def test_resolve_repo_is_none_outside_any_git_repo(wired, tmp_path):
    plain = tmp_path / "not-git"
    plain.mkdir()

    async def scenario():
        return await client.resolve_repo(plain)

    assert run_with_app(wired, scenario) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `just test tests/test_client_repo_context.py`
Expected: FAIL — `AttributeError: module 'kraft.client' has no attribute 'resolve_repo'`

- [ ] **Step 3: Write the implementation**

Add to `src/kraft/client.py`, immediately after `resolve_context` (and add `from kraft import auth, config` — `config` is already imported):

```python
async def resolve_repo(cwd: Path | None = None) -> str | None:
    """The connected repo the cwd is inside, or None.

    Reads `GET /repos` rather than parsing `repos.yaml`: a local YAML read would
    work with the server down, but it puts a second reader on config the API
    already owns and shapes, and every verb that uses this answer needs the
    server anyway.

    Parents are walked so a submodule checkout resolves to the connected
    superproject.
    """
    here = cwd or Path.cwd()
    toplevel = config.git_read(here, "rev-parse", "--show-toplevel", expected_failure=True)
    if toplevel is None:
        return None
    payload = await _get("/repos")
    connected = {entry["path"] for entry in payload["repos"]}
    root = Path(toplevel)
    for candidate in (root, *root.parents):
        if str(candidate) in connected:
            return str(candidate)
    return None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `just test tests/test_client_repo_context.py`
Expected: PASS (5 tests)

- [ ] **Step 5: Lint and commit**

```bash
just lint
git add src/kraft/client.py tests/test_client_repo_context.py
git commit -m "feat(cli): resolve_repo() — cwd to connected repo, parents walked"
```

---

### Task 4: argparse dispatch, with bare `kraft` still serving

**Files:**
- Modify: `src/kraft/cli.py`
- Modify: `tests/conftest.py`
- Create: `tests/test_cli_verbs.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Consumes: `cli._serve`, `kraft.mcp.serve_stdio`, `kraft.init.install`.
- Produces:
  - `cli.build_parser() -> argparse.ArgumentParser`
  - `cli.emit(value, renderer, as_json: bool) -> None`
  - `cli.main(argv: list[str] | None = None) -> None` — unchanged signature.
  - Each verb handler is `def _cmd_<verb>(ns: argparse.Namespace) -> None`, set as the subparser's `func` default.

This task wires the framework and moves the two existing subcommands into it. Verbs arrive in Tasks 5 and 6.

- [ ] **Step 1: Add the `app` fixture to `tests/conftest.py`**

Every `test_cli_*.py` in sub-projects A–E drives `cli.main()` against the ASGI app; one fixture, in conftest, so it is written once. Append to `tests/conftest.py`:

```python
from pathlib import Path

import httpx

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FAKE_CLAUDE = _REPO_ROOT / "fixtures" / "fake-claude.sh"


@pytest.fixture
def app(tmp_path, monkeypatch):
    """The app wired to client.http(), with its lifespan entered per call.

    cli.main() runs asyncio.run() itself, so — unlike test_client_read.py, where
    one coroutine owns the loop — the lifespan cannot stay open across the call.
    Each handler opens and closes its own loop, so each gets its own lifespan.
    """
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("KRAFT_BD_CWD", str(isolated_bd(tmp_path)))
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(fake_templates_dir(tmp_path, str(_FAKE_CLAUDE))))
    monkeypatch.setenv(
        "KRAFT_FRONTEND_DIST", os.environ.get("KRAFT_FRONTEND_DIST") or str(tmp_path / "no-dist")
    )
    monkeypatch.delenv("KRAFT_WORK_ITEM_ID", raising=False)
    monkeypatch.delenv("NO_COLOR", raising=False)
    import kraft.api as api
    from support.harness import fake_templates_dir, isolated_bd

    from kraft import client

    class Lifespan(httpx.AsyncClient):
        """An AsyncClient that enters the app lifespan for the life of the client."""

        async def __aenter__(self):
            self._ctx = api.app.router.lifespan_context(api.app)
            await self._ctx.__aenter__()
            return await super().__aenter__()

        async def __aexit__(self, *exc):
            await super().__aexit__(*exc)
            await self._ctx.__aexit__(*exc)

    monkeypatch.setattr(
        client,
        "http",
        lambda: Lifespan(
            transport=httpx.ASGITransport(app=api.app), base_url="http://kraft"
        ),
    )
    return api
```

Move `from support.harness import fake_templates_dir, isolated_bd` and `from kraft import client` to the top of `conftest.py` with the other imports if ruff prefers; the imports are inside the fixture above only so the block pastes cleanly.

- [ ] **Step 1b: Write the failing tests**

```python
# tests/test_cli_verbs.py
"""Every verb through cli.main(), with client.http() on the ASGI app.

This tests the dispatch table and the rendering, not httpx: the client layer has
its own tests in test_client_*.py.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from support.harness import make_repo

from kraft import cli, client


def test_bare_kraft_still_serves(monkeypatch):
    """The zero-argument default predates the CLI and must survive it."""
    served = []
    monkeypatch.setattr(cli, "_serve", lambda: served.append(True))
    cli.main([])
    assert served == [True]


def test_an_unknown_verb_is_a_usage_error(capsys):
    with pytest.raises(SystemExit) as caught:
        cli.main(["nonsense"])
    assert caught.value.code == 2
    assert "nonsense" in capsys.readouterr().err


def test_mcp_still_dispatches(monkeypatch):
    called = []
    import kraft.mcp as mcp

    monkeypatch.setattr(mcp, "serve_stdio", lambda: called.append(True))
    cli.main(["mcp"])
    assert called == [True]


def test_init_still_dispatches_and_honours_repo_scope(monkeypatch, tmp_path, capsys):
    seen = {}
    import kraft.init as init_mod

    def fake_install(repo_scope):
        seen["repo_scope"] = repo_scope
        return [tmp_path / "written.json"]

    monkeypatch.setattr(init_mod, "install", fake_install)
    cli.main(["init", "--repo"])
    assert seen["repo_scope"] is True
    assert "written.json" in capsys.readouterr().out


```

The error-path and `--json`-equality tests need a verb that reaches the server, so they arrive in Task 5 with `list` and `show`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `just test tests/test_cli_verbs.py`
Expected: FAIL — `cli.main(["nonsense"])` raises `SystemExit("kraft: unknown command 'nonsense' ...")` from the old string-compare dispatch, whose exit code is the message string, not `2`.

- [ ] **Step 3: Write the implementation**

Replace `main` in `src/kraft/cli.py` and add the framework above it. Imports at the top of the file gain `argparse`, `asyncio`, `json`, and `from kraft import client, render`.

```python
def emit(value, renderer, as_json: bool) -> None:
    """One place decides human-or-JSON, so no verb can forget the contract.

    `--json` prints exactly what `client.py` returned. The CLI must never become
    a second definition of what a work item is — the MCP door reads the same
    value, and the two are only guaranteed identical if neither reshapes.
    """
    if as_json:
        print(json.dumps(value, indent=2))
    else:
        print(renderer(value))


def _json_flag() -> argparse.ArgumentParser:
    """A parent parser so `--json` works on every verb without eight copies."""
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument(
        "--json", action="store_true", help="print the raw API payload instead of a table"
    )
    return parent


def _cmd_mcp(ns: argparse.Namespace) -> None:
    from kraft.mcp import serve_stdio

    serve_stdio()


def _cmd_init(ns: argparse.Namespace) -> None:
    from kraft.init import install

    for path in install(repo_scope=ns.repo):
        print(f"kraft: wrote {path}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kraft",
        description="Kraft: run it with no arguments to serve; subcommands talk to a server.",
    )
    subs = parser.add_subparsers(dest="verb", required=True)
    common = _json_flag()

    mcp = subs.add_parser("mcp", help="serve the MCP tools over stdio")
    mcp.set_defaults(func=_cmd_mcp)

    init = subs.add_parser("init", help="register Kraft's MCP server and skills with an agent")
    init.add_argument("--repo", action="store_true", help="install into this repo, not the user")
    init.set_defaults(func=_cmd_init)

    _add_verbs(subs, common)
    return parser


def _add_verbs(subs, common: argparse.ArgumentParser) -> None:
    """Filled in by Tasks 5 and 6. Split out so the framework has one seam."""


def main(argv: list[str] | None = None) -> None:
    """Bare `kraft` serves, as it always has. Subcommands are the two front doors.

    The zero-argument check happens before argparse sees anything: serving must
    stay the default, and argparse would print usage for an empty argv. (This
    file used to avoid argparse entirely, on the grounds that one string compare
    was the whole dispatch. That stopped being true at eight verbs with flags.)
    """
    args = sys.argv[1:] if argv is None else list(argv)
    if not args:
        _serve()
        return
    ns = build_parser().parse_args(args)
    try:
        ns.func(ns)
    except (ValueError, PermissionError) as exc:
        # ValueError is what client.py raises for every API and context failure;
        # PermissionError is the worker self-action guard.
        print(f"kraft: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except KeyboardInterrupt:
        raise SystemExit(130) from None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `just test tests/test_cli_verbs.py tests/test_cli.py`
Expected: PASS (4 tests here, plus the existing seeding tests). Every test in this task exercises dispatch only — none of them needs a server.

- [ ] **Step 5: Lint and commit**

```bash
just lint
git add src/kraft/cli.py tests/conftest.py tests/test_cli_verbs.py tests/test_cli.py
git commit -m "feat(cli): argparse dispatch, --json contract, bare kraft still serves"
```

---

### Task 5: Read verbs — `list`, `show`, `search`

**Files:**
- Modify: `src/kraft/cli.py` (`_add_verbs`, three handlers, three renderers)
- Modify: `tests/test_cli_verbs.py`

**Interfaces:**
- Consumes: `client.list_work_items`, `client.get_work_item`, `client.search`, `client.resolve_repo`, `render.table`, `render.kv`, `cli.emit`.
- Produces: `_cmd_list`, `_cmd_show`, `_cmd_search`, and `_repo_scope(ns) -> str | None` — the shared "explicit `--repo` → cwd repo → None" resolver Task 6 also uses.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cli_verbs.py` (and remove the `xfail` marker added in Task 4):

```python
def _make_item(app, repo, title="a thing"):
    """Create one work item through the API, returning its id."""
    import asyncio

    async def go():
        async with client.http() as http:
            response = await http.post(
                "/work-items",
                json={"title": title, "repo": str(repo), "autostart": False},
            )
        assert response.status_code == 201, response.text
        return response.json()["id"]

    return asyncio.run(go())


def _connect(repo):
    import asyncio

    return asyncio.run(client.ensure_repo(str(repo)))


def test_list_renders_a_table_with_a_header(app, tmp_path, capsys):
    repo = make_repo(tmp_path)
    _make_item(app, repo, "first thing")
    cli.main(["list"])
    out = capsys.readouterr().out
    assert "ID" in out.splitlines()[0] and "TITLE" in out.splitlines()[0]
    assert "first thing" in out


def test_list_json_matches_the_client_payload(app, tmp_path, capsys):
    import asyncio

    repo = make_repo(tmp_path)
    _make_item(app, repo, "first thing")
    cli.main(["list", "--json"])
    printed = json.loads(capsys.readouterr().out)
    assert printed == asyncio.run(client.list_work_items())


def test_list_filters_by_status(app, tmp_path, capsys):
    repo = make_repo(tmp_path)
    _make_item(app, repo, "first thing")
    cli.main(["list", "--status", "completed", "--json"])
    assert json.loads(capsys.readouterr().out) == []


def test_list_scopes_to_the_cwd_repo(app, tmp_path, monkeypatch, capsys):
    here = make_repo(tmp_path, name="here")
    elsewhere = make_repo(tmp_path, name="elsewhere")
    _connect(here)
    _make_item(app, here, "mine")
    _make_item(app, elsewhere, "theirs")
    monkeypatch.chdir(here)
    cli.main(["list", "--json"])
    titles = [item["title"] for item in json.loads(capsys.readouterr().out)]
    assert titles == ["mine"]


def test_list_all_ignores_the_cwd_scope(app, tmp_path, monkeypatch, capsys):
    here = make_repo(tmp_path, name="here")
    elsewhere = make_repo(tmp_path, name="elsewhere")
    _connect(here)
    _make_item(app, here, "mine")
    _make_item(app, elsewhere, "theirs")
    monkeypatch.chdir(here)
    cli.main(["list", "--all", "--json"])
    titles = sorted(item["title"] for item in json.loads(capsys.readouterr().out))
    assert titles == ["mine", "theirs"]


def test_show_takes_an_explicit_id(app, tmp_path, capsys):
    repo = make_repo(tmp_path)
    wid = _make_item(app, repo, "detail me")
    cli.main(["show", wid])
    out = capsys.readouterr().out
    assert wid in out and "detail me" in out


def test_show_defaults_to_the_work_item_this_session_is_in(app, tmp_path, monkeypatch, capsys):
    repo = make_repo(tmp_path)
    wid = _make_item(app, repo, "implicit")
    monkeypatch.setenv("KRAFT_WORK_ITEM_ID", wid)
    cli.main(["show"])
    assert "implicit" in capsys.readouterr().out


def test_show_with_no_context_names_both_ways_to_fix_it(app, capsys):
    with pytest.raises(SystemExit) as caught:
        cli.main(["show"])
    assert caught.value.code == 1
    assert "no work item" in capsys.readouterr().err


def test_search_renders_results(app, capsys):
    cli.main(["search", "anything", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["query"] == "anything"


def test_an_operation_failure_is_a_kraft_message_on_stderr(app, capsys):
    with pytest.raises(SystemExit) as caught:
        cli.main(["show", "no-such-item"])
    assert caught.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == ""  # stdout stays clean so --json stays pipeable
    assert captured.err.startswith("kraft: ")
    assert "404" in captured.err
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `just test tests/test_cli_verbs.py`
Expected: FAIL — `argparse` exits 2 on `invalid choice: 'list'`

- [ ] **Step 3: Write the implementation**

In `src/kraft/cli.py`, replace the `_add_verbs` stub and add the handlers and renderers:

```python
_LIST_COLUMNS = [
    ("ID", "id"),
    ("STATUS", "status"),
    ("GATE", "pending_gate"),
    ("NODE", "current_node_id"),
    ("TITLE", "title"),
]


def _render_list(items: list[dict]) -> str:
    painted = [
        {**item, "status": render.paint(item["status"], render.STATUS_COLORS.get(item["status"], ""))}
        for item in items
    ]
    return render.table(painted, _LIST_COLUMNS)


def _render_show(item: dict) -> str:
    return render.kv([(key, str(value)) for key, value in item.items()])


def _render_search(payload: dict) -> str:
    rows = [
        {"kind": hit.get("kind"), "repo": hit.get("repo"), "path": hit.get("path")}
        for hit in payload.get("results", [])
    ]
    return render.table(rows, [("KIND", "kind"), ("REPO", "repo"), ("PATH", "path")])


def _repo_scope(ns: argparse.Namespace) -> str | None:
    """Explicit --repo, then the cwd's connected repo, then nothing.

    The one precedence rule every verb shares. `--all` opts out of the implicit
    half, for when the scoping is what surprised you.
    """
    if getattr(ns, "all", False):
        return None
    if getattr(ns, "repo", None):
        return ns.repo
    return asyncio.run(client.resolve_repo())


def _cmd_list(ns: argparse.Namespace) -> None:
    repo = _repo_scope(ns)
    items = asyncio.run(client.list_work_items(ns.status))
    if repo:
        items = [item for item in items if item["repo"] == repo]
    emit(items, _render_list, ns.json)


def _cmd_show(ns: argparse.Namespace) -> None:
    emit(asyncio.run(client.get_work_item(ns.id)), _render_show, ns.json)


def _cmd_search(ns: argparse.Namespace) -> None:
    emit(asyncio.run(client.search(ns.query, ns.limit)), _render_search, ns.json)
```

```python
def _add_verbs(subs, common: argparse.ArgumentParser) -> None:
    listing = subs.add_parser("list", parents=[common], help="the board")
    listing.add_argument("--status", help="active, needs_human, paused or completed")
    listing.add_argument("--repo", help="only this repo (default: the repo you are standing in)")
    listing.add_argument("--all", action="store_true", help="every repo, ignoring the cwd")
    listing.set_defaults(func=_cmd_list)

    show = subs.add_parser("show", parents=[common], help="one work item")
    show.add_argument("id", nargs="?", help="default: the work item this session is standing in")
    show.set_defaults(func=_cmd_show)

    search = subs.add_parser("search", parents=[common], help="specs, plans and session summaries")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=20)
    search.set_defaults(func=_cmd_search)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `just test tests/test_cli_verbs.py`
Expected: PASS (all read-verb tests, including the error-path and `--json`-equality tests)

- [ ] **Step 5: Lint and commit**

```bash
just lint
git add src/kraft/cli.py tests/test_cli_verbs.py
git commit -m "feat(cli): list, show and search verbs with cwd repo scoping"
```

---

### Task 6: Write and act verbs — `create`, `approve`, `reject`, `pause`, `resume`

**Files:**
- Modify: `src/kraft/cli.py` (`_add_verbs`, five handlers)
- Modify: `tests/test_cli_verbs.py`

**Interfaces:**
- Consumes: `client.create_work_item`, `client.approve_gate`, `client.reject_gate`, `client.pause`, `client.resume`, `cli._repo_scope`, `cli.emit`.
- Produces: `_cmd_create`, `_cmd_approve`, `_cmd_reject`, `_cmd_pause`, `_cmd_resume`, `_render_action(result: dict) -> str`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cli_verbs.py`:

```python
def test_create_uses_the_cwd_repo_and_lands_paused(app, tmp_path, monkeypatch, capsys):
    repo = make_repo(tmp_path)
    _connect(repo)
    monkeypatch.chdir(repo)
    cli.main(["create", "filed from a terminal", "--json"])
    created = json.loads(capsys.readouterr().out)
    assert created["title"] == "filed from a terminal"
    # an agent (or a human) files work; a human starts it from the board
    assert created["status"] == "paused"


def test_create_outside_a_connected_repo_says_how_to_fix_it(app, tmp_path, monkeypatch, capsys):
    stranger = make_repo(tmp_path, name="stranger")
    monkeypatch.chdir(stranger)
    with pytest.raises(SystemExit) as caught:
        cli.main(["create", "nowhere"])
    assert caught.value.code == 1
    assert "no repo" in capsys.readouterr().err


def test_reject_requires_a_note(app, capsys):
    with pytest.raises(SystemExit) as caught:
        cli.main(["reject", "Kraft-whatever"])
    assert caught.value.code == 2  # missing required argument is a usage error
    assert "--note" in capsys.readouterr().err


def test_a_worker_cannot_act_on_its_own_work_item(app, tmp_path, monkeypatch, capsys):
    """The §6 rule-2 guard fires through the CLI door too, not only through MCP."""
    repo = make_repo(tmp_path)
    wid = _make_item(app, repo, "mine to do, not to approve")
    monkeypatch.setenv("KRAFT_WORK_ITEM_ID", wid)
    with pytest.raises(SystemExit) as caught:
        cli.main(["approve"])
    assert caught.value.code == 1
    assert "cannot act on its own work item" in capsys.readouterr().err


def test_resume_starts_a_paused_item(app, tmp_path, capsys):
    repo = make_repo(tmp_path)
    wid = _make_item(app, repo, "start me")
    cli.main(["resume", wid, "--steer", "go left", "--json"])
    assert capsys.readouterr().out.strip()  # the API's response, whatever shape it has


def test_pause_on_a_paused_item_surfaces_the_api_error(app, tmp_path, capsys):
    repo = make_repo(tmp_path)
    wid = _make_item(app, repo, "already paused")
    with pytest.raises(SystemExit) as caught:
        cli.main(["pause", wid])
    assert caught.value.code == 1
    assert capsys.readouterr().err.startswith("kraft: ")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `just test tests/test_cli_verbs.py`
Expected: FAIL — `invalid choice: 'create'`

- [ ] **Step 3: Write the implementation**

Handlers in `src/kraft/cli.py`:

```python
def _render_action(result: dict) -> str:
    """An act response is small and shapeless; a kv block beats inventing a table."""
    return render.kv([(key, str(value)) for key, value in result.items()]) or "ok"


def _cmd_create(ns: argparse.Namespace) -> None:
    emit(
        asyncio.run(client.create_work_item(ns.title, _repo_scope(ns), ns.chain)),
        _render_action,
        ns.json,
    )


def _cmd_approve(ns: argparse.Namespace) -> None:
    emit(asyncio.run(client.approve_gate(ns.gate, ns.id)), _render_action, ns.json)


def _cmd_reject(ns: argparse.Namespace) -> None:
    emit(asyncio.run(client.reject_gate(ns.note, ns.gate, ns.id)), _render_action, ns.json)


def _cmd_pause(ns: argparse.Namespace) -> None:
    emit(asyncio.run(client.pause(ns.id)), _render_action, ns.json)


def _cmd_resume(ns: argparse.Namespace) -> None:
    emit(asyncio.run(client.resume(ns.steer, ns.id)), _render_action, ns.json)
```

Append to `_add_verbs`:

```python
    create = subs.add_parser("create", parents=[common], help="file a work item (starts paused)")
    create.add_argument("title")
    create.add_argument("--repo", help="default: the repo you are standing in")
    create.add_argument("--chain", default="quick-task", help="chain template (default quick-task)")
    create.set_defaults(func=_cmd_create, all=False)

    approve = subs.add_parser("approve", parents=[common], help="approve the pending gate")
    approve.add_argument("id", nargs="?")
    approve.add_argument("--gate", help="default: whichever gate is pending")
    approve.set_defaults(func=_cmd_approve)

    reject = subs.add_parser("reject", parents=[common], help="reject the pending gate")
    reject.add_argument("id", nargs="?")
    reject.add_argument("--note", required=True, help="what is wrong; a rejection needs a reason")
    reject.add_argument("--gate", help="default: whichever gate is pending")
    reject.set_defaults(func=_cmd_reject)

    pause = subs.add_parser("pause", parents=[common], help="stop the running attempt")
    pause.add_argument("id", nargs="?")
    pause.set_defaults(func=_cmd_pause)

    resume = subs.add_parser("resume", parents=[common], help="start or restart a paused item")
    resume.add_argument("id", nargs="?")
    resume.add_argument("--steer", help="carried into the next attempt's prompt")
    resume.set_defaults(func=_cmd_resume)
```

Note `create.set_defaults(..., all=False)`: `_repo_scope` reads `ns.all`, and `create` has no `--all` flag because creating into "every repo" is meaningless.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `just test tests/test_cli_verbs.py`
Expected: PASS

- [ ] **Step 5: Run the whole suite**

Run: `just test`
Expected: PASS — in particular `tests/test_cli.py`, `tests/test_mcp.py` and every `tests/test_client_*.py`, which prove the MCP door and the client layer are unchanged.

- [ ] **Step 6: Lint and commit**

```bash
just lint
git add src/kraft/cli.py tests/test_cli_verbs.py
git commit -m "feat(cli): create, approve, reject, pause and resume verbs"
```

---

### Task 7: Document the surface

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md` and `AGENTS.md` (mirror substantive edits across both — they are independent files, not symlinks)

**Interfaces:**
- Consumes: the verbs from Tasks 4–6.
- Produces: no code.

- [ ] **Step 1: Add a CLI section to `README.md`**

Place it after the "Running Kraft" section:

````markdown
## The `kraft` command

`kraft` with no arguments serves. Subcommands talk to a running server.

```bash
kraft list                      # the board, scoped to the repo you are in
kraft list --all --status=paused
kraft show                      # the work item whose worktree you are in
kraft create "fix the flaky test"   # files it paused; a human starts it
kraft approve                   # approve whichever gate is pending
kraft reject --note "the plan skips migrations"
kraft pause / kraft resume --steer "try the other adapter"
kraft search "retry policy"
```

Every verb takes `--json`, which prints the raw API payload — the same value
`kraft mcp` hands an agent. An id is optional wherever the work item can be
inferred from the directory you are standing in.
````

- [ ] **Step 2: Add the same block to `CLAUDE.md` and `AGENTS.md`**

Under "Running Kraft", one short paragraph plus the command list, so an agent
reading either file learns the CLI exists without opening the README.

- [ ] **Step 3: Verify the help text matches the docs**

Run: `just install && kraft --help && kraft list --help`
Expected: every documented verb and flag appears; nothing documented is missing.

- [ ] **Step 4: Commit**

```bash
just lint
git add README.md CLAUDE.md AGENTS.md
git commit -m "docs: the kraft CLI verbs"
```

- [ ] **Step 5: Close the beads issue**

```bash
bd close Kraft-bbg.1 --reason="CLI-A shipped: argparse dispatch, render.py, resolve_repo, 8 verbs"
bd ready   # B, C, D and E unblock here
```

---

## Self-Review

**Spec coverage**

| Spec section | Task |
|---|---|
| §3 verbs (8) | 5 and 6 |
| §3 `--all`, verb-name reservations | 5 |
| §4 output contract (table default, `--json` exact, colour, stderr, exit codes) | 1 and 4 |
| §4.1 rendering in one module | 1 |
| §5 `resolve_repo`, precedence, no branch resolution | 3 and 5 (`_repo_scope`) |
| §6 connect error in `client.py` | 2 |
| §7 argparse, zero-arg serve preserved, comment rewritten | 4 |
| §8 testing (per-verb, `--json` equality, context, bare serve, connect error, self-action guard) | 1–6 |
| §9 decisions | all |

**Placeholder scan:** the `app` fixture lives in `tests/conftest.py` (Task 4 Step 1) and every later sub-project's `test_cli_*.py` uses it unchanged. The only forward reference is the deliberately-named-then-deleted test in Task 4 Step 1b, which carries its own instruction and is written in full in Task 5. No TBDs, no "add error handling", no "similar to Task N".

**Type consistency:** `emit(value, renderer, as_json)` is used with that signature in Tasks 4, 5 and 6. `_repo_scope(ns)` is defined in Task 5 and consumed in Task 6, where `create` sets `all=False` because `_repo_scope` reads it. `render.table(rows, columns, width=None)` is called with two arguments everywhere outside its own tests. `client.resolve_repo` is async and every call site wraps it in `asyncio.run`.
