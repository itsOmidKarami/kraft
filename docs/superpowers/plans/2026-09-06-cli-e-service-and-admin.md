# Kraft CLI Sub-project E — Service and Admin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `kraft serve`, `kraft health` and `kraft reindex` — the command A's connect-error message already tells you to run, the server's own view of itself, and a reindex without a browser.

**Architecture:** `serve` is a named door onto the existing `_serve()`/`_bind()` path with two flags that layer over the env vars those functions already read. `health` and `reindex` are thin wrappers on `GET /health` and `POST /index/rescan`. `doctor` is deferred to `Kraft-bbg.6`. No server changes.

**Tech Stack:** Python 3.14, `uvicorn` (existing), `httpx`, argparse, pytest.

**Spec:** `docs/superpowers/specs/2026-09-06-cli-e-service-and-admin-design.md`

**Beads issue:** `Kraft-bbg.5`. Claim it before Task 1: `bd update Kraft-bbg.5 --claim`.

**Depends on:** sub-project A (`Kraft-bbg.1`) — `cli.build_parser`, `cli.emit`, `render.kv`, `render.paint`, `render.relative_time`, `client._get`, `client._send`.

## Global Constraints

- Python `>=3.14`, ruff `line-length = 100`, no new runtime dependencies.
- **`kraft serve --host/--port` must route through the existing `_bind()`.** The refusal to bind a non-loopback address without a password (`cli._bind`, "refusing to bind ... no password is set") must fire for a flag exactly as it does for `KRAFT_HOST`. A flag must not be a way around a security check an env var respects. This is a required regression test (Task 1), not optional.
- Precedence for bind: **flag > env (`KRAFT_HOST`/`KRAFT_PORT`) > `access.yaml`**. Implemented by setting the env var from the flag before calling `_bind()`, so there is one precedence chain, not two.
- Bare `kraft` still serves, unchanged. `kraft serve` is the documented name for the same thing.
- `kraft health` exits `1` on `"degraded"`, `0` on `"ok"`, so it works in a shell conditional.
- No `kraft stop`, no daemonising, no pidfiles (spec §2). No `doctor` here (spec §6.1).
- Errors to stderr as `kraft: <message>`; exit `0`/`1`/`2` as in A.
- Commits stay local: no push, no merge to `main`, no `bd dolt push` unless asked.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/kraft/cli.py` *(modify)* | `_cmd_serve`, `_cmd_health`, `_cmd_reindex` and their subparsers. |
| `src/kraft/client.py` *(modify)* | `health()`, `reindex()`. |
| `src/kraft/render.py` *(modify)* | `health_block()`. |
| `tests/test_cli.py` *(modify)* | `serve` flag tests, beside the existing seeding tests — including the password regression. |
| `tests/test_cli_admin.py` *(create)* | `health` and `reindex`. |

---

### Task 1: `kraft serve [--host] [--port]`

**Files:**
- Modify: `src/kraft/cli.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Consumes: `cli._serve()`, `cli._bind()`, `cli.seed_home()`.
- Produces: `cli._cmd_serve(ns)`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cli.py`:

```python
def _servable_home(monkeypatch, tmp_path, access_yaml: str) -> Path:
    """A templates dir that already exists, so _serve() skips seeding."""
    home = tmp_path / "templates"
    home.mkdir()
    (home / "access.yaml").write_text(access_yaml)
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(home))
    monkeypatch.delenv("KRAFT_HOST", raising=False)
    monkeypatch.delenv("KRAFT_PORT", raising=False)
    return home


def test_serve_verb_reaches_uvicorn_with_the_configured_bind(monkeypatch, tmp_path):
    _servable_home(monkeypatch, tmp_path, "bind: 127.0.0.1\nport: 8765\n")
    seen = {}
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, **kw: seen.update(kw))
    cli.main(["serve"])
    assert seen["host"] == "127.0.0.1"
    assert seen["port"] == 8765


def test_serve_flags_override_access_yaml(monkeypatch, tmp_path):
    _servable_home(monkeypatch, tmp_path, "bind: 127.0.0.1\nport: 8765\n")
    seen = {}
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, **kw: seen.update(kw))
    cli.main(["serve", "--port", "9001"])
    assert seen["port"] == 9001
    assert seen["host"] == "127.0.0.1"  # untouched: only the flag given changes


def test_serve_flag_beats_env(monkeypatch, tmp_path):
    _servable_home(monkeypatch, tmp_path, "bind: 127.0.0.1\nport: 8765\n")
    monkeypatch.setenv("KRAFT_PORT", "9002")
    seen = {}
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, **kw: seen.update(kw))
    cli.main(["serve", "--port", "9003"])
    assert seen["port"] == 9003


def test_serve_host_flag_cannot_bypass_the_password_check(monkeypatch, tmp_path):
    """The security regression test for this sub-project. A flag must not be a
    way around a check an env var respects."""
    _servable_home(monkeypatch, tmp_path, "bind: 127.0.0.1\nport: 8765\n")
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, **kw: pytest.fail("must not bind"))
    with pytest.raises(SystemExit, match="refusing to bind 0.0.0.0"):
        cli.main(["serve", "--host", "0.0.0.0"])


def test_bare_kraft_and_kraft_serve_are_the_same_path(monkeypatch, tmp_path):
    _servable_home(monkeypatch, tmp_path, "bind: 127.0.0.1\nport: 8765\n")
    calls = []
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, **kw: calls.append(kw))
    cli.main([])
    cli.main(["serve"])
    assert calls[0] == calls[1]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `just test tests/test_cli.py -k serve`
Expected: FAIL — argparse rejects `serve` (the bare-`kraft` test passes already; keep it).

- [ ] **Step 3: Write the implementation**

`src/kraft/cli.py`:

```python
def _cmd_serve(ns: argparse.Namespace) -> None:
    """The same path as bare `kraft`, with two flags on top.

    The flags become the env vars `_bind()` already reads, so there is one
    precedence chain (flag > env > access.yaml) and one place that refuses a
    LAN bind without a password. Setting the env rather than passing arguments
    through is deliberate: a second signature would be a second place for that
    check to be forgotten.
    """
    if ns.host:
        os.environ["KRAFT_HOST"] = ns.host
    if ns.port:
        os.environ["KRAFT_PORT"] = str(ns.port)
    _serve()
```

Subparser:

```python
    serve = subs.add_parser("serve", help="run the server (the same as bare `kraft`)")
    serve.add_argument("--host", help="bind address (default: access.yaml, or KRAFT_HOST)")
    serve.add_argument("--port", type=int, help="port (default: access.yaml, or KRAFT_PORT)")
    serve.set_defaults(func=_cmd_serve)
```

`serve` does **not** take `parents=[common]`: there is no payload to print as JSON.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `just test tests/test_cli.py`
Expected: PASS — including `test_serve_host_flag_cannot_bypass_the_password_check`.

- [ ] **Step 5: Lint and commit**

```bash
just lint
git add src/kraft/cli.py tests/test_cli.py
git commit -m "feat(cli): kraft serve with --host/--port, routed through the same bind check"
```

---

### Task 2: `client.health()` and `client.reindex()`

**Files:**
- Modify: `src/kraft/client.py`
- Create: `tests/test_cli_admin.py`

**Interfaces:**
- Consumes: `client._get`, `client._send`, `client._detail`.
- Produces:
  - `async def health() -> dict` — the `GET /health` payload: `{status, invalid_templates, invalid_policy, reattach_summary, index, bind}`.
  - `async def reindex(repo: str | None = None) -> dict` — `{repo, stats: {inserted, updated, renamed, deleted}}`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cli_admin.py
"""health and reindex — the server's own view, from a terminal."""

from __future__ import annotations

import asyncio
import json

import pytest

from kraft import cli, client


# `app` fixture: tests/conftest.py (sub-project A Task 4). It wires client.http()
# to the ASGI app with the lifespan entered per client.


def test_health_returns_the_status_block(app):
    payload = asyncio.run(client.health())
    assert payload["status"] in ("ok", "degraded")
    assert "index" in payload and "bind" in payload


def test_reindex_all_returns_totals(app):
    payload = asyncio.run(client.reindex())
    assert payload["repo"] is None
    assert set(payload["stats"]) == {"inserted", "updated", "renamed", "deleted"}


def test_reindex_unknown_repo_is_a_readable_404(app):
    with pytest.raises(ValueError, match="404"):
        asyncio.run(client.reindex("/no/such/repo"))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `just test tests/test_cli_admin.py`
Expected: FAIL — `module 'kraft.client' has no attribute 'health'`

- [ ] **Step 3: Write the implementation**

Add to `src/kraft/client.py`:

```python
async def health() -> dict:
    """The server's own view of itself: invalid config, index state, reattach."""
    return await _get("/health")


async def reindex(repo: str | None = None) -> dict:
    """Rescan one repo's documents, or every repo's. Returns the change counts.

    Not through `_act`: `/index/rescan` takes `repo` as a query parameter, and
    `_post` only sends JSON bodies.
    """
    response = await _send("POST", "/index/rescan", params={"repo": repo} if repo else None)
    if response.status_code >= 400:
        raise ValueError(f"kraft {response.status_code}: {_detail(response)}")
    return response.json()
```

`_send` is sub-project A Task 2 (`client._send(method, path, **kwargs) -> httpx.Response`); it is what turns a dead server into the `kraft serve` hint.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `just test tests/test_cli_admin.py`
Expected: PASS (3 tests)

- [ ] **Step 5: Lint and commit**

```bash
just lint
git add src/kraft/client.py tests/test_cli_admin.py
git commit -m "feat(cli): client reads /health and triggers a reindex"
```

---

### Task 3: `kraft health` and `kraft reindex`

**Files:**
- Modify: `src/kraft/render.py` (`health_block`)
- Modify: `src/kraft/cli.py`
- Modify: `tests/test_cli_admin.py`, `tests/test_render.py`

**Interfaces:**
- Consumes: `client.health`, `client.reindex`, `render.kv`, `render.paint`, `cli.emit`.
- Produces: `render.health_block(payload) -> str`, `cli._cmd_health(ns)`, `cli._render_reindex(result) -> str`, `cli._cmd_reindex(ns)`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_render.py`:

```python
def test_health_block_names_each_degraded_reason():
    payload = {
        "status": "degraded",
        "bind": "127.0.0.1",
        "invalid_templates": {"broken.yaml": "no nodes"},
        "invalid_policy": "unknown key: foo",
        "reattach_summary": {"scanned": 2, "adopted": ["s1"], "resolved_from_file": [],
                             "unknown": [], "resumed_work_items": []},
        "index": {"documents": 12, "repos_scanned": 1, "last_scan_at": None,
                  "embeddings": {"available": False, "model": "m", "chunks": 0,
                                 "reason": "fastembed not installed"},
                  "errors": []},
    }
    out = render.health_block(payload)
    assert "degraded" in out
    assert "broken.yaml" in out and "no nodes" in out
    assert "unknown key: foo" in out
    assert "12" in out  # documents
    assert "fastembed not installed" in out
    assert "1 adopted" in out and "2 scanned" in out


def test_health_block_ok_is_short():
    payload = {
        "status": "ok",
        "bind": "127.0.0.1",
        "invalid_templates": {},
        "invalid_policy": None,
        "reattach_summary": {"scanned": 0, "adopted": [], "resolved_from_file": [],
                             "unknown": [], "resumed_work_items": []},
        "index": {"documents": 0, "repos_scanned": 0, "last_scan_at": None,
                  "embeddings": {"available": True, "model": "m", "chunks": 0, "reason": None},
                  "errors": []},
    }
    out = render.health_block(payload)
    assert "ok" in out
    assert "invalid" not in out.lower()
```

Add to `tests/test_cli_admin.py`:

```python
def test_health_exit_code_follows_status(app, monkeypatch, capsys):
    async def degraded():
        return {
            "status": "degraded",
            "bind": "127.0.0.1",
            "invalid_templates": {"x.yaml": "bad"},
            "invalid_policy": None,
            "reattach_summary": {"scanned": 0, "adopted": [], "resolved_from_file": [],
                                 "unknown": [], "resumed_work_items": []},
            "index": {"documents": 0, "repos_scanned": 0, "last_scan_at": None,
                      "embeddings": {"available": True, "model": "m", "chunks": 0,
                                     "reason": None},
                      "errors": []},
        }

    monkeypatch.setattr(client, "health", degraded)
    with pytest.raises(SystemExit) as caught:
        cli.main(["health"])
    assert caught.value.code == 1
    assert "x.yaml" in capsys.readouterr().out  # the reason is on stdout, it is not an error


def test_health_ok_exits_zero(app, capsys):
    cli.main(["health"])  # a fresh fixture home is healthy; no SystemExit
    assert "ok" in capsys.readouterr().out


def test_health_json_is_the_raw_payload(app, capsys):
    cli.main(["health", "--json"])
    assert json.loads(capsys.readouterr().out) == asyncio.run(client.health())


def test_reindex_prints_the_counts(app, capsys):
    cli.main(["reindex"])
    out = capsys.readouterr().out
    for key in ("inserted", "updated", "renamed", "deleted"):
        assert key in out


def test_reindex_unknown_repo_is_a_kraft_message(app, capsys):
    with pytest.raises(SystemExit) as caught:
        cli.main(["reindex", "--repo", "/no/such/repo"])
    assert caught.value.code == 1
    assert "404" in capsys.readouterr().err
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `just test tests/test_render.py -k health tests/test_cli_admin.py`
Expected: FAIL — `render` has no `health_block`; argparse rejects `health`.

- [ ] **Step 3: Write the implementation**

`src/kraft/render.py`:

```python
def health_block(payload: dict) -> str:
    """`/health`, readable. Every degraded reason is spelled out: a status that
    says "degraded" and nothing else sends you to the browser anyway."""
    status = payload.get("status", "?")
    colour = "\033[32m" if status == "ok" else "\033[33m"
    index = payload.get("index", {})
    embeddings = index.get("embeddings", {})
    pairs = [
        ("status", paint(status, colour)),
        ("bind", str(payload.get("bind", "-"))),
        ("documents", str(index.get("documents", 0))),
        ("repos scanned", str(index.get("repos_scanned", 0))),
        ("last scan", relative_time(index.get("last_scan_at"))),
        (
            "embeddings",
            "available"
            if embeddings.get("available")
            else f"unavailable ({embeddings.get('reason') or 'no reason given'})",
        ),
    ]
    for name, reason in (payload.get("invalid_templates") or {}).items():
        pairs.append(("invalid template", f"{name}: {reason}"))
    if payload.get("invalid_policy"):
        pairs.append(("invalid policy", str(payload["invalid_policy"])))
    for error in index.get("errors") or []:
        pairs.append(("index error", str(error)))
    reattach = payload.get("reattach_summary") or {}
    if reattach.get("scanned"):
        pairs.append(
            (
                "reattached",
                f"{len(reattach.get('adopted', []))} adopted, "
                f"{len(reattach.get('resumed_work_items', []))} resumed "
                f"of {reattach['scanned']} scanned",
            )
        )
    if reattach.get("unknown"):
        pairs.append(("orphaned sessions", ", ".join(reattach["unknown"])))
    return kv(pairs)
```

`src/kraft/cli.py`:

```python
def _cmd_health(ns: argparse.Namespace) -> None:
    payload = asyncio.run(client.health())
    emit(payload, render.health_block, ns.json)
    if payload.get("status") != "ok":
        # exit 1 so `kraft health && ...` works; the reasons are already on stdout
        raise SystemExit(1)


def _render_reindex(result: dict) -> str:
    scope = result.get("repo") or "all repos"
    counts = ", ".join(f"{k} {v}" for k, v in result.get("stats", {}).items())
    return f"reindexed {scope}: {counts}"


def _cmd_reindex(ns: argparse.Namespace) -> None:
    emit(asyncio.run(client.reindex(ns.repo)), _render_reindex, ns.json)
```

Subparsers:

```python
    health = subs.add_parser("health", parents=[common], help="the server's own status")
    health.set_defaults(func=_cmd_health)

    reindex = subs.add_parser("reindex", parents=[common], help="rescan documents into the index")
    reindex.add_argument("--repo", help="one repo path (default: all)")
    reindex.set_defaults(func=_cmd_reindex)
```

> `_cmd_health` raises `SystemExit(1)` directly rather than `ValueError`: `main()`'s handler would prefix a `kraft:` message on stderr, and a degraded status is a result, not an error — its reasons belong on stdout where the test asserts them.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `just test tests/test_render.py tests/test_cli_admin.py`
Expected: PASS

- [ ] **Step 5: Run the whole suite**

Run: `just test`
Expected: PASS

- [ ] **Step 6: Lint and commit**

```bash
just lint
git add src/kraft/render.py src/kraft/cli.py tests/test_render.py tests/test_cli_admin.py
git commit -m "feat(cli): kraft health (exit 1 on degraded) and kraft reindex"
```

---

### Task 4: Document the service verbs

**Files:**
- Modify: `README.md`, `CLAUDE.md`, `AGENTS.md`

- [ ] **Step 1: Extend the README**

In "Running Kraft", after the `just install` line:

````markdown
`kraft serve` is the same as bare `kraft`; `--host` and `--port` override
`access.yaml` for one run (flag > `KRAFT_HOST`/`KRAFT_PORT` > `access.yaml`).
A non-loopback bind still refuses to start without a password, flag or not.

```bash
kraft health             # exit 1 when degraded, reasons on stdout
kraft reindex [--repo P] # rescan documents into the search index
```

There is no `kraft stop`: the server runs in the foreground, Ctrl-C stops it.
````

- [ ] **Step 2: Mirror into `CLAUDE.md` and `AGENTS.md`**

- [ ] **Step 3: Commit and close**

```bash
just lint
git add README.md CLAUDE.md AGENTS.md
git commit -m "docs: kraft serve, health and reindex"
bd close Kraft-bbg.5 --reason="serve/health/reindex shipped; doctor deferred to Kraft-bbg.6"
bd ready   # Kraft-bbg.6 (doctor) unblocks here
```

---

## Self-Review

**Spec coverage**

| Spec section | Task |
|---|---|
| §3 `kraft serve` (flags, same `_bind()`, password check preserved) | 1 |
| §3 `kraft health` (rendered, exit code) | 2, 3 |
| §3 `kraft doctor` — deferred | out of scope; `Kraft-bbg.6` |
| §3 `kraft reindex` (`--repo`, counts) | 2, 3 |
| §5 testing (`serve --port`, `--host 0.0.0.0` refusal, health exit code, reindex 404) | 1, 2, 3 |
| §6 decisions (doctor deferred; flags with documented precedence) | 1, 4 |

**Placeholder scan:** none.

**Type consistency:** `client.reindex(repo)` takes a path string and the CLI passes `ns.repo`; the 404 test uses a path the API cannot know. `render.health_block(payload)` consumes the `/health` shape verified in `tests/test_cli_admin.py::test_health_returns_the_status_block`. `_cmd_serve` sets env before `_serve()`, which reads it via `_bind()` — the same path `test_bare_kraft_and_kraft_serve_are_the_same_path` proves.
