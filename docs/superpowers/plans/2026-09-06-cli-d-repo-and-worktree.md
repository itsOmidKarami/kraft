# Kraft CLI Sub-project D — Repo and Worktree Context Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `kraft repos`, `kraft connect`, `kraft path` (alias `cd`) and `kraft open` — see what is connected, connect the cwd, and get from a work item to the checkout its agent is editing.

**Architecture:** Front doors onto `GET /repos`, the existing `client.ensure_repo()`, the `worktree_path` field `GET /work-items/{wid}` already returns, and `POST /work-items/{wid}/open-worktree`. Two small `client.py` additions (`repos()`, `open_worktree()`); one `worktree_path` read that `client.get_work_item` already forwards. No server changes, no destructive verbs.

**Tech Stack:** Python 3.14, `httpx`, argparse, pytest.

**Spec:** `docs/superpowers/specs/2026-09-06-cli-d-repo-and-worktree-design.md`

**Beads issue:** `Kraft-bbg.4`. Claim it before Task 1: `bd update Kraft-bbg.4 --claim`.

**Depends on:** sub-project A (`Kraft-bbg.1`) — `client.resolve_repo`, `client.ensure_repo`, `render.table`, `cli.emit`, `cli._render_action`.

## Global Constraints

- Python `>=3.14`, ruff `line-length = 100`, no new runtime dependencies.
- **No `disconnect`, no repo editing.** `DELETE /repos` and `PATCH /repos` stay UI-only (spec §2, §6). There is no destructive verb in this sub-project, and therefore no confirmation machinery.
- **No second `repos.yaml` reader.** `kraft repos` and `resolve_repo` both read `GET /repos`. Not parsing the YAML locally is a rule, not an oversight (spec §4).
- `kraft path` prints **exactly one line** — the path, no decoration, no trailing whitespace beyond the newline — because it is consumed by `cd "$(kraft path ID)"`. Tested strictly.
- `kraft connect` on an already-connected repo exits `0` and says so. Already connected is the goal state.
- Errors to stderr as `kraft: <message>`; exit `0`/`1`/`2` as in A.
- Commits stay local: no push, no merge to `main`, no `bd dolt push` unless asked.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/kraft/client.py` *(modify)* | `repos()`, `open_worktree()`. |
| `src/kraft/cli.py` *(modify)* | `_cmd_repos`, `_cmd_connect`, `_cmd_path`, `_cmd_open` and their subparsers. |
| `tests/test_cli_repos.py` *(create)* | Client functions and the four verbs. |

---

### Task 1: `client.repos()` and `client.open_worktree()`

**Files:**
- Modify: `src/kraft/client.py`
- Create: `tests/test_cli_repos.py`

**Interfaces:**
- Consumes: `client._get`, `client._act`, `client._target`.
- Produces:
  - `async def repos() -> list[dict]` — each `{path, name, default_chain_template, enabled, ...}` as `GET /repos` returns it.
  - `async def open_worktree(work_item_id: str | None = None, editor: str | None = None) -> dict`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cli_repos.py
"""repos, connect, path, open — the repo and worktree verbs."""

from __future__ import annotations

import asyncio
import json

import pytest
from support.harness import make_repo

from kraft import cli, client


# `app` fixture: tests/conftest.py (sub-project A Task 4). It wires client.http()
# to the ASGI app with the lifespan entered per client.


def _make_item(repo, title="locate me"):
    async def go():
        async with client.http() as http:
            response = await http.post(
                "/work-items", json={"title": title, "repo": str(repo), "autostart": False}
            )
        assert response.status_code == 201, response.text
        return response.json()["id"]

    return asyncio.run(go())


def test_repos_is_empty_until_something_is_connected(app):
    assert asyncio.run(client.repos()) == []


def test_repos_lists_a_connected_repo(app, tmp_path):
    repo = make_repo(tmp_path)
    asyncio.run(client.ensure_repo(str(repo)))
    listed = asyncio.run(client.repos())
    assert [entry["path"] for entry in listed] == [str(repo)]


def test_open_worktree_without_a_worktree_is_a_readable_404(app, tmp_path):
    repo = make_repo(tmp_path)
    wid = _make_item(repo)  # paused, never run: no worktree yet
    with pytest.raises(ValueError, match="404"):
        asyncio.run(client.open_worktree(wid))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `just test tests/test_cli_repos.py`
Expected: FAIL — `module 'kraft.client' has no attribute 'repos'`

- [ ] **Step 3: Write the implementation**

Add to `src/kraft/client.py`:

```python
async def repos() -> list[dict]:
    """Every connected repo, as the API shapes it.

    This and `resolve_repo` are the only readers of the repo list on the client
    side, and neither parses `repos.yaml`: a local YAML read would work with the
    server down, but would drift from the API's shaping (spec D §4).
    """
    return (await _get("/repos")).get("repos", [])


async def open_worktree(work_item_id: str | None = None, editor: str | None = None) -> dict:
    """Open the item's worktree in an editor on the server's machine — the same
    launch as the UI's "Open worktree", including its 501 when headless."""
    target = await _target(work_item_id)
    return await _act(f"/work-items/{target}/open-worktree", {"editor": editor} if editor else {})
```

`_target` is defined in sub-project B Task 2 (and duplicated verbatim in C Task 1 if B has not shipped). If neither has landed, add it here exactly as written there and note the duplication in the commit message.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `just test tests/test_cli_repos.py`
Expected: PASS (3 tests)

- [ ] **Step 5: Lint and commit**

```bash
just lint
git add src/kraft/client.py tests/test_cli_repos.py
git commit -m "feat(cli): client lists repos and opens a worktree"
```

---

### Task 2: `kraft repos` and `kraft connect`

**Files:**
- Modify: `src/kraft/cli.py`
- Modify: `tests/test_cli_repos.py`

**Interfaces:**
- Consumes: `client.repos`, `client.ensure_repo`, `client.resolve_repo`, `render.table`, `render.paint`, `cli.emit`, `cli._render_action`.
- Produces: `cli._cmd_repos(ns)`, `cli._cmd_connect(ns)`, `cli._render_repos(rows, here)`.

- [ ] **Step 1: Write the failing tests**

```python
def test_repos_marks_the_repo_you_are_standing_in(app, tmp_path, monkeypatch, capsys):
    here = make_repo(tmp_path, name="here")
    there = make_repo(tmp_path, name="there")
    asyncio.run(client.ensure_repo(str(here)))
    asyncio.run(client.ensure_repo(str(there)))
    monkeypatch.chdir(here)
    cli.main(["repos"])
    lines = capsys.readouterr().out.splitlines()
    marked = [line for line in lines if line.startswith("*")]
    assert len(marked) == 1
    assert str(here) in marked[0]


def test_repos_json_is_the_raw_list(app, tmp_path, capsys):
    repo = make_repo(tmp_path)
    asyncio.run(client.ensure_repo(str(repo)))
    cli.main(["repos", "--json"])
    assert json.loads(capsys.readouterr().out) == asyncio.run(client.repos())


def test_connect_defaults_to_the_cwd(app, tmp_path, monkeypatch, capsys):
    repo = make_repo(tmp_path)
    monkeypatch.chdir(repo)
    cli.main(["connect"])
    assert str(repo) in capsys.readouterr().out
    assert [entry["path"] for entry in asyncio.run(client.repos())] == [str(repo)]


def test_connect_twice_is_fine_and_says_so(app, tmp_path, capsys):
    repo = make_repo(tmp_path)
    cli.main(["connect", str(repo)])
    capsys.readouterr()
    cli.main(["connect", str(repo)])  # must not raise SystemExit
    assert "already connected" in capsys.readouterr().out


def test_connect_a_non_git_directory_surfaces_the_api_error(app, tmp_path, capsys):
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(SystemExit) as caught:
        cli.main(["connect", str(plain)])
    assert caught.value.code == 1
    assert "not a git repository" in capsys.readouterr().err
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `just test tests/test_cli_repos.py`
Expected: FAIL — argparse rejects `repos`.

- [ ] **Step 3: Write the implementation**

`src/kraft/cli.py`:

```python
_REPO_COLUMNS = [("", "here"), ("NAME", "name"), ("CHAIN", "default_chain_template"), ("PATH", "path")]


def _render_repos(rows: list[dict], here: str | None) -> str:
    """The cwd's repo gets a `*` in the first column: it is the answer to "why
    did my last command pick that repo"."""
    shaped = [
        {
            **row,
            "here": "*" if row["path"] == here else " ",
            "name": row["name"] if row.get("enabled", True) else render.paint(row["name"], render.DIM),
        }
        for row in rows
    ]
    return render.table(shaped, _REPO_COLUMNS)


def _cmd_repos(ns: argparse.Namespace) -> None:
    async def run():
        return await client.repos(), await client.resolve_repo()

    rows, here = asyncio.run(run())
    emit(rows, lambda value: _render_repos(value, here), ns.json)


def _cmd_connect(ns: argparse.Namespace) -> None:
    result = asyncio.run(client.ensure_repo(ns.path))
    if ns.json:
        emit(result, str, True)
        return
    verb = "already connected" if result.get("already_connected") else "connected"
    print(f"{verb}: {result['path']}")
```

Subparsers:

```python
    repos = subs.add_parser("repos", parents=[common], help="connected repositories")
    repos.set_defaults(func=_cmd_repos)

    connect = subs.add_parser("connect", parents=[common], help="connect a repo (idempotent)")
    connect.add_argument("path", nargs="?", help="default: the current directory")
    connect.set_defaults(func=_cmd_connect)
```

> `render.table` right-strips each line, so a `" "` in the first column of a non-cwd row leaves the row starting with a space — the test only checks rows that start with `*`. If the table's first-column alignment looks off in practice, swap `" "` for `"-"`; the test does not depend on it.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `just test tests/test_cli_repos.py`
Expected: PASS

- [ ] **Step 5: Lint and commit**

```bash
just lint
git add src/kraft/cli.py tests/test_cli_repos.py
git commit -m "feat(cli): kraft repos (cwd marked) and kraft connect (idempotent)"
```

---

### Task 3: `kraft path` (alias `cd`) and `kraft open`

**Files:**
- Modify: `src/kraft/cli.py`
- Modify: `tests/test_cli_repos.py`

**Interfaces:**
- Consumes: `client.get_work_item` (forwards `worktree_path`), `client.open_worktree`, `cli.emit`, `cli._render_action`.
- Produces: `cli._cmd_path(ns)`, `cli._cmd_open(ns)`, `cli.SHELL_WRAPPER: str`.

- [ ] **Step 1: Write the failing tests**

```python
def test_path_prints_exactly_one_line(app, tmp_path, capsys):
    repo = make_repo(tmp_path)
    wid = _make_item(repo)
    cli.main(["path", wid])
    out = capsys.readouterr().out
    # consumed by cd "$(kraft path ID)": one line, no decoration, nothing else
    assert out.endswith("\n")
    assert out.count("\n") == 1
    assert out.strip() == asyncio.run(client.get_work_item(wid))["worktree_path"]


def test_cd_is_an_alias_for_path(app, tmp_path, capsys):
    repo = make_repo(tmp_path)
    wid = _make_item(repo)
    cli.main(["path", wid])
    expected = capsys.readouterr().out
    cli.main(["cd", wid])
    assert capsys.readouterr().out == expected


def test_path_defaults_to_the_resolved_work_item(app, tmp_path, monkeypatch, capsys):
    repo = make_repo(tmp_path)
    wid = _make_item(repo)
    monkeypatch.setenv("KRAFT_WORK_ITEM_ID", wid)
    cli.main(["path"])
    assert wid in capsys.readouterr().out


def test_path_shell_prints_a_function(capsys):
    cli.main(["path", "--shell"])
    out = capsys.readouterr().out
    assert "kcd()" in out or "function" in out
    assert "kraft path" in out


def test_open_on_a_headless_server_is_a_kraft_message(app, tmp_path, monkeypatch, capsys):
    repo = make_repo(tmp_path)
    wid = _make_item(repo)

    async def fake_open(work_item_id=None, editor=None):
        raise ValueError("kraft 501: no editor available on the server for default")

    monkeypatch.setattr(client, "open_worktree", fake_open)
    with pytest.raises(SystemExit) as caught:
        cli.main(["open", wid])
    assert caught.value.code == 1
    assert "501" in capsys.readouterr().err


def test_open_passes_the_editor_through(app, tmp_path, monkeypatch, capsys):
    repo = make_repo(tmp_path)
    wid = _make_item(repo)
    seen = {}

    async def fake_open(work_item_id=None, editor=None):
        seen.update(work_item_id=work_item_id, editor=editor)
        return {"path": "/wt", "editor": editor or "system"}

    monkeypatch.setattr(client, "open_worktree", fake_open)
    cli.main(["open", wid, "--editor", "zed"])
    assert seen == {"work_item_id": wid, "editor": "zed"}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `just test tests/test_cli_repos.py -k "path or cd or open"`
Expected: FAIL — argparse rejects `path`.

- [ ] **Step 3: Write the implementation**

`src/kraft/cli.py`:

```python
#: Printed by `kraft path --shell`. A subprocess cannot change its parent's
#: directory, so the real `cd` has to be a function in the user's shell.
SHELL_WRAPPER = """\
# Add to ~/.zshrc or ~/.bashrc:
kcd() { cd "$(kraft path "$@")" || return; }
"""


def _cmd_path(ns: argparse.Namespace) -> None:
    if ns.shell:
        print(SHELL_WRAPPER, end="")
        return
    item = asyncio.run(client.get_work_item(ns.id))
    # exactly one line: this is consumed by cd "$(kraft path ID)"
    print(item["worktree_path"])


def _cmd_open(ns: argparse.Namespace) -> None:
    emit(asyncio.run(client.open_worktree(ns.id, ns.editor)), _render_action, ns.json)
```

Subparsers:

```python
    path = subs.add_parser(
        "path", aliases=["cd"], parents=[common], help="print a work item's worktree path"
    )
    path.add_argument("id", nargs="?")
    path.add_argument("--shell", action="store_true", help="print a shell function that cds")
    path.set_defaults(func=_cmd_path)

    open_p = subs.add_parser("open", parents=[common], help="open the worktree in an editor")
    open_p.add_argument("id", nargs="?")
    open_p.add_argument("--editor", help="code, cursor, zed, obsidian (default: system)")
    open_p.set_defaults(func=_cmd_open)
```

> `--json` on `path` is accepted from the shared parent but ignored: the contract is one bare line, and a JSON-wrapped path would break `$(...)`. `--json` on `path` should print the same bare line — do not special-case it.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `just test tests/test_cli_repos.py`
Expected: PASS

- [ ] **Step 5: Run the whole suite**

Run: `just test`
Expected: PASS

- [ ] **Step 6: Lint and commit**

```bash
just lint
git add src/kraft/cli.py tests/test_cli_repos.py
git commit -m "feat(cli): kraft path (alias cd) and kraft open"
```

---

### Task 4: Document the repo verbs

**Files:**
- Modify: `README.md`, `CLAUDE.md`, `AGENTS.md`

- [ ] **Step 1: Extend the README's CLI section**

````markdown
Repos and worktrees:

```bash
kraft repos              # what is connected; `*` marks the one you are in
kraft connect            # connect the current repo (safe to repeat)
cd "$(kraft path <id>)"  # into the item's worktree; `kraft cd` is an alias
kraft path --shell       # a shell function that does the cd for you
kraft open <id>          # the worktree in an editor
```

Disconnecting and editing a repo's settings stay in the UI.
````

- [ ] **Step 2: Mirror into `CLAUDE.md` and `AGENTS.md`**

- [ ] **Step 3: Commit and close**

```bash
just lint
git add README.md CLAUDE.md AGENTS.md
git commit -m "docs: the kraft repo and worktree verbs"
bd close Kraft-bbg.4 --reason="repos/connect/path(cd)/open shipped; no disconnect by design"
```

---

## Self-Review

**Spec coverage**

| Spec section | Task |
|---|---|
| §3 `kraft repos` (cwd marked) | 1, 2 |
| §3 `kraft connect` (idempotent, cwd default, 400 surfaced) | 2 |
| §3 `kraft path` / `cd` (one line, `--shell`) | 3 |
| §3 `kraft open` (501 as message, editor passthrough) | 1, 3 |
| §4 no second repos reader | 1 (docstring), constraints |
| §5 testing (strict one-line `path`, `connect` twice, non-git 400, headless 501) | 2, 3 |
| §6 decisions (`path`+alias; no `disconnect`) | 3, constraints |

**Placeholder scan:** none. The `_target` note in Task 1 points at the exact definition in B/C.

**Type consistency:** `client.open_worktree(work_item_id, editor)` is defined in Task 1 and called positionally in Task 3 with `(ns.id, ns.editor)`; the fake in the tests has the same signature. `_render_repos(rows, here)` takes `here` as the resolved repo string, matching `client.resolve_repo() -> str | None`.
