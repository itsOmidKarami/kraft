# Kraft CLI Sub-project C — Reviewing Work Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `kraft diff`, `kraft docs` and `kraft doc` — read what an agent changed and what it wrote, so `kraft approve` is an informed decision instead of a blind one.

**Architecture:** Three plain request/response functions in `client.py` over endpoints that already exist (`GET /work-items/{wid}/diff`, `GET /work-items/{wid}/documents`, `GET /documents/{id}`, `POST /documents/{id}/open`). `render.py` gains a diff-stat block, a colourised diff, and a pager. No streaming, no server changes.

**Tech Stack:** Python 3.14, stdlib `subprocess`/`shutil`, `httpx`, argparse, pytest.

**Spec:** `docs/superpowers/specs/2026-09-06-cli-c-reviewing-work-design.md`

**Beads issue:** `Kraft-bbg.3`. Claim it before Task 1: `bd update Kraft-bbg.3 --claim`.

**Depends on:** sub-project A (`Kraft-bbg.1`) — `render.py`, `cli.build_parser`, `cli.emit`, `client._get`, `client._post`, `client._act`.

## Global Constraints

- Python `>=3.14`, ruff `line-length = 100`, no new runtime dependencies.
- **`kraft diff` goes through the API, never `git -C <worktree>`** (spec §6.1). The worktree being local is incidental; the API is the one data path, and it works against a Kraft on another machine.
- **A truncated diff must say so.** `truncated: true` from the API produces a visible final line naming `DIFF_MAX_BYTES` (`api.py:54`, currently `1_000_000`) and the worktree path. This is a tested requirement, asserted on rendered output, not on the payload. A silently partial diff at an approval gate is the worst failure this CLI could have.
- **Untracked files are listed separately.** Mid-chain, an agent writing a file without `git add` is normal; those files are invisible in a unified diff.
- `base_ref: null` prints "no baseline recorded for this work item", never an empty diff. Empty and unknown are different answers.
- Paging only when stdout is a tty and `--no-pager` is absent. Never under pytest.
- No interactive prompts. No `approve --review` (spec §6.2).
- Errors to stderr as `kraft: <message>`; exit `0`/`1`/`2` as in A.
- Commits stay local: no push, no merge to `main`, no `bd dolt push` unless asked.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/kraft/client.py` *(modify)* | `diff()`, `documents()`, `document()`, `open_document()`. |
| `src/kraft/render.py` *(modify)* | `diff_stat()`, `diff_body()`, `page()`. |
| `src/kraft/cli.py` *(modify)* | `_cmd_diff`, `_cmd_docs`, `_cmd_doc` and their subparsers. |
| `tests/test_cli_reviewing.py` *(create)* | Client functions and verbs; the truncation and untracked warnings. |
| `tests/test_render.py` *(modify)* | `diff_stat`, `diff_body`, `page` in isolation. |

---

### Task 1: Client reads — `diff()`, `documents()`, `document()`, `open_document()`

**Files:**
- Modify: `src/kraft/client.py`
- Create: `tests/test_cli_reviewing.py`

**Interfaces:**
- Consumes: `client._get`, `client._act`, `client._target` (from B Task 2 — if B has not shipped, define `_target` here exactly as B does; the two must be identical, so copy it verbatim and note the duplication in the commit message so whichever lands second deletes one copy).
- Produces:
  - `async def diff(work_item_id: str | None = None) -> dict` — the API payload unchanged: `{work_item_id, base_ref, files, diff, untracked, truncated}`.
  - `async def documents(work_item_id: str | None = None) -> list[dict]`
  - `async def document(doc_id: str) -> dict`
  - `async def open_document(doc_id: str, editor: str | None = None) -> dict`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cli_reviewing.py
"""diff, docs, doc — reading what an agent did before approving it."""

from __future__ import annotations

import asyncio
import json

import pytest
from support.harness import make_repo

from kraft import cli, client


# `app` fixture: tests/conftest.py (sub-project A Task 4). It wires client.http()
# to the ASGI app with the lifespan entered per client.


def _make_item(repo, title="review me"):
    async def go():
        async with client.http() as http:
            response = await http.post(
                "/work-items", json={"title": title, "repo": str(repo), "autostart": False}
            )
        assert response.status_code == 201, response.text
        return response.json()["id"]

    return asyncio.run(go())


def test_diff_on_an_item_with_no_baseline_is_explicit(app, tmp_path):
    repo = make_repo(tmp_path)
    wid = _make_item(repo)
    payload = asyncio.run(client.diff(wid))
    # a paused, never-run item has no env_setup node stamped yet
    assert payload["base_ref"] is None
    assert payload["diff"] == ""
    assert payload["truncated"] is False


def test_diff_defaults_to_the_resolved_work_item(app, tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    wid = _make_item(repo)
    monkeypatch.setenv("KRAFT_WORK_ITEM_ID", wid)
    assert asyncio.run(client.diff())["work_item_id"] == wid


def test_documents_is_empty_for_a_fresh_item(app, tmp_path):
    repo = make_repo(tmp_path)
    wid = _make_item(repo)
    assert asyncio.run(client.documents(wid)) == []


def test_document_404_is_a_readable_message(app):
    with pytest.raises(ValueError, match="404"):
        asyncio.run(client.document("no-such-doc"))


def test_open_document_404_is_a_readable_message(app):
    with pytest.raises(ValueError, match="404"):
        asyncio.run(client.open_document("no-such-doc"))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `just test tests/test_cli_reviewing.py`
Expected: FAIL — `module 'kraft.client' has no attribute 'diff'`

- [ ] **Step 3: Write the implementation**

Add to `src/kraft/client.py`:

```python
async def diff(work_item_id: str | None = None) -> dict:
    """What the agent changed, against the item's `base_ref`.

    The payload is passed through untouched — `truncated` and `untracked` are
    the two fields a renderer must not drop, and passing the dict whole is how
    that is guaranteed rather than remembered.
    """
    return await _get(f"/work-items/{await _target(work_item_id)}/diff")


async def documents(work_item_id: str | None = None) -> list[dict]:
    """The specs, plans and summaries the indexer linked to this item. No
    content: that is one `document()` call per id."""
    payload = await _get(f"/work-items/{await _target(work_item_id)}/documents")
    return payload.get("documents", [])


async def document(doc_id: str) -> dict:
    return await _get(f"/documents/{doc_id}")


async def open_document(doc_id: str, editor: str | None = None) -> dict:
    """Hand the document to an editor on the server's machine.

    Reuses the server's editor table and its 501-when-headless answer rather
    than growing a second launcher here.
    """
    return await _act(f"/documents/{doc_id}/open", {"editor": editor} if editor else {})
```

If `_target` does not yet exist in `client.py` (sub-project B not merged), add it above these:

```python
async def _target(work_item_id: str | None) -> str:
    """An explicit id, or the item this session is standing in.

    Not `_forbid_self_action`: reading your own diff is exactly what a worker
    session should be able to do. The guard is about acting, not looking.
    """
    if work_item_id:
        return work_item_id
    resolved, _origin = resolve_context()
    if resolved is None:
        raise ValueError("no work item: pass an id, or run from a Kraft worktree")
    return resolved
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `just test tests/test_cli_reviewing.py`
Expected: PASS (5 tests)

- [ ] **Step 5: Lint and commit**

```bash
just lint
git add src/kraft/client.py tests/test_cli_reviewing.py
git commit -m "feat(cli): client reads diff and linked documents"
```

---

### Task 2: `render.diff_stat()`, `render.diff_body()`, `render.page()`

**Files:**
- Modify: `src/kraft/render.py`
- Modify: `tests/test_render.py`

**Interfaces:**
- Consumes: `render.paint`, `render.use_color`, `render.table`.
- Produces:
  - `diff_stat(payload: dict) -> str` — one line per file plus a totals line; always ends with the untracked list (if any) and the truncation warning (if set).
  - `diff_body(payload: dict) -> str` — the coloured unified diff, then the untracked list, then the truncation warning. Same trailer as `diff_stat`, from one shared helper.
  - `page(text: str, *, force_plain: bool = False) -> None` — writes through `$PAGER` on a tty, else to stdout.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_render.py`:

```python
_DIFF = {
    "work_item_id": "Kraft-x",
    "base_ref": "abc123",
    "files": [
        {"path": "a.py", "insertions": 3, "deletions": 1},
        {"path": "b.py", "insertions": 0, "deletions": 7},
    ],
    "diff": "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1,3 @@\n-old\n+new\n+more\n",
    "untracked": ["notes.md"],
    "truncated": False,
}


def test_diff_stat_lists_files_and_totals():
    out = render.diff_stat(_DIFF)
    assert "a.py" in out and "b.py" in out
    assert "+3" in out and "-7" in out
    assert "2 files" in out


def test_diff_stat_and_body_both_list_untracked_files():
    for renderer in (render.diff_stat, render.diff_body):
        out = renderer(_DIFF)
        assert "untracked" in out.lower()
        assert "notes.md" in out


def test_truncation_is_never_silent():
    """The single most important assertion in this sub-project."""
    cut = {**_DIFF, "truncated": True}
    for renderer in (render.diff_stat, render.diff_body):
        out = renderer(cut)
        assert "truncated" in out.lower()
        assert out.rstrip().splitlines()[-1].lower().startswith("warning")


def test_no_baseline_is_not_an_empty_diff():
    none = {**_DIFF, "base_ref": None, "files": [], "diff": "", "untracked": []}
    assert "no baseline" in render.diff_body(none)
    assert "no baseline" in render.diff_stat(none)


def test_diff_body_colours_only_on_a_tty(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    assert "\033[" not in render.diff_body(_DIFF)


def test_page_writes_plainly_when_not_a_tty(capsys):
    render.page("hello")
    assert capsys.readouterr().out == "hello\n"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `just test tests/test_render.py -k "diff or page or baseline or truncation"`
Expected: FAIL — `render` has no `diff_stat`.

- [ ] **Step 3: Write the implementation**

Add to `src/kraft/render.py` (`subprocess` joins the imports):

```python
_DIFF_LINE_COLORS = (
    ("+++", "\033[1m"),
    ("---", "\033[1m"),
    ("+", "\033[32m"),
    ("-", "\033[31m"),
    ("@@", "\033[36m"),
    ("diff --git", "\033[1m"),
)


def _diff_trailer(payload: dict) -> list[str]:
    """What must follow every diff view, stat or full: files git cannot see, and
    the warning that the diff you just read was not all of it.

    One helper for both renderers so a future third view cannot forget either.
    """
    lines: list[str] = []
    if payload.get("untracked"):
        lines.append("")
        lines.append(paint("untracked (not in the diff above):", DIM))
        lines.extend(f"  {path}" for path in payload["untracked"])
    if payload.get("truncated"):
        lines.append("")
        lines.append(
            paint(
                "WARNING: diff truncated by the server at its size limit — "
                "this is not the whole change. Read the rest in the worktree.",
                "\033[33m",
            )
        )
    return lines


def diff_stat(payload: dict) -> str:
    """"How big is this" — the question asked before deciding to read it."""
    if payload.get("base_ref") is None:
        return "no baseline recorded for this work item"
    files = payload.get("files", [])
    rows = [
        {
            "path": f["path"],
            "ins": paint(f"+{f['insertions']}", "\033[32m"),
            "del": paint(f"-{f['deletions']}", "\033[31m"),
        }
        for f in files
    ]
    body = table(rows, [("PATH", "path"), ("", "ins"), ("", "del")]) if rows else "(no changes)"
    total_ins = sum(f["insertions"] for f in files)
    total_del = sum(f["deletions"] for f in files)
    summary = f"{len(files)} files, +{total_ins} -{total_del}"
    return "\n".join([body, summary, *_diff_trailer(payload)])


def diff_body(payload: dict) -> str:
    """The unified diff, coloured the way `git diff` colours it."""
    if payload.get("base_ref") is None:
        return "no baseline recorded for this work item"
    out: list[str] = []
    for line in payload.get("diff", "").splitlines():
        code = next((c for prefix, c in _DIFF_LINE_COLORS if line.startswith(prefix)), "")
        out.append(paint(line, code))
    if not out:
        out.append("(no changes)")
    return "\n".join([*out, *_diff_trailer(payload)])


def page(text: str, *, force_plain: bool = False) -> None:
    """Through `$PAGER` on a terminal; straight to stdout otherwise.

    `less -R` is the fallback because it passes the colour codes through; a
    pager that does not would show escape garbage.
    """
    if force_plain or not use_color():
        print(text)
        return
    pager = os.environ.get("PAGER") or ("less -R" if shutil.which("less") else "")
    if not pager:
        print(text)
        return
    try:
        subprocess.run(pager, input=text.encode(), shell=True, check=False)
    except OSError:
        print(text)
```

> `use_color()` doubles as the tty check on purpose: it is already "stdout is a terminal and the user did not opt out", which is exactly when paging is wanted.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `just test tests/test_render.py`
Expected: PASS

- [ ] **Step 5: Lint and commit**

```bash
just lint
git add src/kraft/render.py tests/test_render.py
git commit -m "feat(cli): diff stat/body renderers with untracked and truncation trailers, and a pager"
```

---

### Task 3: `kraft diff`

**Files:**
- Modify: `src/kraft/cli.py`
- Modify: `tests/test_cli_reviewing.py`

**Interfaces:**
- Consumes: `client.diff`, `render.diff_stat`, `render.diff_body`, `render.page`, `cli.emit`.
- Produces: `cli._cmd_diff(ns)`.

- [ ] **Step 1: Write the failing tests**

```python
def test_diff_no_baseline_prints_the_explicit_line(app, tmp_path, capsys):
    repo = make_repo(tmp_path)
    wid = _make_item(repo)
    cli.main(["diff", wid])
    assert "no baseline" in capsys.readouterr().out


def test_diff_stat_and_truncation_reach_stdout(app, tmp_path, monkeypatch, capsys):
    repo = make_repo(tmp_path)
    wid = _make_item(repo)

    async def fake_diff(work_item_id=None):
        return {
            "work_item_id": wid,
            "base_ref": "abc",
            "files": [{"path": "x.py", "insertions": 1, "deletions": 0}],
            "diff": "diff --git a/x.py b/x.py\n+hi\n",
            "untracked": ["new.txt"],
            "truncated": True,
        }

    monkeypatch.setattr(client, "diff", fake_diff)
    cli.main(["diff", wid, "--stat"])
    out = capsys.readouterr().out
    assert "x.py" in out
    assert "new.txt" in out
    assert "truncated" in out.lower()  # asserted on the rendered output, not the payload


def test_diff_name_only_prints_paths_one_per_line(app, tmp_path, monkeypatch, capsys):
    repo = make_repo(tmp_path)
    wid = _make_item(repo)

    async def fake_diff(work_item_id=None):
        return {
            "work_item_id": wid,
            "base_ref": "abc",
            "files": [
                {"path": "a.py", "insertions": 1, "deletions": 0},
                {"path": "b.py", "insertions": 1, "deletions": 0},
            ],
            "diff": "",
            "untracked": ["c.py"],
            "truncated": False,
        }

    monkeypatch.setattr(client, "diff", fake_diff)
    cli.main(["diff", wid, "--name-only"])
    # untracked paths are included: they are files the agent touched
    assert capsys.readouterr().out.split() == ["a.py", "b.py", "c.py"]


def test_diff_json_is_the_raw_payload(app, tmp_path, capsys):
    repo = make_repo(tmp_path)
    wid = _make_item(repo)
    cli.main(["diff", wid, "--json"])
    printed = json.loads(capsys.readouterr().out)
    assert printed == asyncio.run(client.diff(wid))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `just test tests/test_cli_reviewing.py -k diff`
Expected: FAIL — argparse rejects `diff`.

- [ ] **Step 3: Write the implementation**

`src/kraft/cli.py`:

```python
def _cmd_diff(ns: argparse.Namespace) -> None:
    payload = asyncio.run(client.diff(ns.id))
    if ns.json:
        emit(payload, str, True)
        return
    if ns.name_only:
        paths = [f["path"] for f in payload.get("files", [])] + list(payload.get("untracked", []))
        print("\n".join(paths))
        return
    text = render.diff_stat(payload) if ns.stat else render.diff_body(payload)
    render.page(text, force_plain=ns.no_pager)
```

Subparser:

```python
    diff = subs.add_parser("diff", parents=[common], help="what the agent changed")
    diff.add_argument("id", nargs="?")
    diff.add_argument("--stat", action="store_true", help="per-file counts only")
    diff.add_argument("--name-only", action="store_true", help="changed and untracked paths")
    diff.add_argument("--no-pager", action="store_true")
    diff.set_defaults(func=_cmd_diff)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `just test tests/test_cli_reviewing.py`
Expected: PASS

- [ ] **Step 5: Lint and commit**

```bash
just lint
git add src/kraft/cli.py tests/test_cli_reviewing.py
git commit -m "feat(cli): kraft diff — stat, name-only, paged body; truncation never silent"
```

---

### Task 4: `kraft docs` and `kraft doc`

**Files:**
- Modify: `src/kraft/cli.py`
- Modify: `tests/test_cli_reviewing.py`

**Interfaces:**
- Consumes: `client.documents`, `client.document`, `client.open_document`, `render.table`, `render.page`, `cli.emit`.
- Produces: `cli._cmd_docs(ns)`, `cli._cmd_doc(ns)`, `cli._render_docs(rows)`.

- [ ] **Step 1: Write the failing tests**

```python
def test_docs_lists_linked_documents(app, tmp_path, monkeypatch, capsys):
    repo = make_repo(tmp_path)
    wid = _make_item(repo)

    async def fake_documents(work_item_id=None):
        return [
            {
                "document_id": "doc-1",
                "kind": "spec",
                "title": "A spec",
                "path": "docs/a.md",
                "repo": str(repo),
            }
        ]

    monkeypatch.setattr(client, "documents", fake_documents)
    cli.main(["docs", wid])
    out = capsys.readouterr().out
    assert "doc-1" in out and "spec" in out and "docs/a.md" in out


def test_docs_empty_says_nothing_rather_than_crashing(app, tmp_path, capsys):
    repo = make_repo(tmp_path)
    wid = _make_item(repo)
    cli.main(["docs", wid])
    assert "(nothing)" in capsys.readouterr().out


def test_doc_prints_content(app, monkeypatch, capsys):
    async def fake_document(doc_id):
        return {"id": doc_id, "title": "A spec", "path": "docs/a.md", "content": "# Hello\n"}

    monkeypatch.setattr(client, "document", fake_document)
    cli.main(["doc", "doc-1"])
    assert "# Hello" in capsys.readouterr().out


def test_doc_open_hands_off_to_the_server(app, monkeypatch, capsys):
    seen = {}

    async def fake_open(doc_id, editor=None):
        seen.update(doc_id=doc_id, editor=editor)
        return {"document_id": doc_id, "path": "/abs/docs/a.md", "editor": editor or "system"}

    monkeypatch.setattr(client, "open_document", fake_open)
    cli.main(["doc", "doc-1", "--open", "code"])
    assert seen == {"doc_id": "doc-1", "editor": "code"}
    assert "/abs/docs/a.md" in capsys.readouterr().out


def test_doc_open_on_a_headless_server_is_a_kraft_message(app, capsys, monkeypatch):
    async def fake_open(doc_id, editor=None):
        raise ValueError("kraft 501: no editor available on the server for default")

    monkeypatch.setattr(client, "open_document", fake_open)
    with pytest.raises(SystemExit) as caught:
        cli.main(["doc", "doc-1", "--open"])
    assert caught.value.code == 1
    assert "501" in capsys.readouterr().err
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `just test tests/test_cli_reviewing.py -k doc`
Expected: FAIL — argparse rejects `docs` and `doc`.

- [ ] **Step 3: Write the implementation**

`src/kraft/cli.py`:

```python
_DOC_COLUMNS = [("ID", "document_id"), ("KIND", "kind"), ("TITLE", "title"), ("PATH", "path")]


def _render_docs(rows: list[dict]) -> str:
    return render.table(rows, _DOC_COLUMNS)


def _cmd_docs(ns: argparse.Namespace) -> None:
    emit(asyncio.run(client.documents(ns.id)), _render_docs, ns.json)


def _cmd_doc(ns: argparse.Namespace) -> None:
    if ns.open is not None:
        editor = ns.open or None  # `--open` alone means the server's default
        result = asyncio.run(client.open_document(ns.doc_id, editor))
        emit(result, _render_action, ns.json)
        return
    doc = asyncio.run(client.document(ns.doc_id))
    if ns.json:
        emit(doc, str, True)
        return
    render.page(doc.get("content", ""), force_plain=ns.no_pager)
```

Subparsers:

```python
    docs = subs.add_parser("docs", parents=[common], help="documents linked to a work item")
    docs.add_argument("id", nargs="?")
    docs.set_defaults(func=_cmd_docs)

    doc = subs.add_parser("doc", parents=[common], help="print one document, or open it")
    doc.add_argument("doc_id")
    doc.add_argument(
        "--open",
        nargs="?",
        const="",
        metavar="EDITOR",
        help="open in an editor on the server (code, cursor, zed, obsidian; default: system)",
    )
    doc.add_argument("--no-pager", action="store_true")
    doc.set_defaults(func=_cmd_doc)
```

`_render_action` comes from sub-project A Task 6.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `just test tests/test_cli_reviewing.py`
Expected: PASS

- [ ] **Step 5: Run the whole suite**

Run: `just test`
Expected: PASS

- [ ] **Step 6: Lint and commit**

```bash
just lint
git add src/kraft/cli.py tests/test_cli_reviewing.py
git commit -m "feat(cli): kraft docs and kraft doc, with --open via the server's editor table"
```

---

### Task 5: Document the reviewing verbs

**Files:**
- Modify: `README.md`, `CLAUDE.md`, `AGENTS.md`

- [ ] **Step 1: Extend the README's CLI section**

````markdown
Reviewing before you approve:

```bash
kraft diff --stat        # how big is it
kraft diff               # the whole change, paged; untracked files listed after
kraft docs               # the spec, plan and summaries linked to the item
kraft doc <id>           # print one; `--open code` hands it to an editor
```

A diff the server had to truncate ends with a warning line. Read the rest in
the worktree (`kraft path`) before approving.
````

- [ ] **Step 2: Mirror into `CLAUDE.md` and `AGENTS.md`**

- [ ] **Step 3: Commit and close**

```bash
just lint
git add README.md CLAUDE.md AGENTS.md
git commit -m "docs: the kraft reviewing verbs"
bd close Kraft-bbg.3 --reason="diff/docs/doc shipped; truncation and untracked are tested trailers"
```

---

## Self-Review

**Spec coverage**

| Spec section | Task |
|---|---|
| §3 `kraft diff` (`--stat`, `--name-only`, `--no-pager`, truncation, untracked, null base_ref) | 1, 2, 3 |
| §3 `kraft docs` | 1, 4 |
| §3 `kraft doc` (`--open` via `POST /documents/{id}/open`) | 1, 4 |
| §4 paging (`$PAGER`, `less -R`, never non-tty) | 2 |
| §5 testing (truncation on rendered output, untracked, null baseline, 501 as message, no pty needed) | 2, 3, 4 |
| §6 decisions (API not git; no `--review`) | constraints, 3 |

**Placeholder scan:** none. Task 1 names the `_target` overlap with sub-project B and gives the exact code for whichever lands first.

**Type consistency:** `client.diff` returns the raw dict and every renderer takes that dict. `render.page(text, *, force_plain)` is called with `force_plain=ns.no_pager` in Tasks 3 and 4. `_render_docs` uses `document_id` as the key because that is what `documents_for_work_item` returns (`index/service.py`), while `client.document()` returns `id` — the two payloads differ on the wire and the columns follow the wire.
