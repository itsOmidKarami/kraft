# Agent Integration Phase 1 (Read Path) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An agent session started by a human can read Kraft's board, one work item, and the cross-repo search index, over MCP.

**Architecture:** One core module `src/kraft/client.py` holds async functions that speak HTTP to the local Kraft server and own base-URL, auth, and identity resolution. `kraft mcp` is a stdio MCP server that is a thin dispatch table over it. `kraft` with no arguments still seeds-and-serves, unchanged. Nothing in this phase mutates state.

**Tech Stack:** Python 3.14, httpx (promoted to a runtime dependency here), the `mcp` Python SDK (`FastMCP`), FastAPI + `TestClient` for tests, pytest.

**Spec:** `docs/superpowers/specs/2026-09-05-agent-integration-design.md`

## Global Constraints

- Python `>=3.14` (`pyproject.toml`).
- `from __future__ import annotations` at the top of every new module — every existing module in `src/kraft/` does this.
- No new writer on `orchestrator.db`. Every client function goes over HTTP to `api.py`. (Spec §3.)
- Nothing in this phase mutates state: read tools only. No `POST`, `PUT`, `PATCH`, or `DELETE` from `client.py` in phase 1. (Spec §9 phase 1.)
- Auth posture is unchanged from `src/kraft/auth.py`: off on localhost, on for anything else. The bearer token is only consulted when `_requires_auth(app)` is already true. (Spec §5.)
- Line length and formatting follow `ruff`; run `just lint` before each commit.
- Tests live in `tests/`, use `tmp_path` and `monkeypatch`, and follow the `_client(tmp_path, monkeypatch)` pattern in `tests/test_api.py:15-30`.

---

### Task 1: Runtime dependencies and `kraft` subcommand dispatch

`kraft.cli.main()` currently takes no arguments and always serves. This task gives it a subcommand it can dispatch on without changing what bare `kraft` does, and promotes `httpx` from a dev dependency to a runtime one because `client.py` imports it at runtime.

**Files:**
- Modify: `pyproject.toml:6-13` (dependencies), `pyproject.toml:20-26` (dev group)
- Modify: `src/kraft/cli.py:58-64` (`main`)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `kraft.cli.main(argv: list[str] | None = None) -> None`. Passing `argv=[]` serves; `argv=["mcp"]` runs the MCP server; anything else raises `SystemExit`. Later tasks add branches here.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli.py`:

```python
def test_bare_kraft_still_serves(monkeypatch):
    served = []
    monkeypatch.setattr(cli, "_serve", lambda: served.append(True))
    cli.main([])
    assert served == [True]


def test_unknown_subcommand_exits_with_a_usable_message(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "_serve", lambda: pytest.fail("must not serve"))
    with pytest.raises(SystemExit) as exc:
        cli.main(["wat"])
    assert "wat" in str(exc.value)
```

Add `import pytest` to the imports at the top of `tests/test_cli.py`.

- [ ] **Step 2: Run test to verify it fails**

Run: `just test -k "bare_kraft or unknown_subcommand"`
Expected: FAIL — `AttributeError: module 'kraft.cli' has no attribute '_serve'`

- [ ] **Step 3: Write minimal implementation**

In `src/kraft/cli.py`, add `import sys` to the imports, then replace the body of `main` with:

```python
def _serve() -> None:
    templates_dir = Path(os.environ.get("KRAFT_TEMPLATES_DIR") or default_templates_dir())
    if seed_home(templates_dir):
        print(f"kraft: seeded default config in {templates_dir}")
    host, port = _bind(templates_dir)
    print(f"kraft: http://{host}:{port}")
    uvicorn.run("kraft.api:app", host=host, port=port, log_level="warning")


def main(argv: list[str] | None = None) -> None:
    """Bare `kraft` serves, as it always has. Subcommands are the agent surface.

    argparse is deliberately not used: the serving path must stay the
    zero-argument default, and one string compare is the whole dispatch.
    """
    args = sys.argv[1:] if argv is None else list(argv)
    if not args:
        _serve()
        return
    if args[0] == "mcp":
        from kraft.mcp import serve_stdio

        serve_stdio()
        return
    raise SystemExit(f"kraft: unknown command {args[0]!r} (try `kraft` or `kraft mcp`)")
```

In `pyproject.toml`, move `"httpx>=0.28.1"` out of the `dev` group and into `[project].dependencies`, and add the MCP SDK. The dependencies list becomes:

```toml
dependencies = [
    "pyyaml>=6",
    "psutil>=5",
    "fastapi>=0.141.1",
    "uvicorn>=0.52.4",
    "websockets>=15",
    "sqlite-vec>=0.1.9",
    # client.py imports these at runtime, not just under test. httpx was a dev
    # dependency until the agent surface existed; a `kraft mcp` installed from a
    # wheel has no dev group to fall back on.
    "httpx>=0.28.1",
    "mcp>=1.2",
]
```

Delete the `"httpx>=0.28.1",` line from `[dependency-groups].dev`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv sync && just test -k "bare_kraft or unknown_subcommand"`
Expected: PASS, 2 passed. `kraft.mcp` does not exist yet, but nothing imports it until Task 5 — the import is inside the branch on purpose.

- [ ] **Step 5: Verify nothing else broke and commit**

Run: `just test tests/test_cli.py && just lint`
Expected: all pass.

```bash
git add pyproject.toml uv.lock src/kraft/cli.py tests/test_cli.py
git commit -m "kraft grows a subcommand, and httpx becomes a real dependency"
```

---

### Task 2: `resolve_context()` — which work item is this session in

Pure path and environment logic. A session Kraft started knows its work item from `$KRAFT_WORK_ITEM_ID`; a session a human started in a worktree is identified by its directory, because `executor.py:329` lays worktrees out as `run_dirs.worktrees / work_item_id`.

**Files:**
- Create: `src/kraft/client.py`
- Test: `tests/test_client_context.py`

**Interfaces:**
- Consumes: `kraft.paths.RunDirs`, `kraft.paths.default_run_dir` (existing).
- Produces: `kraft.client.resolve_context(cwd: Path | None = None) -> tuple[str | None, str]`, returning `(work_item_id_or_None, origin)` where `origin` is `"worker"` or `"user"`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_client_context.py`:

```python
"""Which work item a session is in — the lookup every client function defaults to."""

from __future__ import annotations

import pytest

from kraft import client


@pytest.fixture
def run_dir(monkeypatch, tmp_path):
    base = tmp_path / "run"
    (base / "worktrees").mkdir(parents=True)
    monkeypatch.setenv("KRAFT_RUN_DIR", str(base))
    monkeypatch.delenv("KRAFT_WORK_ITEM_ID", raising=False)
    return base


def test_env_var_wins_and_marks_the_caller_a_worker(run_dir, monkeypatch):
    monkeypatch.setenv("KRAFT_WORK_ITEM_ID", "abc123")
    assert client.resolve_context(cwd=run_dir) == ("abc123", "worker")


def test_worktree_root_resolves_to_its_work_item(run_dir):
    wt = run_dir / "worktrees" / "deadbeef"
    wt.mkdir()
    assert client.resolve_context(cwd=wt) == ("deadbeef", "user")


def test_a_subdirectory_of_the_worktree_resolves_too(run_dir):
    deep = run_dir / "worktrees" / "deadbeef" / "src" / "kraft"
    deep.mkdir(parents=True)
    assert client.resolve_context(cwd=deep) == ("deadbeef", "user")


def test_outside_any_worktree_resolves_to_nothing(run_dir, tmp_path):
    elsewhere = tmp_path / "some" / "other" / "repo"
    elsewhere.mkdir(parents=True)
    assert client.resolve_context(cwd=elsewhere) == (None, "user")


def test_the_worktrees_directory_itself_is_not_a_work_item(run_dir):
    assert client.resolve_context(cwd=run_dir / "worktrees") == (None, "user")


def test_a_missing_cwd_does_not_raise(run_dir):
    assert client.resolve_context(cwd=run_dir / "gone") == (None, "user")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `just test tests/test_client_context.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'kraft.client'`

- [ ] **Step 3: Write minimal implementation**

Create `src/kraft/client.py`:

```python
"""The one place that knows how to talk to a local Kraft server.

`kraft mcp` and the `kraft` subcommands are both dispatch tables over this
module; neither holds logic the other lacks. Validation is not duplicated here
— it lives in `api.py`, where the UI already exercises it.
"""

from __future__ import annotations

import os
from pathlib import Path

from kraft.paths import RunDirs, default_run_dir


def resolve_context(cwd: Path | None = None) -> tuple[str | None, str]:
    """`(work_item_id, origin)` for the session calling us.

    `origin` is `"worker"` only when `$KRAFT_WORK_ITEM_ID` is set, which the
    executor injects into sessions it starts. That is what the self-action guard
    keys on, so a worker that somehow lost the variable would read as a human —
    see spec §4, "Ordering constraint": the guard and the injection must ship
    together.
    """
    wid = os.environ.get("KRAFT_WORK_ITEM_ID")
    if wid:
        return wid, "worker"
    worktrees = RunDirs(Path(os.environ.get("KRAFT_RUN_DIR") or default_run_dir())).worktrees
    try:
        worktrees = worktrees.resolve()
        here = (cwd or Path.cwd()).resolve()
    except OSError:
        return None, "user"
    for candidate in (here, *here.parents):
        if candidate.parent == worktrees:
            return candidate.name, "user"
    return None, "user"
```

`Path.resolve()` on a missing path does not raise on POSIX, so the walk simply
finds no match — the `OSError` guard is for an unreadable cwd.

- [ ] **Step 4: Run test to verify it passes**

Run: `just test tests/test_client_context.py`
Expected: PASS, 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/kraft/client.py tests/test_client_context.py
git commit -m "A session can work out which work item it is standing in"
```

---

### Task 3: Bearer token for password-protected instances

On loopback with no password there is nothing to authenticate and this changes nothing. When a password *is* set, every request `client.py` makes would 401, because the only credential `api.py` accepts today is a browser session cookie (`api.py:248`). This task adds a second accepted credential.

The bearer check must go **after** the `sec-fetch-dest` SPA-shell branch (`api.py:236-246`): that branch returns `index.html` for any GET claiming to be a document navigation, so a check placed before it leaves that path reachable, and one placed inside it answers MCP calls with HTML.

**Files:**
- Modify: `src/kraft/auth.py` (append)
- Modify: `src/kraft/api.py:70` (startup), `src/kraft/api.py:247-252` (middleware)
- Test: `tests/test_client_auth.py`

**Interfaces:**
- Consumes: `kraft.auth.new_token` (existing, `auth.py:52`).
- Produces: `kraft.auth.ensure_mcp_token(run_dir: Path) -> str` and `kraft.auth.read_mcp_token(run_dir: Path) -> str | None`; `app.state.mcp_token`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_client_auth.py`:

```python
"""The bearer credential `client.py` uses when a password is set."""

from __future__ import annotations

import os

from kraft import auth


def test_ensure_mcp_token_creates_a_private_file_once(tmp_path):
    first = auth.ensure_mcp_token(tmp_path)
    assert first
    assert auth.read_mcp_token(tmp_path) == first
    # regenerating on every serve would invalidate a registered MCP client
    assert auth.ensure_mcp_token(tmp_path) == first


def test_the_token_file_is_not_world_readable(tmp_path):
    auth.ensure_mcp_token(tmp_path)
    mode = os.stat(tmp_path / "mcp-token").st_mode & 0o777
    assert mode == 0o600


def test_read_returns_none_when_there_is_no_token(tmp_path):
    assert auth.read_mcp_token(tmp_path) is None


def test_an_empty_token_file_is_replaced_not_trusted(tmp_path):
    (tmp_path / "mcp-token").write_text("   \n")
    assert auth.read_mcp_token(tmp_path) is None
    assert auth.ensure_mcp_token(tmp_path).strip()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `just test tests/test_client_auth.py`
Expected: FAIL — `AttributeError: module 'kraft.auth' has no attribute 'ensure_mcp_token'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/kraft/auth.py` (and add `import os` and `from pathlib import Path` to its imports):

```python
MCP_TOKEN_FILE = "mcp-token"


def read_mcp_token(run_dir: Path) -> str | None:
    """The token on disk, or None. Whitespace-only counts as absent."""
    try:
        token = (Path(run_dir) / MCP_TOKEN_FILE).read_text().strip()
    except OSError:
        return None
    return token or None


def ensure_mcp_token(run_dir: Path) -> str:
    """The bearer credential for non-browser clients (design §5).

    Created once and kept: regenerating per serve would silently break an MCP
    client registered against the old value. Opened 0600 rather than chmod'd
    after writing, so the secret is never briefly world-readable.
    """
    existing = read_mcp_token(run_dir)
    if existing:
        return existing
    token = new_token()
    path = Path(run_dir) / MCP_TOKEN_FILE
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(token)
    return token
```

In `src/kraft/api.py`, immediately after the `run_dirs = RunDirs(...).ensure()` line at `api.py:70`, add:

```python
    app.state.mcp_token = auth_mod.ensure_mcp_token(run_dirs.base)
```

In the `_authenticate` middleware, replace the cookie check (`api.py:247-252`) with:

```python
    # After the SPA-shell branch above on purpose: that branch answers any GET
    # claiming `sec-fetch-dest: document`, so a bearer check placed before it
    # would leave that path unauthenticated, and one placed inside it would hand
    # an MCP client HTML instead of JSON.
    bearer = request.headers.get("authorization", "")
    expected = getattr(app.state, "mcp_token", None)
    if expected and bearer.startswith("Bearer ") and hmac.compare_digest(bearer[7:], expected):
        return await call_next(request)
    token = request.cookies.get(auth_mod.COOKIE)
    if not token or not await app.state.db.write(
        lambda c, token=token: auth_mod.touch_session(c, token)
    ):
        return JSONResponse({"detail": "authentication required"}, status_code=401)
    return await call_next(request)
```

Add `import hmac` to `src/kraft/api.py`'s imports if it is not already there.

- [ ] **Step 4: Run tests to verify they pass**

Run: `just test tests/test_client_auth.py && just test tests/test_settings_api.py tests/test_api.py`
Expected: PASS. The existing auth tests must still pass — this adds a credential, it does not replace one.

- [ ] **Step 5: Commit**

```bash
git add src/kraft/auth.py src/kraft/api.py tests/test_client_auth.py
git commit -m "A non-browser client can authenticate without a cookie"
```

---

### Task 4: `client.py` read functions

Three functions over endpoints that already exist. Each trims its response: `GET /work-items` returns the full `chain_definition` for *every* item (`api.py:399`) and `GET /work-items/{wid}` returns the whole row plus every worker session (`api.py:429-442`). Handing that to an agent verbatim burns its context on JSON it did not ask for.

**Files:**
- Modify: `src/kraft/client.py`
- Test: `tests/test_client_read.py`

**Interfaces:**
- Consumes: `resolve_context()` from Task 2; `auth.read_mcp_token` from Task 3.
- Produces:
  - `kraft.client.base_url() -> str`
  - `kraft.client.http() -> httpx.AsyncClient` (module-level, monkeypatched by tests)
  - `async kraft.client.list_work_items(status: str | None = None) -> list[dict]`
  - `async kraft.client.get_work_item(work_item_id: str | None = None) -> dict`
  - `async kraft.client.search(q: str, limit: int = 20) -> dict`

- [ ] **Step 1: Write the failing test**

Create `tests/test_client_read.py`:

```python
"""client.py against the real app, over ASGI — no server, no MCP client.

Async tests follow the suite's existing shape: a sync test function wrapping an
inner coroutine with `asyncio.run` (see tests/test_adapters_subprocess.py). The
suite has no async-test plugin and does not need one.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import httpx
import pytest
from support.harness import fake_templates_dir, isolated_bd, make_repo

from kraft import client

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FAKE_CLAUDE = _REPO_ROOT / "fixtures" / "fake-claude.sh"


@pytest.fixture
def wired(tmp_path, monkeypatch):
    """The app, with client.http() pointed at it in-process."""
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
        lambda: httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.app), base_url="http://kraft"
        ),
    )
    return api


def test_base_url_prefers_loopback_over_a_wildcard_bind(monkeypatch, tmp_path):
    access = tmp_path / "templates"
    access.mkdir()
    (access / "access.yaml").write_text("bind: 0.0.0.0\nport: 9999\n")
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(access))
    monkeypatch.delenv("KRAFT_HOST", raising=False)
    monkeypatch.delenv("KRAFT_PORT", raising=False)
    # 0.0.0.0 is an address to listen on, never one to connect to
    assert client.base_url() == "http://127.0.0.1:9999"


def _create(repo, title="read me") -> str:
    async def scenario():
        async with client.http() as http:
            response = await http.post("/work-items", json={"title": title, "repo": str(repo)})
        assert response.status_code == 201, response.text
        return response.json()["id"]

    return asyncio.run(scenario())


def test_list_work_items_is_trimmed(wired, tmp_path):
    _create(make_repo(tmp_path))

    items = asyncio.run(client.list_work_items())
    assert [i["title"] for i in items] == ["read me"]
    # the board's full chain definition is not something an agent asked for
    assert set(items[0]) == {
        "id",
        "title",
        "repo",
        "status",
        "current_node_id",
        "pending_gate",
    }


def test_list_work_items_filters_by_status(wired, tmp_path):
    _create(make_repo(tmp_path))
    assert asyncio.run(client.list_work_items(status="no-such-status")) == []


def test_get_work_item_defaults_to_the_resolved_context(wired, tmp_path, monkeypatch):
    wid = _create(make_repo(tmp_path), title="mine")

    monkeypatch.setenv("KRAFT_WORK_ITEM_ID", wid)
    item = asyncio.run(client.get_work_item())
    assert item["id"] == wid
    assert item["title"] == "mine"
    # the detail endpoint's worker_sessions and usage rollup are not forwarded
    assert "worker_sessions" not in item


def test_get_work_item_without_a_context_says_so(wired):
    with pytest.raises(ValueError, match="no work item"):
        asyncio.run(client.get_work_item())


def test_search_passes_the_query_through(wired):
    out = asyncio.run(client.search("anything"))
    assert out["query"] == "anything"
    assert isinstance(out["results"], list)


def test_an_http_error_becomes_a_readable_message(wired):
    with pytest.raises(ValueError, match="404"):
        asyncio.run(client.get_work_item("no-such-item"))
```

The suite has no async-test plugin, so no marker and no fixture are added for
this — `asyncio.run` inside a sync test is the established pattern.

- [ ] **Step 2: Run test to verify it fails**

Run: `just test tests/test_client_read.py`
Expected: FAIL — `AttributeError: module 'kraft.client' has no attribute 'base_url'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/kraft/client.py`, and add these imports at the top: `import httpx`, `from kraft import auth, config`, `from kraft.paths import default_templates_dir`.

```python
def base_url() -> str:
    """Where the local server is listening.

    Mirrors `cli._bind` on host/port resolution but not on its refusal to bind a
    LAN address without a password: that is a rule about *serving*, and this is a
    client. A wildcard bind is rewritten to loopback — `0.0.0.0` is an address to
    listen on, never one to connect to.
    """
    templates_dir = Path(os.environ.get("KRAFT_TEMPLATES_DIR") or default_templates_dir())
    access = config.load_access(templates_dir / "access.yaml")
    host = os.environ.get("KRAFT_HOST") or access["bind"]
    port = int(os.environ.get("KRAFT_PORT") or access["port"])
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"
    return f"http://{host}:{port}"


def http() -> httpx.AsyncClient:
    """A configured client. Module-level so tests can point it at the ASGI app."""
    run_dir = Path(os.environ.get("KRAFT_RUN_DIR") or default_run_dir())
    headers = {}
    token = auth.read_mcp_token(run_dir)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return httpx.AsyncClient(base_url=base_url(), headers=headers, timeout=30)


async def _get(path: str, **params) -> dict | list:
    async with http() as session:
        response = await session.get(path, params={k: v for k, v in params.items() if v is not None})
    if response.status_code >= 400:
        # An agent reads this string. "404: work item not found" is actionable;
        # an httpx traceback is not.
        raise ValueError(f"kraft {response.status_code}: {_detail(response)}")
    return response.json()


def _detail(response: httpx.Response) -> str:
    try:
        return str(response.json().get("detail", response.text))
    except ValueError:
        return response.text


async def list_work_items(status: str | None = None) -> list[dict]:
    """The board, trimmed to what a caller can act on.

    `GET /work-items` carries the full chain definition per row for the UI's
    progress rendering; forwarding that would spend an agent's context on JSON
    it did not ask for.
    """
    payload = await _get("/work-items")
    keep = ("id", "title", "repo", "status", "current_node_id", "pending_gate")
    return [
        {k: item[k] for k in keep}
        for item in payload["items"]
        if status is None or item["status"] == status
    ]


async def get_work_item(work_item_id: str | None = None) -> dict:
    """One item. Defaults to the item this session is standing in."""
    if work_item_id is None:
        work_item_id, _origin = resolve_context()
    if work_item_id is None:
        raise ValueError(
            "no work item: pass one, or run from a Kraft worktree under "
            f"{RunDirs(Path(os.environ.get('KRAFT_RUN_DIR') or default_run_dir())).worktrees}"
        )
    item = await _get(f"/work-items/{work_item_id}")
    keep = (
        "id",
        "title",
        "repo",
        "status",
        "current_node_id",
        "pending_gate",
        "worktree_path",
        "bead_id",
    )
    return {k: item[k] for k in keep if k in item}


async def search(q: str, limit: int = 20) -> dict:
    """Cross-repo search over specs, plans, and session summaries."""
    return await _get("/search", q=q, limit=limit)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `just test tests/test_client_read.py`
Expected: PASS, 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/kraft/client.py tests/test_client_read.py
git commit -m "An agent can read the board without opening a browser"
```

---

### Task 5: `kraft mcp` — the stdio server

A dispatch table. Every tool is a docstring plus a call into `client.py`; no logic lives here, because the CLI front door has to behave identically.

The smoke test runs the real `kraft mcp` process against a real `uvicorn`. `TestClient` runs in-process and would not catch a missing transport dependency — that is exactly how the absent WebSocket library stayed hidden until `tests/test_ws.py` ran against real uvicorn.

**Files:**
- Create: `src/kraft/mcp.py`
- Test: `tests/test_mcp.py`

**Interfaces:**
- Consumes: `client.list_work_items`, `client.get_work_item`, `client.search` from Task 4; the `kraft mcp` branch from Task 1.
- Produces: `kraft.mcp.build() -> FastMCP` and `kraft.mcp.serve_stdio() -> None`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_mcp.py`:

```python
"""The MCP front door. Registration is checked in-process; the transport is
checked against a real server, because an in-process check cannot see a missing
transport dependency (the lesson from tests/test_ws.py)."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from kraft import mcp


def _tools():
    return asyncio.run(mcp.build().list_tools())


def test_the_read_tools_are_registered():
    assert {t.name for t in _tools()} == {"list_work_items", "get_work_item", "search"}


def test_every_tool_has_a_description_an_agent_can_act_on():
    """The docstring is what an agent reads to decide whether to call the tool.
    A one-word description is a tool that never gets used correctly."""
    for tool in _tools():
        assert tool.description and len(tool.description) > 30, tool.name


@pytest.mark.slow
def test_kraft_mcp_starts_over_real_stdio(tmp_path):
    """`kraft mcp` answers an MCP initialize on stdin/stdout as a real process."""
    env = {**os.environ, "KRAFT_HOME": str(tmp_path / "home")}
    request = (
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "0"},
                },
            }
        )
        + "\n"
    )
    proc = subprocess.run(
        [sys.executable, "-m", "kraft", "mcp"],
        input=request,
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
        cwd=Path(__file__).resolve().parents[1],
    )
    assert '"serverInfo"' in proc.stdout, proc.stderr
```

`slow` is already registered in `pyproject.toml:47` ("excluded from the blocking
PR gate; run in the parallel slow-tests CI job") — that is exactly this test's
category, so no marker needs adding.

- [ ] **Step 2: Run test to verify it fails**

Run: `just test tests/test_mcp.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'kraft.mcp'`

- [ ] **Step 3: Write minimal implementation**

Create `src/kraft/mcp.py`:

```python
"""`kraft mcp` — the MCP front door onto `client.py`.

A dispatch table and nothing more. Every tool here is a docstring plus one call
into `client`, because `kraft <verb>` is the same functions behind a different
door and the two must not drift.

Docstrings are the tool descriptions an agent reads to decide whether to call
something, so they are written for that reader, not for a maintainer.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from kraft import client


def build() -> FastMCP:
    server = FastMCP("kraft")

    @server.tool()
    async def list_work_items(status: str | None = None) -> list[dict]:
        """List Kraft work items — the board. Optionally filter by status
        ("active", "paused", "completed", "failed"). Returns id, title, repo,
        status, current node, and any gate waiting on a human."""
        return await client.list_work_items(status)

    @server.tool()
    async def get_work_item(work_item_id: str | None = None) -> dict:
        """Get one Kraft work item. With no id, resolves the work item this
        session is standing in — correct when the cwd is a Kraft worktree or the
        session was started by Kraft itself."""
        return await client.get_work_item(work_item_id)

    @server.tool()
    async def search(q: str, limit: int = 20) -> dict:
        """Search Kraft's cross-repo index of specs, plans, and session
        summaries. Use this before writing a spec, to find whether the decision
        was already made somewhere else."""
        return await client.search(q, limit)

    return server


def serve_stdio() -> None:
    build().run(transport="stdio")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `just test tests/test_mcp.py`
Expected: PASS, 3 passed. If the `slow` test is deselected by the suite's tiering, run it explicitly with `just test tests/test_mcp.py -m slow`.

- [ ] **Step 5: Run the whole suite and commit**

Run: `just test && just lint`
Expected: all pass.

```bash
git add src/kraft/mcp.py tests/test_mcp.py
git commit -m "kraft mcp serves the read tools over stdio"
```

---

## Phase 1 done when

`uv sync && just install`, then registering `kraft mcp` with an agent, gives that agent three working tools: it can list the board, read the item whose worktree it is sitting in without being told an id, and search specs and plans across every registered repo. Nothing it can call mutates state.

Phase 2 (write path: `create_work_item` paused, `ensure_repo`, the `autostart` field, `kraft init`, and the §8.1 amendment to `01_conceptual_model.md`) builds on `client.py` and the `main()` dispatch this phase establishes.
