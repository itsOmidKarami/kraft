# CLI Command Groups and Server Stop — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move `kraft`'s 28 flat verbs into four command groups (`item`, `view`, `repo`, `admin`), rename `serve` to `start`, and add `kraft admin stop`.

**Architecture:** Nested `argparse` subparsers — one `add_subparsers` per group, built by four functions that replace `_add_verbs`. Every `_cmd_*` handler, renderer and `emit()` is unchanged; only parser construction and pre-argparse dispatch move. A `MOVED` dict turns each old verb into an exit-2 error naming its new path. `stop` is a `SIGTERM` to the pid in `run/kraft.pid`, written by `_serve()`.

**Tech Stack:** Python 3, argparse, uvicorn, pytest.

**Spec:** `docs/superpowers/specs/2026-09-08-cli-command-groups-design.md`

## Global Constraints

- No aliases and no compatibility shims. One name per command; `MOVED` is data that produces an error, never a dispatch.
- `--json` payloads, every renderer, and every `_cmd_*` handler's behaviour are unchanged. The CLI and the MCP door must keep returning identical values (`cli.emit`'s contract).
- Bare `kraft` with no arguments still serves. That check stays before argparse in `main()`.
- `_bind()`'s refusal to bind a non-loopback address without a password must keep firing. The pidfile is written *after* `_bind()` returns.
- The pidfile lives under the run dir of the active `KRAFT_HOME`, so a `just dev` instance and an installed one can run at once.
- POSIX only for `stop` (`os.kill`, `signal.SIGTERM`). No daemonizing, no `--detach`, no log redirection.
- Run tests with the named files, not the full suite: `pytest tests/test_cli_verbs.py -q` and friends. The whole suite is ~14 minutes.

---

### Task 1: The four group parsers

Splits `_add_verbs` into four group builders and nests them under group parsers. After this task the new tree works and the old flat verbs are gone (they error with argparse's own message; Task 2 makes that message useful).

**Files:**
- Modify: `src/kraft/cli.py:172-197` (`build_parser`), `src/kraft/cli.py:527-681` (`_add_verbs`)
- Test: `tests/test_cli_verbs.py`, `tests/test_cli_repos.py`, `tests/test_cli_watching.py`, `tests/test_cli_doctor.py`, `tests/test_cli_admin.py`, `tests/test_cli_reviewing.py`, `tests/test_gates.py`, `tests/test_ws.py`

**Interfaces:**
- Produces: `cli._add_item(subs, common)`, `cli._add_view(subs, common)`, `cli._add_repo(subs, common)`, `cli._add_admin(subs, common)` — each takes the group's `_SubParsersAction` and the shared `--json` parent parser from `_json_flag()`, returns `None`. `cli._add_verbs` is deleted.

- [ ] **Step 1: Write the failing tests for the group tree**

Add to `tests/test_cli_verbs.py`:

```python
GROUPS = {
    "item": ["create", "approve", "reject", "pause", "resume", "retry", "abandon"],
    "view": ["list", "show", "search", "logs", "events", "watch", "diff", "docs", "doc", "artifact"],
    "repo": ["list", "connect", "disconnect", "path", "cd", "open"],
    "admin": ["start", "stop", "health", "doctor", "reindex", "init", "mcp"],
}


@pytest.mark.parametrize(
    "group,verb", [(g, v) for g, verbs in GROUPS.items() for v in verbs]
)
def test_every_grouped_verb_parses_to_a_handler(group, verb):
    """The tree is the interface. Each leaf must reach a callable."""
    args = [group, verb]
    if verb in ("search",):
        args.append("query")
    if verb == "doc":
        args.append("doc-1")
    if verb == "create":
        args.append("a title")
    if verb == "reject":
        args += ["--note", "why"]
    ns = cli.build_parser().parse_args(args)
    assert callable(ns.func)


@pytest.mark.parametrize("group", sorted(GROUPS))
def test_a_group_with_no_verb_is_a_usage_error(group, capsys):
    with pytest.raises(SystemExit) as caught:
        cli.main([group])
    assert caught.value.code == 2
```

- [ ] **Step 2: Run them to verify they fail**

Run: `pytest tests/test_cli_verbs.py -q -k "grouped_verb or group_with_no_verb"`
Expected: FAIL — argparse rejects `item` as an invalid choice (exit 2 for the parse tests too, but `build_parser().parse_args` raises before `ns.func` exists).

- [ ] **Step 3: Build the group parsers**

In `build_parser()`, replace the three inline `serve`/`mcp`/`init` parsers and the `_add_verbs(subs, common)` call with:

```python
    subs = parser.add_subparsers(dest="group", required=True)
    common = _json_flag()

    for name, help_text, adder in (
        ("item", "act on a work item", _add_item),
        ("view", "read a work item, or the board", _add_view),
        ("repo", "connected repositories and their worktrees", _add_repo),
        ("admin", "this machine's server and its install", _add_admin),
    ):
        group = subs.add_parser(name, help=help_text)
        adder(group.add_subparsers(dest="verb", required=True), common)
    return parser
```

Then split the body of `_add_verbs` into `_add_item`, `_add_view`, `_add_repo` and `_add_admin`, moving each existing `subs.add_parser(...)` block into the group the spec assigns it, unchanged apart from the function it now lives in. `serve`, `mcp` and `init` move out of `build_parser()` into `_add_admin`, keeping their flags; `serve`'s parser string becomes `"start"` and its handler is renamed `_cmd_start`:

```python
    start = subs.add_parser("start", parents=[common], help="run the server (same as bare `kraft`)")
    start.add_argument("--host", help="bind address (default: access.yaml, or KRAFT_HOST)")
    start.add_argument("--port", type=int, help="port (default: access.yaml, or KRAFT_PORT)")
    start.set_defaults(func=_cmd_start)
```

`repos` becomes `repo list` — rename the parser string to `"list"`, keep `_cmd_repos`. `path` keeps its `aliases=["cd"]`, now under `repo`. Delete `_add_verbs`.

Leave `stop` out of `_add_admin` for now; Task 4 adds it. Remove `"stop"` from `GROUPS["admin"]` in the test until then, or write Task 4 first — the plan orders it after because `stop` needs Task 3's pidfile.

- [ ] **Step 4: Run the new tests**

Run: `pytest tests/test_cli_verbs.py -q -k "grouped_verb or group_with_no_verb"`
Expected: PASS.

- [ ] **Step 5: Move the existing tests onto the new argv**

Every `cli.main([...])` and `build_parser().parse_args([...])` in the seven test files listed above gains its group as the first element: `cli.main(["list"])` → `cli.main(["view", "list"])`, `cli.main(["approve", wid])` → `cli.main(["item", "approve", wid])`, `cli.main(["repos"])` → `cli.main(["repo", "list"])`. Mechanical; the assertions do not change.

Find them with:

```bash
grep -rn 'cli.main(\[\|parse_args(\[' tests/ | grep -v node_modules
```

- [ ] **Step 6: Run the CLI test files**

Run: `pytest tests/test_cli_verbs.py tests/test_cli_repos.py tests/test_cli_watching.py tests/test_cli_doctor.py tests/test_cli_admin.py tests/test_cli_reviewing.py tests/test_gates.py tests/test_ws.py -q`
Expected: PASS. `test_bare_kraft_still_serves` and `test_mcp_still_dispatches` must be among them — the latter now calls `cli.main(["admin", "mcp"])`.

- [ ] **Step 7: Commit**

```bash
git add src/kraft/cli.py tests/
git commit -m "refactor(cli): four command groups, serve becomes start"
```

---

### Task 2: The MOVED map

**Files:**
- Modify: `src/kraft/cli.py` (module level, and `main()` at `cli.py:687`)
- Test: `tests/test_cli_verbs.py`

**Interfaces:**
- Consumes: the group tree from Task 1.
- Produces: `cli.MOVED: dict[str, str]` — old verb to new space-separated path, e.g. `{"list": "view list"}`.

- [ ] **Step 1: Write the failing test**

```python
def test_every_moved_verb_names_its_new_path(capsys):
    """A removed verb must say where it went. argparse's own error does not."""
    for old, new in cli.MOVED.items():
        with pytest.raises(SystemExit) as caught:
            cli.main([old])
        assert caught.value.code == 2
        err = capsys.readouterr().err
        assert old in err and f"kraft {new}" in err


def test_moved_covers_every_verb_that_existed():
    """A verb dropped from MOVED is a verb that vanishes silently."""
    expected = {
        "create", "approve", "reject", "pause", "resume", "retry", "abandon",
        "list", "show", "search", "logs", "events", "watch", "diff", "docs",
        "doc", "artifact", "repos", "connect", "disconnect", "path", "cd",
        "open", "health", "doctor", "reindex", "init", "mcp", "serve",
    }
    assert set(cli.MOVED) == expected
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/test_cli_verbs.py -q -k moved`
Expected: FAIL with `AttributeError: module 'kraft.cli' has no attribute 'MOVED'`.

- [ ] **Step 3: Add the map and the intercept**

At module level in `cli.py`:

```python
#: Verbs that were top-level before the groups, and where each one went. Data,
#: not aliases: nothing here dispatches. argparse's own "invalid choice" prints
#: the four group names and says nothing about where `list` went, so the check
#: happens before argparse sees argv.
MOVED = {
    "create": "item create", "approve": "item approve", "reject": "item reject",
    "pause": "item pause", "resume": "item resume", "retry": "item retry",
    "abandon": "item abandon",
    "list": "view list", "show": "view show", "search": "view search",
    "logs": "view logs", "events": "view events", "watch": "view watch",
    "diff": "view diff", "docs": "view docs", "doc": "view doc",
    "artifact": "view artifact",
    "repos": "repo list", "connect": "repo connect", "disconnect": "repo disconnect",
    "path": "repo path", "cd": "repo cd", "open": "repo open",
    "serve": "admin start", "health": "admin health", "doctor": "admin doctor",
    "reindex": "admin reindex", "init": "admin init", "mcp": "admin mcp",
}
```

In `main()`, after the zero-argument check and before `build_parser()`:

```python
    if args[0] in MOVED:
        print(f"kraft: '{args[0]}' moved to `kraft {MOVED[args[0]]}`", file=sys.stderr)
        raise SystemExit(2)
```

- [ ] **Step 4: Run the tests**

Run: `pytest tests/test_cli_verbs.py -q -k moved`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/kraft/cli.py tests/test_cli_verbs.py
git commit -m "feat(cli): removed verbs name their new path"
```

---

### Task 3: The pidfile

**Files:**
- Modify: `src/kraft/paths.py:44-70` (`RunDirs`), `src/kraft/cli.py:70-76` (`_serve`)
- Test: `tests/test_cli_admin.py`

**Interfaces:**
- Produces: `paths.RunDirs.pid -> Path` (`base / "kraft.pid"`); `cli._read_pid(path: Path) -> int | None` — the live pid, or `None` when the file is absent, unparseable, or names a dead process, removing the file in that last case.

- [ ] **Step 1: Write the failing tests**

```python
import os
from kraft import cli
from kraft.paths import RunDirs


def test_serve_writes_and_clears_the_pidfile(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_HOME", str(tmp_path))
    seen = {}

    def fake_run(*args, **kwargs):
        seen["pid"] = RunDirs(tmp_path / "run").pid.read_text().strip()

    monkeypatch.setattr(cli.uvicorn, "run", fake_run)
    cli._serve()
    assert seen["pid"] == str(os.getpid())
    assert not RunDirs(tmp_path / "run").pid.exists()


def test_a_second_serve_refuses_while_one_is_live(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("KRAFT_HOME", str(tmp_path))
    pid_path = RunDirs(tmp_path / "run").pid
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text(str(os.getpid()))  # this process is certainly alive
    monkeypatch.setattr(cli.uvicorn, "run", lambda *a, **k: pytest.fail("started anyway"))
    with pytest.raises(SystemExit):
        cli._serve()
    assert "already running" in capsys.readouterr().err
    assert pid_path.exists()  # a live server's pidfile is not ours to delete


def test_a_stale_pidfile_does_not_block_serve(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_HOME", str(tmp_path))
    pid_path = RunDirs(tmp_path / "run").pid
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text("999999")  # no such process
    started = []
    monkeypatch.setattr(cli.uvicorn, "run", lambda *a, **k: started.append(True))
    cli._serve()
    assert started == [True]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `pytest tests/test_cli_admin.py -q -k "pidfile or serve"`
Expected: FAIL — `RunDirs` has no `pid`.

- [ ] **Step 3: Add `RunDirs.pid`**

In `paths.py`, beside the other properties:

```python
    @property
    def pid(self) -> Path:
        """The running server's pid, for `kraft admin stop`.

        Under the run dir rather than a fixed system path because it belongs to
        one KRAFT_HOME: a `just dev` instance and an installed one must be able
        to run at once.
        """
        return self.base / "kraft.pid"
```

- [ ] **Step 4: Write the pidfile in `_serve`**

```python
def _read_pid(path: Path) -> int | None:
    """The live pid in `path`, or None — clearing the file if it is stale.

    A pidfile that outlived a SIGKILLed server is stale, not a conflict, so no
    caller may treat its existence alone as "running".
    """
    try:
        pid = int(path.read_text())
    except (FileNotFoundError, ValueError):
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        path.unlink(missing_ok=True)
        return None
    except PermissionError:
        pass  # alive, and not ours
    return pid


def _serve() -> None:
    templates_dir = Path(os.environ.get("KRAFT_TEMPLATES_DIR") or default_templates_dir())
    if seed_home(templates_dir):
        print(f"kraft: seeded default config in {templates_dir}")
    host, port = _bind(templates_dir)
    # After _bind: a run refused for binding a LAN address without a password
    # must not leave a pidfile behind.
    pid_path = RunDirs(default_run_dir()).ensure().pid
    running = _read_pid(pid_path)
    if running is not None:
        print(f"kraft: already running (pid {running}) — kraft admin stop", file=sys.stderr)
        raise SystemExit(1)
    pid_path.write_text(str(os.getpid()))
    print(f"kraft: http://{host}:{port}")
    try:
        uvicorn.run("kraft.api:app", host=host, port=port, log_level="warning")
    finally:
        pid_path.unlink(missing_ok=True)
```

Import `RunDirs` and `default_run_dir` from `kraft.paths` at the top of `cli.py` if they are not already imported.

- [ ] **Step 5: Run the tests**

Run: `pytest tests/test_cli_admin.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/kraft/paths.py src/kraft/cli.py tests/test_cli_admin.py
git commit -m "feat(cli): the server writes run/kraft.pid and refuses to double-start"
```

---

### Task 4: `kraft admin stop`

**Files:**
- Modify: `src/kraft/cli.py` (`_add_admin`, new `_cmd_stop`)
- Test: `tests/test_cli_admin.py`

**Interfaces:**
- Consumes: `cli._read_pid` and `RunDirs.pid` from Task 3.
- Produces: `cli._cmd_stop(ns: argparse.Namespace) -> None`.

- [ ] **Step 1: Write the failing tests**

```python
def test_stop_signals_the_running_pid(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("KRAFT_HOME", str(tmp_path))
    pid_path = RunDirs(tmp_path / "run").pid
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text("4171")
    signalled, alive = [], [True]

    def fake_kill(pid, sig):
        if sig == 0 and not alive[0]:
            raise ProcessLookupError
        if sig != 0:
            signalled.append((pid, sig))
            alive[0] = False

    monkeypatch.setattr(cli.os, "kill", fake_kill)
    cli.main(["admin", "stop"])
    assert signalled == [(4171, signal.SIGTERM)]
    assert "4171" in capsys.readouterr().out


def test_stop_with_no_server_is_not_an_error(tmp_path, monkeypatch, capsys):
    """Safe to run twice: a teardown script must not fail on the second call."""
    monkeypatch.setenv("KRAFT_HOME", str(tmp_path))
    cli.main(["admin", "stop"])
    assert "no server running" in capsys.readouterr().out


def test_stop_reports_a_stale_pidfile_and_clears_it(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("KRAFT_HOME", str(tmp_path))
    pid_path = RunDirs(tmp_path / "run").pid
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text("999999")
    cli.main(["admin", "stop"])
    assert "no server running" in capsys.readouterr().out
    assert not pid_path.exists()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `pytest tests/test_cli_admin.py -q -k stop`
Expected: FAIL — `admin stop` is an invalid choice (exit 2).

- [ ] **Step 3: Implement `_cmd_stop` and register it**

```python
def _cmd_stop(ns: argparse.Namespace) -> None:
    """SIGTERM to the pid in the run dir, then wait for it to actually go.

    Nothing running is not a failure: `kraft admin stop` in a teardown script
    must be safe to run twice.
    """
    pid_path = RunDirs(default_run_dir()).pid
    pid = _read_pid(pid_path)
    if pid is None:
        print("kraft: no server running")
        return
    os.kill(pid, signal.SIGTERM)
    for _ in range(50):
        if _read_pid(pid_path) is None:
            print(f"kraft: stopped (pid {pid})")
            return
        time.sleep(0.1)
    print(f"kraft: pid {pid} did not stop within 5s", file=sys.stderr)
    raise SystemExit(1)
```

Import `signal` and `time` at the top of `cli.py`, and `signal` at the top of `tests/test_cli_admin.py` (the test asserts on `signal.SIGTERM`). In `_add_admin`, beside `start`:

```python
    stop = subs.add_parser("stop", parents=[common], help="stop the running server")
    stop.set_defaults(func=_cmd_stop)
```

Add `"stop"` back to `GROUPS["admin"]` in `tests/test_cli_verbs.py` if Task 1 removed it.

- [ ] **Step 4: Run the tests**

Run: `pytest tests/test_cli_admin.py tests/test_cli_verbs.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/kraft/cli.py tests/test_cli_admin.py tests/test_cli_verbs.py
git commit -m "feat(cli): kraft admin stop"
```

---

### Task 5: Hint strings inside the product

Nine strings name a command the user should type next. Each must move with its command, or the product tells people to run something that no longer exists.

**Files:**
- Modify: `src/kraft/client.py:133`, `src/kraft/client.py:155`, `src/kraft/client.py:227`, `src/kraft/client.py:451`, `src/kraft/render.py:39`, `src/kraft/render.py:259`, `src/kraft/api.py:1563`, `src/kraft/index/ingest.py:155`, `src/kraft/doctor.py`
- Test: `tests/test_cli_verbs.py`

**Interfaces:**
- Consumes: `cli.MOVED` from Task 2.

- [ ] **Step 1: Write the failing test**

```python
import pathlib
import re

SRC = pathlib.Path(cli.__file__).parent


def test_no_source_string_tells_a_user_to_run_a_removed_verb():
    """A hint that names a dead command is worse than no hint."""
    offenders = []
    pattern = re.compile(r"`kraft (" + "|".join(sorted(cli.MOVED)) + r")\b")
    for path in SRC.rglob("*.py"):
        if path.name == "cli.py":  # MOVED itself lists them
            continue
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{path.name}:{n}: {line.strip()}")
    assert not offenders, "\n".join(offenders)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/test_cli_verbs.py -q -k removed_verb`
Expected: FAIL, listing the nine lines.

- [ ] **Step 3: Update each string**

Rewrite each hint to its new path. `client.py:133` becomes:

```python
        raise ValueError(f"no Kraft server at {base_url()} — start one with `kraft`") from exc
```

(bare `kraft`, not `kraft admin start`: the shortest true thing to type.) The rest take their group — `kraft item abandon`, `kraft view logs`, `kraft repo connect`, `kraft view list`, `kraft view artifact`, `kraft view watch`, `kraft view docs`, `kraft admin doctor`. Let the test tell you if one was missed.

- [ ] **Step 4: Run the test plus the suites that assert on these strings**

Run: `pytest tests/test_cli_verbs.py tests/test_client_read.py tests/test_client_act.py tests/test_client_context.py tests/test_api.py -q`
Expected: PASS. Some client tests assert on the old sentence — update the expected string, not the code.

- [ ] **Step 5: Commit**

```bash
git add src/kraft tests/
git commit -m "fix(cli): hints name the grouped commands"
```

---

### Task 6: Documentation

**Files:**
- Modify: `README.md`, `CLAUDE.md`, `AGENTS.md`

- [ ] **Step 1: Rewrite the verb table in all three**

Replace the flat command block with the grouped tree, keeping each line's existing explanatory comment:

```
kraft                                  # serve; same as `kraft admin start`
kraft view list [--all] [--status=paused]   # the board, scoped to the cwd's repo
kraft view show [ID]                   # ID defaults to the worktree you are in
kraft item create "title"              # files it paused; a human starts it
kraft item approve [ID] / kraft item reject [ID] --note "why"
kraft item pause [ID] / kraft item resume [ID] --steer "..."
kraft item retry [ID] [--steer "..."]  # the only door back onto a stopped item
kraft item abandon [ID] --yes          # destroys uncommitted work
kraft view search "query"
kraft view logs [ID] [-f] [-n N]       # a worker session's log; --json is NDJSON
kraft view events [ID] [--after N] [--type T]
kraft view watch                       # live board, needs a terminal
kraft view diff [ID] [--stat|--name-only]
kraft view docs [ID] / kraft view doc DOC_ID [--open [EDITOR]]
kraft view artifact [ID]               # the doc the pending gate is about
kraft repo list                        # `*` marks the repo you are in
kraft repo connect [PATH] / kraft repo disconnect [PATH]
kraft repo path [ID] (alias cd) / kraft repo open [ID]
kraft admin start [--host H] [--port P] / kraft admin stop
kraft admin health                     # exit 1 when degraded
kraft admin doctor                     # every check at once; exit 1 on any
kraft admin reindex [--repo PATH]
kraft admin init [--repo] / kraft admin mcp
```

Add one line under it: `Old flat verbs are gone; typing one prints where it moved.`

- [ ] **Step 2: Check no doc still shows a dead command**

Run:

```bash
grep -nE '`?kraft (list|show|approve|reject|pause|resume|retry|abandon|search|logs|events|watch|diff|docs|doc|artifact|repos|connect|disconnect|path|cd|open|serve|health|doctor|reindex|init|mcp)\b' README.md CLAUDE.md AGENTS.md
```

Expected: no output. (Historical plan and spec files under `docs/superpowers/` are a record of what was true then — leave them.)

- [ ] **Step 3: Run the named test files once more**

Run: `pytest tests/test_cli_verbs.py tests/test_cli_repos.py tests/test_cli_watching.py tests/test_cli_doctor.py tests/test_cli_admin.py tests/test_cli_reviewing.py tests/test_gates.py tests/test_ws.py tests/test_client_read.py tests/test_client_act.py tests/test_client_context.py tests/test_api.py -q`
Expected: PASS.

- [ ] **Step 4: Lint**

Run: `just lint`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add README.md CLAUDE.md AGENTS.md
git commit -m "docs: the grouped kraft command tree"
```
