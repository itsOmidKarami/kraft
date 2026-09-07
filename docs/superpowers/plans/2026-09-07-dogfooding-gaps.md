# Closing the Dogfooding Gaps Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Kraft able to run its own development cycle end to end — a chain that opens a real merge request, reads real CI, and merges — with spend capped, dead items reclaimable, and concurrency honestly bounded.

**Architecture:** A `Forge` Protocol in `src/kraft/adapters/forge.py` with three implementations (`GlabCli`, `GhCli`, `FakeForge`), dispatched from a new `kind: forge` branch in `executor._dispatch` and selected by name in `templates/registry.yaml` — no runtime probing. The four smaller gaps are local edits to `cli.py`, `templates/policy.yaml`, `store.py`/`api.py`, and `intake.py`.

**Tech Stack:** Python 3.14, FastAPI, SQLite via `store.py`, pytest, `glab` and `gh` CLIs, PyYAML.

**Spec:** `docs/superpowers/specs/2026-09-07-dogfooding-gaps-design.md`

## Global Constraints

- `requires-python = ">=3.14"`. Use modern syntax freely; no back-compat shims.
- No new runtime dependencies. `glab` and `gh` are external binaries invoked as subprocesses, not Python packages.
- Every subprocess call goes through `asyncio.to_thread(subprocess.run, ...)`, matching `adapters/beads.py`. Never block the event loop.
- `except OSError, ValueError` — not `except OSError` alone — when reading a file that a crashed process may have left as invalid UTF-8. This exact clause has been wrong three times in this codebase (`adapters/subprocess.py:47`).
- Registry `kind` values are validated in `templates.py:13` `_VALID_KINDS`. A new kind that is not added there fails every template load.
- Tests: run only the named files, never the full suite. `just test` is ~14 minutes.
- Conservative git profile (CLAUDE.md): commit steps are written into this plan, but do not push, and do not merge anything without the human saying so at the gate.

---

### Task 1: `kraft --version`

**Files:**
- Modify: `src/kraft/cli.py:581` (`main`), and `build_parser`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: nothing later tasks rely on.

The installed build that fell through to `serve` on `kraft health` predated argparse. The tree already rejects unknown subcommands; what was missing is any way to see the install is behind.

- [ ] **Step 1: Write the failing test**

```python
def test_version_flag_prints_a_version(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert out.startswith("kraft ")
    assert out.strip() != "kraft"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli.py::test_version_flag_prints_a_version -v`
Expected: FAIL — argparse exits 2 with "unrecognized arguments: --version".

- [ ] **Step 3: Write minimal implementation**

In `src/kraft/cli.py`, at the top:

```python
from importlib.metadata import PackageNotFoundError, version as _pkg_version


def _version() -> str:
    try:
        return _pkg_version("kraft")
    except PackageNotFoundError:
        # Running from a source checkout that was never installed.
        return "0.0.0+source"
```

In `build_parser()`, on the top-level parser (not on a subparser):

```python
parser.add_argument("--version", action="version", version=f"kraft {_version()}")
```

`main` needs no change: `--version` before a subcommand reaches the top-level parser, and argparse's `version` action exits 0 itself.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cli.py::test_version_flag_prints_a_version -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/kraft/cli.py tests/test_cli.py
git commit -m "feat(cli): add --version so a stale install is visible"
```

---

### Task 2: Spend caps on by default

**Files:**
- Modify: `templates/policy.yaml:17-20`
- Test: `tests/test_budget.py`

**Interfaces:**
- Consumes: nothing.
- Produces: nothing in code. The shipped default becomes `work_item_usd: 10`, `daily_usd: 50`.

Enforcement already exists (`policy.py`, `executor._budget_breach`). Only the shipped numbers change. The test exists so a future edit that returns them to `null` fails rather than silently shipping unlimited spend.

- [ ] **Step 1: Write the failing test**

```python
def test_shipped_policy_has_real_spend_caps():
    """The packaged default must never ship uncapped. Kraft-9oq."""
    from kraft import policy as policy_mod
    from kraft.paths import packaged_templates_dir

    loaded = policy_mod.load(packaged_templates_dir() / "policy.yaml")
    assert loaded.budget.work_item_usd == 10
    assert loaded.budget.daily_usd == 50
```

If `kraft.paths` has no `packaged_templates_dir`, read the file relative to the repo root instead — `Path(__file__).parents[1] / "templates" / "policy.yaml"` — and leave a comment saying why.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_budget.py::test_shipped_policy_has_real_spend_caps -v`
Expected: FAIL — `assert None == 10`.

Confirm the failure is the caps being `null`, not an import or path error. A test that fails because it cannot find the file pins nothing.

- [ ] **Step 3: Write minimal implementation**

In `templates/policy.yaml`, replace the two `null`s:

```yaml
budget:
  work_item_usd: 10   # per work item, across every loop in its chain
  daily_usd: 50       # every work item on this instance, since local midnight
```

Leave the existing comment above the block — it explains that a cap refuses to *start* the next task and cannot interrupt a running one, which is still true.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_budget.py -v`
Expected: PASS, and the existing budget tests still pass.

- [ ] **Step 5: Update the seeded copy by hand**

`$KRAFT_HOME/templates/policy.yaml` is written once on first run and never overwritten, so this change does not reach an existing install (that is Kraft-ouo, not solved here):

```bash
python3 - <<'EOF'
import pathlib, os
p = pathlib.Path(os.environ.get("KRAFT_HOME", pathlib.Path.home() / ".kraft")) / "templates" / "policy.yaml"
if p.exists():
    s = p.read_text()
    s = s.replace("work_item_usd: null", "work_item_usd: 10").replace("daily_usd: null", "daily_usd: 50")
    p.write_text(s)
    print("patched", p)
else:
    print("no seeded policy at", p, "- nothing to patch")
EOF
```

Show the output. If it printed "no seeded policy", say so rather than assuming it worked.

- [ ] **Step 6: Commit**

```bash
git add templates/policy.yaml tests/test_budget.py
git commit -m "feat(policy): ship real spend caps instead of unlimited"
```

---

### Task 3: One global concurrency limit

**Files:**
- Modify: `src/kraft/intake.py:26-29` (move `_active_count`), `src/kraft/intake.py:71`
- Modify: `src/kraft/store.py` (add `active_count`)
- Modify: `src/kraft/api.py:1086` (`resume_work_item`)
- Modify: `templates/intake.yaml:9`
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `store.active_count(conn) -> int`, used by `intake.tick` and `api.resume_work_item`.

The bead's premise is half wrong and the plan corrects it: `intake._active_count` already counts *every* active item, including manual starts (`intake.py:69` says so). The real gap is that the limit is only *enforced* in the intake tick — `POST /work-items/{wid}/resume` starts an item no matter how many are running.

**Deliberate boundary:** guard `resume` only. Do not guard `approve_gate` or `retry`. Those continue work that is already underway, and refusing them would strand a human mid-chain with no way to finish. Only the "start something new" door is bounded.

- [ ] **Step 1: Write the failing test**

```python
async def test_resume_refuses_when_all_slots_are_busy(client, db):
    """A manual start is bounded by the same limit as auto-intake. Kraft-n2d."""
    client.app.state.intake["max_concurrent"] = 1
    busy = await _make_work_item(client, status="active")
    idle = await _make_work_item(client, status="paused")

    resp = await client.post(f"/work-items/{idle}/resume", json={})

    assert resp.status_code == 409
    assert "1" in resp.json()["detail"]  # the limit is named in the message
    assert busy  # the running item is untouched
```

Use whatever fixture `tests/test_api.py` already uses to build a work item in a given status — follow the file's existing pattern rather than inventing `_make_work_item` if a helper is already there.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_api.py::test_resume_refuses_when_all_slots_are_busy -v`
Expected: FAIL — 200, the resume succeeds.

- [ ] **Step 3: Move the count into `store.py`**

Add to `src/kraft/store.py`:

```python
def active_count(conn: sqlite3.Connection) -> int:
    """How many work items are running right now.

    One definition, because two callers bound against it: auto-intake decides
    whether to pick anything up, and a manual resume is refused when the last
    slot is taken. Two copies of this query would let those disagree.
    """
    return conn.execute("SELECT COUNT(*) FROM work_items WHERE status = 'active'").fetchone()[0]
```

In `src/kraft/intake.py`, delete the local `_active_count` and use the store's:

```python
slots = int(cfg.get("max_concurrent", 1)) - st.db.read(store.active_count)
```

Keep the existing comment above that line — it still explains why every active item counts.

- [ ] **Step 4: Guard the resume endpoint**

In `src/kraft/api.py`, in `resume_work_item`, after the existing status check and before any write:

```python
    limit = int(st.intake.get("max_concurrent", 1))
    if st.db.read(store.active_count) >= limit:
        raise HTTPException(
            409,
            f"all {limit} slots are busy; pause something or raise max_concurrent",
        )
```

It goes after the paused check so an item that is not paused still gets the clearer "work item is not paused" error.

- [ ] **Step 5: Raise the shipped default**

In `templates/intake.yaml`, `max_concurrent: 1` becomes `max_concurrent: 3`. Also update the default in `src/kraft/config.py:323` to match, so a config file missing the key behaves the same as the shipped one.

- [ ] **Step 6: Run tests**

Run: `uv run pytest tests/test_api.py -k "resume or concurrent" -v`
Expected: PASS, including the pre-existing resume tests. If an existing test now hits the limit, it was relying on unbounded starts — set `max_concurrent` explicitly in that test rather than weakening the guard.

- [ ] **Step 7: Commit**

```bash
git add src/kraft/store.py src/kraft/intake.py src/kraft/api.py src/kraft/config.py templates/intake.yaml tests/test_api.py
git commit -m "feat: bound manual starts by max_concurrent, default 3"
```

---

### Task 4: Abandon a work item

**Files:**
- Modify: `src/kraft/store.py` (add `abandon_work_item`)
- Modify: `src/kraft/api.py` (new endpoint, and the list endpoint at `:571`)
- Modify: `src/kraft/client.py` (add `abandon`)
- Modify: `src/kraft/cli.py` (add the verb)
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `POST /work-items/{wid}/abandon`, `store.abandon_work_item(conn, work_item_id)`, `client.abandon(work_item_id) -> dict`, `kraft abandon [ID] --yes`.

- [ ] **Step 1: Write the failing tests**

```python
async def test_abandon_sets_terminal_status_and_removes_the_worktree(client, db, run_dirs):
    wid = await _make_work_item(client, status="paused")
    worktree = run_dirs.worktrees / wid
    assert worktree.is_dir()

    resp = await client.post(f"/work-items/{wid}/abandon")

    assert resp.status_code == 200
    assert resp.json()["status"] == "abandoned"
    assert not worktree.exists()


async def test_abandon_refuses_an_active_item(client, db):
    """Pause first. Otherwise this races a running agent's writes."""
    wid = await _make_work_item(client, status="active")

    resp = await client.post(f"/work-items/{wid}/abandon")

    assert resp.status_code == 409
    assert "active" in resp.json()["detail"]


async def test_list_hides_abandoned_items(client, db):
    wid = await _make_work_item(client, status="paused")
    await client.post(f"/work-items/{wid}/abandon")

    visible = (await client.get("/work-items")).json()["items"]
    everything = (await client.get("/work-items?all=true")).json()["items"]

    assert wid not in [i["id"] for i in visible]
    assert wid in [i["id"] for i in everything]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_api.py -k abandon -v`
Expected: FAIL — 404, the route does not exist. Confirm it is 404 on the route and not 404 on the work item; a test that passes because the fixture is broken pins nothing.

- [ ] **Step 3: Add the store write**

In `src/kraft/store.py`, following the shape of `pause_work_item:554`:

```python
def abandon_work_item(conn: sqlite3.Connection, work_item_id: str) -> None:
    """Terminal. The item stays in the table — its events and sessions are still
    the record of what happened — but it is out of the running set for good."""
    conn.execute(
        "UPDATE work_items SET status = 'abandoned', updated_at = ? WHERE id = ?",
        (_now(), work_item_id),
    )
    events.append(conn, work_item_id, "work_item_abandoned", {})
```

- [ ] **Step 4: Add the endpoint**

In `src/kraft/api.py`, beside `pause_work_item:1050`:

```python
@app.post("/work-items/{wid}/abandon")
async def abandon_work_item(wid: str, request: Request):
    """Terminal state plus worktree and branch reclaim (Kraft-x85).

    Refuses while the item is active rather than killing its sessions itself:
    `pause` already owns stopping an attempt, and doing both here would mean two
    places that know how to terminate an agent.
    """
    st = request.app.state
    row = _work_item_row(st, wid)
    if row["status"] == "active":
        raise HTTPException(409, "work item is active; pause it before abandoning")
    if row["status"] == "abandoned":
        return {"id": wid, "status": "abandoned"}
    await st.db.write(lambda c: store.abandon_work_item(c, wid))
    removed = await _remove_worktree(Path(row["repo"]), st.run_dirs.worktrees / wid, wid)
    return {"id": wid, "status": "abandoned", "worktree_removed": removed}
```

And the reclaim helper, near the other git helpers:

```python
async def _remove_worktree(repo: Path, worktree: Path, wid: str) -> bool:
    """Remove the worktree and the `kraft/<id>` branch. Best-effort: the row is
    already abandoned, and a git failure here must not leave the item in a state
    the board cannot show. Failures are logged, not raised."""
    ok = True
    for args in (
        ["git", "worktree", "remove", "--force", str(worktree)],
        ["git", "worktree", "prune"],
        ["git", "branch", "-D", f"kraft/{wid}"],
    ):
        done = await asyncio.to_thread(
            subprocess.run, args, cwd=repo, capture_output=True, text=True
        )
        if done.returncode != 0:
            logger.warning("abandon %s: %s failed: %s", wid, args[1], done.stderr.strip())
            ok = False
    return ok
```

- [ ] **Step 5: Hide abandoned items from the board**

In `list_work_items:571`, the SQL becomes:

```python
        rows = c.execute(
            "SELECT * FROM work_items WHERE (? OR status != 'abandoned') ORDER BY created_at",
            (show_all,),
        ).fetchall()
```

with `show_all` read from the query string: `show_all = request.query_params.get("all") == "true"`. Read it outside `_read`, since `_read` runs on the db thread.

- [ ] **Step 6: Add the client method and the CLI verb**

In `src/kraft/client.py`, beside `pause:509`:

```python
async def abandon(work_item_id: str | None = None) -> dict:
    """Terminal. Removes the worktree, destroying anything uncommitted in it."""
    target = _forbid_self_action(work_item_id)
    return await _act(f"/work-items/{target}/abandon")
```

In `src/kraft/cli.py`, beside `_cmd_pause:266`:

```python
def _cmd_abandon(ns: argparse.Namespace) -> None:
    if not ns.yes:
        raise ValueError("abandon destroys the worktree and anything uncommitted in it; pass --yes")
    emit(asyncio.run(client.abandon(ns.id)), _render_action, ns.json)
```

and in `build_parser`, beside the `pause` parser at `:490`:

```python
    abandon = subs.add_parser("abandon", parents=[common], help="drop an item and reclaim its worktree")
    abandon.add_argument("id", nargs="?")
    abandon.add_argument("--yes", action="store_true", help="required: this destroys uncommitted work")
    abandon.set_defaults(func=_cmd_abandon)
```

`main` already turns `ValueError` into `kraft: <message>` and exit 1, so the missing-`--yes` path needs no extra handling.

- [ ] **Step 7: Add the `--all` flag to `kraft list`**

The `list` parser already has an `--all` flag (see CLAUDE.md's CLI listing). Confirm it reaches the client as the `all=true` query parameter; if it currently means something else, add `--include-abandoned` rather than overloading it, and say so.

- [ ] **Step 8: Run tests**

Run: `uv run pytest tests/test_api.py -k abandon tests/test_cli.py -k abandon -v`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add src/kraft/store.py src/kraft/api.py src/kraft/client.py src/kraft/cli.py tests/test_api.py tests/test_cli.py
git commit -m "feat: abandon a work item and reclaim its worktree"
```

---

### Task 5: The `Forge` protocol and `FakeForge`

**Files:**
- Create: `src/kraft/adapters/forge.py`
- Test: `tests/test_forge.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `Forge` (Protocol), `MR`, `CIStatus`, `FakeForge`, `resolve(name: str) -> Forge`. Tasks 6, 7 and 8 all build on these exact names.

- [ ] **Step 1: Write the failing test**

```python
import pytest
from kraft.adapters import forge


async def test_fake_forge_round_trips_an_mr(tmp_path):
    f = forge.FakeForge(ci_states=["pending", "success"])

    mr = await f.open_mr(repo=tmp_path, branch="kraft/abc", title="t", body="b")

    assert mr.number == 1
    assert mr.url.endswith("/1")
    assert (await f.ci_status(repo=tmp_path, mr=mr)).state == "pending"
    assert (await f.ci_status(repo=tmp_path, mr=mr)).state == "success"


async def test_fake_forge_refuses_to_merge_an_unopened_mr(tmp_path):
    f = forge.FakeForge(ci_states=["success"])
    with pytest.raises(forge.ForgeError):
        await f.merge(repo=tmp_path, mr=forge.MR(number=99, url="http://x/99"))


def test_resolve_rejects_an_unknown_backend():
    with pytest.raises(forge.ForgeError) as exc:
        forge.resolve("bitbucket")
    assert "bitbucket" in str(exc.value)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_forge.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kraft.adapters.forge'`.

- [ ] **Step 3: Write the module**

```python
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

CIState = Literal["pending", "success", "failed"]


class ForgeError(RuntimeError):
    """The forge could not be reached, or answered something unusable."""


@dataclass(frozen=True)
class MR:
    number: int
    url: str


@dataclass(frozen=True)
class CIStatus:
    state: CIState
    url: str
    #: One line per job, for the human_review brief to render.
    jobs: tuple[str, ...] = ()


class Forge(Protocol):
    async def open_mr(self, *, repo: Path, branch: str, title: str, body: str) -> MR: ...
    async def ci_status(self, *, repo: Path, mr: MR) -> CIStatus: ...
    async def merge(self, *, repo: Path, mr: MR) -> None: ...


@dataclass
class FakeForge:
    """In-memory forge for tests. `ci_states` is consumed one call at a time, so
    a test can script pending-then-green without sleeping or polling."""

    ci_states: list[CIState] = field(default_factory=lambda: ["success"])
    opened: dict[int, str] = field(default_factory=dict)
    merged: list[int] = field(default_factory=list)

    async def open_mr(self, *, repo: Path, branch: str, title: str, body: str) -> MR:
        number = len(self.opened) + 1
        self.opened[number] = branch
        return MR(number=number, url=f"http://fake.forge/{number}")

    async def ci_status(self, *, repo: Path, mr: MR) -> CIStatus:
        state = self.ci_states.pop(0) if len(self.ci_states) > 1 else self.ci_states[0]
        return CIStatus(state=state, url=f"{mr.url}/pipelines", jobs=(f"fake-job: {state}",))

    async def merge(self, *, repo: Path, mr: MR) -> None:
        if mr.number not in self.opened:
            raise ForgeError(f"no such merge request: {mr.number}")
        self.merged.append(mr.number)


def resolve(name: str) -> Forge:
    """Named, never probed. Auto-detecting an available CLI would mean the same
    work item takes a different path on a laptop than in a container, and a bug
    that reproduces on one and not the other."""
    match name:
        case "fake":
            return FakeForge()
        case _:
            raise ForgeError(f"unknown forge backend {name!r}; known: fake")
```

`GlabCli` and `GhCli` join `resolve` in Task 6. Leaving them out here keeps this task's test green on its own.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_forge.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/kraft/adapters/forge.py tests/test_forge.py
git commit -m "feat(forge): Forge protocol and FakeForge"
```

---

### Task 6: `GlabCli` and `GhCli`

**Files:**
- Modify: `src/kraft/adapters/forge.py`
- Test: `tests/test_forge.py`

**Interfaces:**
- Consumes: `Forge`, `MR`, `CIStatus`, `ForgeError`, `resolve` from Task 5.
- Produces: `GlabCli`, `GhCli`, both satisfying `Forge`; `resolve("glab")` and `resolve("gh")`.

- [ ] **Step 1: Probe the real CLIs and record their output**

Do not write a parser against remembered flags. Run these read-only commands in this repo and paste the real output into the task notes before writing any code:

```bash
glab --version
glab mr create --help | head -40
glab ci status --help | head -30
glab ci list --help | head -30
gh --version
gh pr create --help | head -40
gh pr view --json 2>&1 | head -20   # prints the valid --json field names
```

Pick, for each backend, the invocation that returns **machine-readable** output — JSON if either offers it (`gh pr view --json statusCheckRollup`, `glab ... -F json` or `glab api`). Parsing human-formatted status text is the failure mode this step exists to avoid. Write down the exact commands chosen; the next step's code must use those and nothing else.

- [ ] **Step 2: Write the failing tests**

Test against a stubbed binary, not the network. Put a fake `glab` on `PATH` that echoes the captured fixture:

```python
import json
import os
import stat

GLAB_MR_CREATE_FIXTURE = "..."   # paste real output from Step 1
GLAB_CI_FIXTURE = "..."          # paste real output from Step 1


def _stub_bin(tmp_path, name: str, stdout: str, rc: int = 0):
    """A fake forge CLI on PATH. Beats monkeypatching subprocess: it exercises
    the real argv-building and the real decoding path."""
    p = tmp_path / name
    p.write_text(f"#!/bin/sh\ncat <<'EOF'\n{stdout}\nEOF\nexit {rc}\n")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    os.environ["PATH"] = f"{tmp_path}:{os.environ['PATH']}"
    return p


async def test_glab_open_mr_parses_the_number_and_url(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    _stub_bin(tmp_path, "glab", GLAB_MR_CREATE_FIXTURE)

    mr = await forge.GlabCli().open_mr(
        repo=tmp_path, branch="kraft/abc", title="t", body="b"
    )

    assert isinstance(mr.number, int)
    assert mr.url.startswith("http")


async def test_glab_ci_status_maps_a_failed_pipeline(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    _stub_bin(tmp_path, "glab", GLAB_CI_FAILED_FIXTURE)

    status = await forge.GlabCli().ci_status(repo=tmp_path, mr=forge.MR(1, "http://x/1"))

    assert status.state == "failed"
    assert status.jobs  # the brief needs something to show


async def test_glab_raises_forge_error_when_the_cli_is_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))  # empty: no glab anywhere
    with pytest.raises(forge.ForgeError):
        await forge.GlabCli().ci_status(repo=tmp_path, mr=forge.MR(1, "http://x/1"))
```

Write the mirror three for `GhCli`.

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_forge.py -v`
Expected: FAIL — `AttributeError: module 'kraft.adapters.forge' has no attribute 'GlabCli'`.

- [ ] **Step 4: Implement both backends**

Shared subprocess helper, then the two classes:

```python
async def _run(repo: Path, args: list[str]) -> str:
    """A forge CLI call. FileNotFoundError becomes ForgeError so a missing or
    unnamed binary reads as a configuration problem, which is what it is."""
    try:
        done = await asyncio.to_thread(
            subprocess.run, args, cwd=repo, capture_output=True, text=True
        )
    except FileNotFoundError as exc:
        raise ForgeError(f"{args[0]} is not installed or not on PATH") from exc
    if done.returncode != 0:
        raise ForgeError(f"{' '.join(args)} failed: {done.stderr.strip() or done.stdout.strip()}")
    return done.stdout
```

Each backend pushes the branch before creating the MR — `git push -u origin <branch>` via the same helper — because a create against an unpushed branch fails on both forges. Map each forge's own status vocabulary onto `CIState` in one explicit dict per backend, and treat an unrecognised status as `"failed"` rather than guessing:

```python
_GLAB_STATES = {
    "success": "success", "passed": "success",
    "failed": "failed", "canceled": "failed",
    "running": "pending", "pending": "pending", "created": "pending",
}
```

Fill this in from the real vocabulary captured in Step 1, not from this sketch. Then extend `resolve`:

```python
    match name:
        case "fake":
            return FakeForge()
        case "glab":
            return GlabCli()
        case "gh":
            return GhCli()
        case _:
            raise ForgeError(f"unknown forge backend {name!r}; known: glab, gh, fake")
```

Update Task 5's `test_resolve_rejects_an_unknown_backend` assertion if it pinned the old "known:" string.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_forge.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/kraft/adapters/forge.py tests/test_forge.py
git commit -m "feat(forge): glab and gh backends"
```

---

### Task 7: `kind: forge` passes registry validation

**Files:**
- Modify: `src/kraft/templates.py:13` (`_VALID_KINDS`), and the validation loop at `:89-105`
- Test: `tests/test_templates.py` (or wherever `load_registry` is currently tested — check first)

**Interfaces:**
- Consumes: nothing from earlier tasks (validation is by string, not by import).
- Produces: registry bindings of the form `{kind: forge, handler: open_mr|ci_poll|merge, backend: glab|gh|fake}` load successfully; anything else is a `RegistryError`.

- [ ] **Step 1: Find the existing test file**

Run: `grep -rln "load_registry" tests/`
Add the new tests to whichever file already covers it. Do not create a second one.

- [ ] **Step 2: Write the failing tests**

```python
def test_forge_binding_loads(tmp_path):
    p = tmp_path / "registry.yaml"
    p.write_text(
        "hooks:\n"
        "  on.mr.open: {kind: forge, handler: open_mr, backend: glab}\n"
    )
    registry = load_registry(p)
    assert registry.hooks["on.mr.open"]["backend"] == "glab"


def test_forge_binding_needs_a_known_handler(tmp_path):
    p = tmp_path / "registry.yaml"
    p.write_text("hooks:\n  on.mr.open: {kind: forge, handler: teleport, backend: glab}\n")
    with pytest.raises(RegistryError, match="teleport"):
        load_registry(p)


def test_forge_binding_needs_a_known_backend(tmp_path):
    p = tmp_path / "registry.yaml"
    p.write_text("hooks:\n  on.mr.open: {kind: forge, handler: open_mr, backend: bitbucket}\n")
    with pytest.raises(RegistryError, match="bitbucket"):
        load_registry(p)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_templates.py -k forge -v`
Expected: FAIL — `RegistryError: ... has unknown kind 'forge'`.

- [ ] **Step 4: Extend the validator**

In `src/kraft/templates.py`:

```python
_VALID_KINDS = {"builtin", "agent", "subprocess", "forge"}
_FORGE_HANDLERS = {"open_mr", "ci_poll", "merge"}
_FORGE_BACKENDS = {"glab", "gh", "fake"}
```

and in the per-hook loop, beside the existing `builtin` and `subprocess` checks:

```python
        if kind == "forge":
            handler = binding.get("handler")
            if handler not in _FORGE_HANDLERS:
                raise RegistryError(
                    f"{path.name}: forge hook {hook!r} has unknown handler {handler!r}; "
                    f"known: {sorted(_FORGE_HANDLERS)}"
                )
            backend = binding.get("backend")
            if backend not in _FORGE_BACKENDS:
                raise RegistryError(
                    f"{path.name}: forge hook {hook!r} has unknown backend {backend!r}; "
                    f"known: {sorted(_FORGE_BACKENDS)}"
                )
```

`_FORGE_BACKENDS` duplicates the names in `forge.resolve`. That is deliberate: importing the adapter into the template validator would make config loading depend on the adapter layer. Add a comment in `forge.resolve` pointing at `_FORGE_BACKENDS` so the two are edited together.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_templates.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/kraft/templates.py tests/test_templates.py
git commit -m "feat(templates): validate kind: forge bindings"
```

---

### Task 8: Dispatch forge tasks from the executor

**Files:**
- Modify: `src/kraft/builtins.py:120-163` (`_record_done` gains `status`)
- Modify: `src/kraft/adapters/forge.py` (add `run_task`)
- Modify: `src/kraft/executor.py:316` (new `kind == "forge"` branch)
- Test: `tests/test_executor.py`, `tests/test_forge.py`

**Interfaces:**
- Consumes: `forge.resolve`, `MR`, `CIStatus`, `ForgeError` (Tasks 5-6); registry validation (Task 7).
- Produces: `forge.run_task(db, run_dirs, *, session_id, work_item_id, node_id, hook_point, handler, backend, repo, branch, title, round=0) -> str` returning `"done"` or `"failed"`.

This is the load-bearing task. `_record_done` currently hardcodes `"done"` (`builtins.py:162`); a red pipeline recorded as done would sail into the merge node.

- [ ] **Step 1: Write the failing test**

```python
async def test_ci_poll_records_failed_when_the_pipeline_is_red(db, run_dirs, monkeypatch):
    """Red CI must not read as a done node — the next node is merge."""
    fake = forge.FakeForge(ci_states=["failed"])
    monkeypatch.setattr(forge, "resolve", lambda name: fake)

    status = await forge.run_task(
        db, run_dirs,
        session_id="s1", work_item_id="w1", node_id="mr_checks",
        hook_point="on.ci.poll", handler="ci_poll", backend="fake",
        repo=run_dirs.worktrees / "w1", branch="kraft/w1", title="t",
    )

    assert status == "failed"
    row = db.read(lambda c: c.execute("SELECT status FROM worker_sessions WHERE id='s1'").fetchone())
    assert row["status"] == "failed"


async def test_ci_poll_records_done_when_the_pipeline_is_green(db, run_dirs, monkeypatch):
    fake = forge.FakeForge(ci_states=["success"])
    monkeypatch.setattr(forge, "resolve", lambda name: fake)
    status = await forge.run_task(
        db, run_dirs,
        session_id="s2", work_item_id="w1", node_id="mr_checks",
        hook_point="on.ci.poll", handler="ci_poll", backend="fake",
        repo=run_dirs.worktrees / "w1", branch="kraft/w1", title="t",
    )
    assert status == "done"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_forge.py -k ci_poll -v`
Expected: FAIL — `AttributeError: ... has no attribute 'run_task'`.

Check the failure is the missing function, not a fixture error. If `db` or `run_dirs` are not existing fixtures in this repo's `conftest.py`, find what is and use that.

- [ ] **Step 3: Give `_record_done` a status**

In `src/kraft/builtins.py`, change the signature and the final write:

```python
async def _record_done(
    db,
    run_dirs,
    *,
    session_id: str,
    work_item_id: str,
    node_id: str,
    hook_point: str,
    round: int,
    log: str,
    status: str = "done",
) -> str:
    """A session row for a builtin that did its work in-process. Shared so a
    builtin's bookkeeping cannot drift from `noop`'s.

    `status` defaults to "done" because every original caller succeeded by
    construction. A forge task can genuinely fail — a red pipeline — and
    recording that as done would let the chain walk into the merge node.
    """
```

and at the end:

```python
    await db.write(lambda c: store.session_exited(c, session_id, status))
    return status
```

`env_setup` and `noop` keep their behaviour through the default. Do not change their call sites.

- [ ] **Step 4: Write `forge.run_task`**

```python
async def run_task(
    db,
    run_dirs,
    *,
    session_id: str,
    work_item_id: str,
    node_id: str,
    hook_point: str,
    handler: str,
    backend: str,
    repo: Path,
    branch: str,
    title: str,
    round: int = 0,
) -> str:
    """One forge node. Records a session the same way a builtin does, because
    the work happens in this process — there is no child to supervise."""
    from kraft import builtins as _builtins

    body = (
        f"Opened by Kraft for work item {work_item_id}.\n\n"
        f"Branch `{branch}`. Review the diff and the pipeline before merging."
    )
    f = resolve(backend)
    try:
        match handler:
            case "open_mr":
                mr = await f.open_mr(repo=repo, branch=branch, title=title, body=body)
                log, status = f"opened {mr.url}\n", "done"
            case "ci_poll":
                mr = MR(number=0, url="")  # resolved from the branch by both CLIs
                ci = await f.ci_status(repo=repo, mr=mr)
                log = f"pipeline {ci.state}: {ci.url}\n" + "".join(f"  {j}\n" for j in ci.jobs)
                status = "done" if ci.state == "success" else "failed"
            case "merge":
                await f.merge(repo=repo, mr=MR(number=0, url=""))
                log, status = "merged\n", "done"
            case _:
                log, status = f"unknown forge handler {handler!r}\n", "failed"
    except ForgeError as exc:
        log, status = f"{hook_point} failed: {exc}\n", "failed"

    return await _builtins._record_done(
        db, run_dirs,
        session_id=session_id, work_item_id=work_item_id, node_id=node_id,
        hook_point=hook_point, round=round, log=log, status=status,
    )
```

A `pending` pipeline resolves to `"failed"` here, which is wrong-but-safe for this pass — it stops at the human rather than merging on an unfinished pipeline. Leave this comment on it:

```python
# ponytail: a pending pipeline stops the node rather than waiting. A real
# poll loop with backoff belongs here once a live run shows how long this
# repo's pipeline actually takes.
```

- [ ] **Step 5: Add the executor branch**

In `src/kraft/executor.py`, after the `kind == "subprocess"` block at `:316` and before the final `raise`:

```python
    if kind == "forge":
        return await _forge.run_task(
            db,
            run_dirs,
            hook_point=task_hook,
            handler=binding["handler"],
            backend=binding["backend"],
            repo=Path(work_item_row["repo"]),
            branch=f"kraft/{work_item_row['id']}",
            title=work_item_row["title"],
            **common,
        )
```

with `from kraft.adapters import forge as _forge` beside the other adapter imports at the top.

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_forge.py tests/test_executor.py tests/test_builtins.py -v`
Expected: PASS. `test_builtins.py` is in the list because `_record_done` changed under `noop` and `env_setup`.

- [ ] **Step 7: Commit**

```bash
git add src/kraft/builtins.py src/kraft/adapters/forge.py src/kraft/executor.py tests/test_forge.py
git commit -m "feat(executor): dispatch kind: forge, and let a builtin record failure"
```

---

### Task 9: The `human_review` gate brief

**Files:**
- Create: `src/kraft/skills/review-brief/SKILL.md`
- Modify: `templates/registry.yaml` (four bindings)
- Test: `tests/test_skills.py` (or wherever `_skill.validate` is tested — check first)

**Interfaces:**
- Consumes: Task 7's validation, Task 8's dispatch.
- Produces: `on.human_review.requested` bound to `{kind: agent, command: claude, skill: review-brief, artifact: review_brief}`; the three forge bindings.

`on.human_review.requested` must be an *agent* task, not a forge one: artifact registration exists only on the agent path (`adapters/agent.py:227`), and the gate needs a document for `kraft artifact` to print.

- [ ] **Step 1: Write the skill**

`src/kraft/skills/review-brief/SKILL.md`, following the shape of the existing `src/kraft/skills/spec/SKILL.md` — read that first and match its frontmatter and its instructions about `$KRAFT_RESULT_PATH` and the artifact path.

Content: read the merge request URL and pipeline result from this node's chain events, read the local review findings, and write a brief covering what changed, what CI said, what the local review flagged, and anything deliberately left out. State the CI result plainly, including when it is red — a brief that buries a failed pipeline is worse than no brief.

- [ ] **Step 2: Rewrite the four registry bindings**

In `templates/registry.yaml`, replace these four placeholder lines:

```yaml
  on.mr.open:                { kind: forge, handler: open_mr, backend: glab }
  on.ci.poll:                { kind: forge, handler: ci_poll, backend: glab }
  on.merge:                  { kind: forge, handler: merge,   backend: glab }
  on.human_review.requested: { kind: agent, command: claude, skill: review-brief, artifact: review_brief }
```

Leave `on.chain.review_ready`, `on.review.local.run` and `on.review.mr.run` as `builtin: noop` — the first two are other efforts, and `on.review.mr.run` is Kraft-pl7.

Above the forge block, note why `glab`:

```yaml
  # backend: glab because this repo's origin is gitlab.com. The public repo after
  # the v0.1.0 split (Kraft-5fx) uses `gh`; changing these three lines is the
  # whole migration.
```

- [ ] **Step 3: Verify the templates still load**

Run: `uv run pytest tests/test_templates.py -v`
Expected: PASS. If `load_templates` reports `default.yaml` invalid, the binding names do not match the hook names in the chain — fix the binding, not the chain.

- [ ] **Step 4: Verify the skill validates**

Run: `uv run python -c "from kraft.skill import validate; from kraft.paths import default_skills_dir; validate(default_skills_dir(), 'review-brief', where='manual')"`
Expected: no output, exit 0. A `SkillError` means the directory name or the frontmatter does not match what `_skill.validate` requires.

- [ ] **Step 5: Commit**

```bash
git add src/kraft/skills/review-brief templates/registry.yaml
git commit -m "feat: bind the forge nodes and add the human_review brief skill"
```

---

### Task 10: The chain end to end against `FakeForge`

**Files:**
- Modify: `tests/test_e2e.py`
- Test: same file

**Interfaces:**
- Consumes: everything from Tasks 3-9.
- Produces: nothing. This task is the proof, not a component.

- [ ] **Step 1: Read the existing e2e test**

Run: `sed -n '1,80p' tests/test_e2e.py`
Match its fixtures and its way of driving a chain. Do not build a second harness beside it.

- [ ] **Step 2: Write the failing test**

```python
async def test_full_chain_reaches_merge_with_a_green_pipeline(e2e_app, monkeypatch):
    """Spec through merge, no network. The whole back half used to be noop."""
    fake = forge.FakeForge(ci_states=["success"])
    monkeypatch.setattr(forge, "resolve", lambda name: fake)

    wid = await _run_chain_to_completion(e2e_app, title="dogfood smoke")

    item = await _get_work_item(e2e_app, wid)
    assert item["status"] == "done"
    assert fake.opened, "no merge request was opened"
    assert fake.merged == [1], "the merge node did not run"


async def test_red_pipeline_stops_at_needs_human_without_merging(e2e_app, monkeypatch):
    fake = forge.FakeForge(ci_states=["failed"])
    monkeypatch.setattr(forge, "resolve", lambda name: fake)

    wid = await _run_chain_to_completion(e2e_app, title="dogfood red ci")

    item = await _get_work_item(e2e_app, wid)
    assert item["status"] == "needs_human"
    assert fake.merged == [], "a red pipeline must never reach the merge node"
```

The second test is the one that matters. The first proves the happy path exists; the second proves the failure path cannot merge broken code.

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_e2e.py -k "chain or pipeline" -v`
Expected: FAIL. Check *why*: the registry the e2e fixture loads must be the packaged one with the forge bindings. If it uses its own fixture registry, add the forge bindings there too.

- [ ] **Step 4: Make them pass**

No new production code should be needed — Tasks 3-9 are the implementation. If something is missing, it is a gap in an earlier task; go fix it there rather than patching around it here.

- [ ] **Step 5: Run the whole affected set**

Run: `uv run pytest tests/test_forge.py tests/test_e2e.py tests/test_budget.py tests/test_api.py -q`
Expected: PASS. This is the same set `.kraft-lite/registry.yaml` binds `on.test.run` to.

- [ ] **Step 6: Commit**

```bash
git add tests/test_e2e.py
git commit -m "test: full chain through the forge nodes, green and red"
```

---

## After the plan

The live run against gitlab.com is not a task in this plan. It happens after these ten land, needs a reinstalled `kraft` (`just install`), and both the first push and the merge are confirmed with the human at the time.
