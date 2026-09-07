# Planning Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bind `on.spec.requested` and `on.plan.requested` to real agent hooks that write a spec and a plan into the work item's worktree, so `spec_approval` and `plan_approval` are gates on an artifact a human can read instead of gates on nothing.

**Architecture:** No new adapter kind. The existing `kind: agent` binding grows two keys — `skill:` (which method to follow) and `artifact:` (what kind of document to produce). `skill:` resolves to a Markdown method file: a bare name resolves against `$KRAFT_HOME/skills/<name>/SKILL.md` and falls back to the bundled `src/kraft/skills/<name>/SKILL.md`; a name containing `:` is a plugin reference passed to the agent as an instruction to load it. `artifact:` makes the adapter inject a contract block naming the exact path (`.engineering/<kind>s/<work_item_id>.md`) the agent must write and commit; nothing is stored, so the path is derived identically by the API when a reviewer asks for it. Because the spec and plan nodes run *before* `env_setup` in `default.yaml`, the worktree is created in code on first dispatch rather than by the `env_setup` node.

**Tech Stack:** Python 3.14, FastAPI, sqlite (via `kraft.db.Database`), PyYAML, pytest/anyio; React 19 + TypeScript + vite + vitest for the SPA; `git worktree` for isolation.

**Spec:** `docs/superpowers/specs/2026-09-06-planning-adapter-design.md`

**Beads issue:** Kraft-pqu (this plan). Also closes Kraft-bmp (worktree ordering) and Kraft-2k4 (indexer link condition), whose fixes are Tasks 1 and 6.

**Depends on:** nothing — every module it touches is already on `main`.

## Global Constraints

- **Commits stay local.** No `git push`, no merge to `main`, no `bd dolt push`, unless the user explicitly asks. Every task's commit step is a local commit on the current branch.
- **Beads for task tracking.** No TodoWrite, no markdown checklists outside this plan file. `bd update Kraft-pqu --claim` before Task 1.
- **Artifact path is derived, never stored.** `.engineering/<kind>s/<work_item_id>.md`, computed by `kraft.adapters.agent.artifact_path(kind, work_item_id)` on both the injection side and the read side. A second spelling of this path anywhere is a bug.
- **No new work-item status.** `needs_context` is the only channel an agent has for asking a question; a spec/plan agent that lacks information uses it, and it already routes to `/steer` + `POST /resume`.
- **`templates/` is seeded once and never overwritten** (`cli.seed_home`). Anything shipped under `src/kraft/skills/` is read directly from the package, with `$KRAFT_HOME/skills/<name>/SKILL.md` as an optional operator overlay that is *not* seeded.
- **Ruff line length 100.** `just lint` must pass at every commit.
- **Tests are synchronous.** There is no pytest-asyncio and no anyio plugin (`pyproject.toml` dev deps are pytest + ruff). Every async test in this suite is a sync `def test_*` wrapping an inner `async def scenario()` driven by `asyncio.run(scenario())` — see `tests/test_builtins.py:12`. A bare `async def test_*` is silently never executed. Every test in this plan follows the sync-wrapper idiom.
- **`store.create_work_item` requires `bead_id` and `chain_template`** (`src/kraft/store.py:29`), both keyword-only and both without defaults.
- **Kraft never reads a steering or method file from inside a target repo.** Skill names with a path separator, `.`, `..`, or a leading `.` are rejected — same rule `kraft.steering._path` already enforces.
- **Budget cap on injected text:** the method block rides in the same `--append-system-prompt` string as `_CTX` and steering. No new budget constant; steering keeps its own `MAX_BYTES = 8192` check.

---

## File Structure

| File | Status | Responsibility |
| --- | --- | --- |
| `src/kraft/skill.py` | create | Resolve a `skill:` value to injectable method text. Overlay → bundled → plugin reference. Mirror of `steering.py`. |
| `src/kraft/skills/spec/SKILL.md` | create | The method a headless agent follows to write a spec. |
| `src/kraft/skills/plan/SKILL.md` | create | The method a headless agent follows to write a plan from a spec. |
| `src/kraft/skills/chain-review/SKILL.md` | move | From top-level `skills/chain-review/SKILL.md`, so all bundled skills live in one package directory. |
| `src/kraft/paths.py` | modify | `default_skills_dir()`. |
| `src/kraft/builtins.py` | modify | Extract `ensure_worktree()` from `env_setup`; share a `_record_done` session-row helper with `noop`. |
| `src/kraft/executor.py` | modify | Call `ensure_worktree` in `run`/`resume`; carry `skills_dir` on `LaunchContext`; pass `artifact`/`method_text` through `_dispatch`. |
| `src/kraft/adapters/agent.py` | modify | `artifact_path()`, the `_ARTIFACT` contract block, `method_text` on `Invocation`, injection order. |
| `src/kraft/templates.py` | modify | Validate `skill:` and `artifact:` on agent bindings; `skills_dir` kwarg on `load_registry`. |
| `src/kraft/index/ingest.py` | modify | Read `work_item_ids` front matter from every document, not only session summaries. |
| `src/kraft/api.py` | modify | `gate_artifact` on the detail payload; `GET /work-items/{wid}/artifact`. |
| `src/kraft/client.py` | modify | `artifact()`; keep `gate_artifact` in `get_work_item`. |
| `src/kraft/cli.py` | modify | `kraft artifact [ID]`. |
| `src/kraft/mcp.py` | modify | `get_gate_artifact` tool. |
| `src/kraft/doctor.py` | modify | `hooks` check: shipped-default bindings still `builtin:noop` locally. |
| `templates/registry.yaml` | modify | Bind the two hooks. |
| `fixtures/fake-claude.sh` | modify | Write and commit a fake artifact when the hook point is spec/plan. |
| `frontend/src/types.ts`, `api.ts` | modify | `gate_artifact`, `WorkItemArtifact`, `getWorkItemArtifact`. |
| `frontend/src/components/ArtifactModal.tsx` | create | Read-only Markdown view of the gate's artifact. Sibling of `DiffModal`. |
| `frontend/src/views/WorkItemDetail.tsx` | modify | "Review spec" / "Review plan" in the `Gate` artifact slot. |
| `pyproject.toml` | modify | Ship `skills/**/*` as package data. |
| `CLAUDE.md` | modify | Document `kraft artifact`. |

---

### Task 1: Create the worktree before the first dispatch

`default.yaml` runs `spec` and `plan` *before* `env_setup`, so today the first two nodes dispatch with `cwd` pointed at a directory that does not exist. An agent that must write a file into the worktree cannot wait for `env_setup`. Extract the worktree creation out of the `env_setup` builtin and call it from the executor before any node runs. `env_setup` keeps working — it becomes the same call plus attachment copying.

**Files:**
- Modify: `src/kraft/builtins.py`
- Modify: `src/kraft/executor.py:744` (in `run`) and `src/kraft/executor.py:~893` (in `resume`) — both are the line `worktree = run_dirs.worktrees / work_item_id`
- Test: `tests/test_builtins.py`, `tests/test_executor.py`

**Interfaces:**
- Consumes: `store.set_base_ref(conn, work_item_id, sha)`, `config.git_read(cwd, *args, expected_failure=False)`.
- Produces: `async def ensure_worktree(db, run_dirs, *, repo: str, work_item_id: str) -> Path` in `kraft.builtins`. Idempotent. Raises `RuntimeError` if git fails.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_builtins.py`. That file already imports `asyncio`, `subprocess`, `Path`, `make_repo`, `kraft_builtins`, `db`, `store` and `RunDirs`; add `import pytest`, `from support.harness import _git` (alongside the existing `make_repo` import) and `from kraft.config import git_read`.

```python
def _make_item(database, repo, wid="w1"):
    return database.write(
        lambda c: store.create_work_item(
            c,
            id=wid,
            bead_id="B",
            title="t",
            repo=str(repo),
            chain_template="quick-task",
            chain_definition="{}",
        )
    )


def _base_ref(database, wid="w1"):
    return database.read(
        lambda c: c.execute("SELECT base_ref FROM work_items WHERE id = ?", (wid,)).fetchone()
    )["base_ref"]


def test_ensure_worktree_is_idempotent_and_pins_base_ref_once(tmp_path):
    repo = make_repo(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            await _make_item(database, repo)
            first = await kraft_builtins.ensure_worktree(
                database, rd, repo=str(repo), work_item_id="w1"
            )
            assert first.is_dir()
            pinned = _base_ref(database)
            assert pinned

            # A second commit lands, then a second call: the pin must not move,
            # and the existing worktree must be reused rather than re-added
            # (git refuses to add a worktree at a path that already exists).
            (repo / "second.txt").write_text("x")
            _git(repo, "add", "-A")
            _git(repo, "commit", "-m", "second")
            again = await kraft_builtins.ensure_worktree(
                database, rd, repo=str(repo), work_item_id="w1"
            )
            assert again == first
            assert _base_ref(database) == pinned
        finally:
            await database.close()

    asyncio.run(scenario())


def test_ensure_worktree_reattaches_an_existing_branch(tmp_path):
    """A rejected gate can leave `kraft/<id>` behind with no worktree. The next
    run must check that branch out, not fail on `-b` for a name in use."""
    repo = make_repo(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            await _make_item(database, repo)
            wt = await kraft_builtins.ensure_worktree(
                database, rd, repo=str(repo), work_item_id="w1"
            )
            _git(repo, "worktree", "remove", "--force", str(wt))

            again = await kraft_builtins.ensure_worktree(
                database, rd, repo=str(repo), work_item_id="w1"
            )
            assert again.is_dir()
            assert git_read(again, "rev-parse", "--abbrev-ref", "HEAD") == "kraft/w1"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_ensure_worktree_raises_when_git_fails(tmp_path):
    not_a_repo = tmp_path / "not-a-repo"
    not_a_repo.mkdir()

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            await _make_item(database, not_a_repo)
            with pytest.raises(RuntimeError, match="w1"):
                await kraft_builtins.ensure_worktree(
                    database, rd, repo=str(not_a_repo), work_item_id="w1"
                )
        finally:
            await database.close()

    asyncio.run(scenario())
```

Add to `tests/test_executor.py`. Note the **inline registry**: the shipped `templates/registry.yaml` binds `on.spec.requested` to `builtin: noop` today but to a real `claude` command after Task 8, and this test must keep testing worktree creation rather than quietly launching an agent seven tasks from now.

```python
def test_the_first_node_runs_before_env_setup_and_still_has_a_worktree(tmp_path):
    """default.yaml puts `spec` first and `env_setup` fourth, so the executor —
    not the env_setup node — is what guarantees the first task has a checkout
    to run in (Kraft-bmp)."""
    repo = make_repo(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            registry = Registry(
                hooks={"on.spec.requested": {"kind": "builtin", "handler": "noop"}}
            )
            chain = {
                "template_id": "t",
                "nodes": [
                    {"id": "spec", "tasks": ["on.spec.requested"], "gate_after": None,
                     "fix_loop": None}
                ],
            }
            await database.write(
                lambda c: store.create_work_item(
                    c,
                    id="w1",
                    bead_id="B",
                    title="t",
                    repo=str(repo),
                    chain_template="t",
                    chain_definition=json.dumps(chain),
                )
            )
            await executor.run(database, rd, work_item_id="w1", registry=registry)
            assert (rd.worktrees / "w1").is_dir()
        finally:
            await database.close()

    asyncio.run(scenario())
```

`Registry` comes from `kraft.templates`; add it to that file's imports if it is not already there.

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/test_builtins.py -k ensure_worktree -v
uv run pytest tests/test_executor.py -k before_env_setup -v
```

Expected: `AttributeError: module 'kraft.builtins' has no attribute 'ensure_worktree'`, and the executor test failing on a missing worktree directory.

- [ ] **Step 3: Write the implementation**

In `src/kraft/builtins.py`, add these imports at the top:

```python
import asyncio
import subprocess
```

Add `ensure_worktree` and the shared session-row helper, and rewrite `env_setup` and `noop` around them:

```python
async def ensure_worktree(db, run_dirs, *, repo: str, work_item_id: str) -> Path:
    """The item's worktree, created if it is not there yet.

    Called by the executor before the first node dispatches, not only by the
    `env_setup` builtin: `default.yaml` runs `spec` and `plan` ahead of
    `env_setup`, and an agent asked to write a file into a directory that does
    not exist fails in a way no chain can recover from (Kraft-bmp). `env_setup`
    still exists — it is this call plus the attachment copy.

    Idempotent in both directions: an existing worktree is returned untouched,
    and an existing `kraft/<id>` branch (a rejected gate removes the worktree
    but keeps the branch) is checked out rather than re-created.
    """
    worktree = run_dirs.worktrees / work_item_id
    if worktree.is_dir():
        return worktree
    # Pin the base before the worktree exists, so the early return above
    # guarantees a crashed-and-retried run never re-pins to a moved HEAD.
    row = db.read(
        lambda c: c.execute(
            "SELECT base_ref FROM work_items WHERE id = ?", (work_item_id,)
        ).fetchone()
    )
    if row is not None and row["base_ref"] is None:
        head = git_read(Path(repo), "rev-parse", "HEAD")
        if head:
            await db.write(lambda c: store.set_base_ref(c, work_item_id, head))
        else:
            # Without a base_ref the diff endpoint answers "no diff available"
            # for the rest of the item's life; the reason belongs in the log
            # rather than in a reviewer's guesswork.
            logger.warning("no base_ref for %s: rev-parse HEAD failed in %s", work_item_id, repo)
    branch = f"kraft/{work_item_id}"
    exists = git_read(
        Path(repo), "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}",
        expected_failure=True,
    )
    # A worktree directory deleted out from under git leaves a stale
    # administrative entry that makes `worktree add` refuse the same path.
    await asyncio.to_thread(
        subprocess.run, ["git", "worktree", "prune"], cwd=repo, capture_output=True, text=True
    )
    args = ["git", "worktree", "add"]
    args += [str(worktree), branch] if exists else [str(worktree), "-b", branch]
    done = await asyncio.to_thread(
        subprocess.run, args, cwd=repo, capture_output=True, text=True
    )
    if done.returncode != 0:
        # Raised, not returned: this runs outside a session, so there is no
        # session status to carry the failure. `api._guard` turns it into
        # needs_human with the git stderr in the reason.
        raise RuntimeError(
            f"git worktree add failed for {work_item_id}: {done.stderr.strip() or done.stdout.strip()}"
        )
    return worktree


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
) -> str:
    """A session row for a builtin that did its work in-process. Shared so a
    builtin's bookkeeping cannot drift from `noop`'s."""
    log_path = run_dirs.logs / f"{session_id}.log"
    result_path = run_dirs.results / f"{session_id}.json"
    log_path.write_text(log)
    await db.write(
        lambda c: store.create_session(
            c,
            id=session_id,
            work_item_id=work_item_id,
            node_id=node_id,
            hook_point=hook_point,
            log_path=str(log_path),
            result_path=str(result_path),
            round=round,
        )
    )
    await db.write(lambda c: store.session_exited(c, session_id, "done"))
    return "done"


async def env_setup(
    db,
    run_dirs,
    *,
    session_id: str,
    work_item_id: str,
    node_id: str,
    repo: str,
    round: int = 0,
    attachments: list[dict] | None = None,
) -> str:
    worktree = await ensure_worktree(db, run_dirs, repo=repo, work_item_id=work_item_id)
    _copy_attachments(Path(repo), worktree, attachments or [])
    return await _record_done(
        db,
        run_dirs,
        session_id=session_id,
        work_item_id=work_item_id,
        node_id=node_id,
        hook_point="on.env.prepare",
        round=round,
        log=f"worktree ready at {worktree}\n",
    )


async def noop(
    db,
    run_dirs,
    *,
    session_id: str,
    work_item_id: str,
    node_id: str,
    hook_point: str,
    round: int = 0,
) -> str:
    """Placeholder task for a hook with no plugin yet: records a done session, does no work."""
    return await _record_done(
        db,
        run_dirs,
        session_id=session_id,
        work_item_id=work_item_id,
        node_id=node_id,
        hook_point=hook_point,
        round=round,
        log=f"noop placeholder for {hook_point}\n",
    )
```

`_copy_attachments` now runs on every `env_setup`, including one whose worktree already existed. It already skips a destination that exists and refuses to write through a symlink, so re-running it is a no-op — which is what makes the early-return-and-skip-copies branch it replaced unnecessary.

In `src/kraft/executor.py`, in **both** `run` and `resume`, replace the line

```python
    worktree = run_dirs.worktrees / work_item_id
```

with

```python
    # Before the first dispatch, not inside the `env_setup` node: `default.yaml`
    # runs `spec` and `plan` first, and both need a checkout to write into.
    worktree = await _builtins.ensure_worktree(
        db, run_dirs, repo=row["repo"], work_item_id=work_item_id
    )
```

- [ ] **Step 4: Run the tests**

```bash
uv run pytest tests/test_builtins.py tests/test_executor.py -v
```

Expected: PASS, including the pre-existing `test_env_setup_creates_worktree_and_branch` and the attachment-copy tests — `env_setup` still records a session row with hook point `on.env.prepare` and status `done`.

Two behaviour changes no current test covers, both intended:

- A re-entered `env_setup` whose worktree already exists previously returned early and recorded **no** session row (`src/kraft/builtins.py:54-57`); it now records one every call. A node that ran is a node with a session, which is what the detail screen's task list assumes.
- A git failure previously produced a `failed` session via `_subprocess.run_task`; it now raises, with no session row, and surfaces through `api._guard` as needs_human with git's stderr in the reason. That is the only option available to `ensure_worktree`, which runs outside any session.

- [ ] **Step 5: Commit**

```bash
git add src/kraft/builtins.py src/kraft/executor.py tests/test_builtins.py tests/test_executor.py
git commit -m "Ensure the worktree in code, before the first node dispatches"
```

---

### Task 2: The skill module

`skill:` on a hook binding names the *method* an agent follows. Resolution is: a value containing `:` is a plugin reference (passed through as an instruction, since Kraft cannot see another tool's plugin cache); anything else is a bare name resolved against the operator overlay `$KRAFT_HOME/skills/<name>/SKILL.md`, falling back to the bundled `src/kraft/skills/<name>/SKILL.md`.

The module is `skill.py`, singular, because `src/kraft/skills/` is a data directory. It deliberately has no `__init__.py` so `[tool.setuptools.packages.find]` skips it and it ships as package data instead.

**Files:**
- Create: `src/kraft/skill.py`
- Modify: `src/kraft/paths.py`
- Modify: `pyproject.toml`
- Move: `skills/chain-review/SKILL.md` → `src/kraft/skills/chain-review/SKILL.md`
- Modify: `tests/test_chain_review_skill.py:16`
- Test: `tests/test_skill.py`

**Interfaces:**
- Produces, in `kraft.skill`: `BUNDLED: Path`, `HEADING: str`, `UNAVAILABLE: str`, `PLUGIN_PROMPT: str`, `class SkillError(Exception)`, `is_plugin_ref(value: str) -> bool`, `path_for(skills_dir: Path | None, name: str, *, where: str) -> Path`, `validate(skills_dir: Path | None, value, *, where: str) -> None`, `read(skills_dir: Path | None, value: str) -> str`.
- Produces, in `kraft.paths`: `default_skills_dir() -> Path`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_skill.py`:

```python
from pathlib import Path

import pytest

from kraft import skill


def _overlay(tmp_path: Path, name: str, body: str) -> Path:
    (tmp_path / name).mkdir(parents=True)
    (tmp_path / name / "SKILL.md").write_text(body)
    return tmp_path


def test_a_bundled_skill_resolves_without_an_overlay(tmp_path):
    assert skill.read(tmp_path, "chain-review").startswith("---")


def test_an_overlay_wins_over_the_bundled_copy(tmp_path):
    _overlay(tmp_path, "chain-review", "operator's own method")
    assert skill.read(tmp_path, "chain-review") == "operator's own method"


def test_an_unknown_skill_raises(tmp_path):
    with pytest.raises(skill.SkillError, match="nope"):
        skill.validate(tmp_path, "nope", where="registry.yaml")


def test_a_plugin_reference_is_passed_through_without_a_lookup(tmp_path):
    # No file anywhere named `superpowers:brainstorming`; validation must not
    # look for one, and read must return the instruction, not a body.
    skill.validate(tmp_path, "superpowers:brainstorming", where="registry.yaml")
    assert "superpowers:brainstorming" in skill.read(tmp_path, "superpowers:brainstorming")


@pytest.mark.parametrize("name", ["../etc/passwd", "a/b", "a\\b", ".", "", ".hidden"])
def test_a_name_that_could_escape_the_skills_directory_is_rejected(tmp_path, name):
    with pytest.raises(skill.SkillError):
        skill.validate(tmp_path, name, where="registry.yaml")


def test_a_non_string_is_rejected(tmp_path):
    with pytest.raises(skill.SkillError):
        skill.validate(tmp_path, ["spec"], where="registry.yaml")
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest tests/test_skill.py -v
```

Expected: `ModuleNotFoundError: No module named 'kraft.skill'`.

- [ ] **Step 3: Move the bundled skill and write the module**

```bash
mkdir -p src/kraft/skills
git mv skills/chain-review src/kraft/skills/chain-review
rmdir skills 2>/dev/null || true
```

Update `tests/test_chain_review_skill.py:16`:

```python
SKILL = Path(__file__).parent.parent / "src" / "kraft" / "skills" / "chain-review" / "SKILL.md"
```

Add to `src/kraft/paths.py`, next to `default_templates_dir`:

```python
def default_skills_dir() -> Path:
    """Where an operator may override a bundled method file.

    Not seeded by `cli.seed_home` and usually absent: the shipped skills live in
    the package (`kraft.skill.BUNDLED`), and this directory exists only when
    someone has deliberately overridden one.
    """
    return kraft_home() / "skills"
```

Create `src/kraft/skill.py`:

```python
"""The *method* an agent hook follows, injected through the system prompt.

Sibling of `steering.py`, and deliberately shaped like it: same bare-name rule,
same validate/read split, same reason for the split — the value must round-trip
through `GET /registry` and the Settings screens as a name, so resolving it into
the config dict would inline a method body into the operator's YAML.

Where it differs is the fallback. Steering is operator-authored and exists only
under `$KRAFT_HOME`; a method is Kraft-authored and *shipped*, with the operator
directory as an optional overlay on top. And a value containing `:` is not a
file at all: it names a skill in some other tool's plugin system, which Kraft
cannot read, so the agent is told to load it by name.
"""

from __future__ import annotations

from pathlib import Path

#: Method files that ship with Kraft. Read from the package, never from
#: `$KRAFT_HOME/templates/`, because `cli.seed_home` copies templates once and
#: never again — a shipped method would then be frozen at whichever version the
#: operator first installed.
BUNDLED = Path(__file__).parent / "skills"

#: What `adapters/agent.py` wraps the method text in.
HEADING = "\n\n## Method\n\n"

#: Appended after the method text, whatever its source. A plugin reference can
#: name a skill this agent's installation does not have; without this the agent
#: improvises a method and the human finds out at the gate.
UNAVAILABLE = (
    "\n\nIf you cannot load the method named above, stop with status "
    '"needs_context" and say in "question" exactly which method was missing. Do '
    "not improvise a method of your own."
)

#: What a `provider:name` value becomes. Kraft cannot read another tool's plugin
#: cache, so the reference is handed to the agent, which can.
PLUGIN_PROMPT = "Follow the {ref} skill for how to do this work. Load it before you start."


class SkillError(Exception):
    pass


def is_plugin_ref(value: str) -> bool:
    """A `:` means "somebody else's skill system", not a file name."""
    return ":" in value


def _local_path(skills_dir: Path | None, name: str, where: str) -> Path:
    if "/" in name or "\\" in name or name in ("", ".", "..") or name.startswith("."):
        raise SkillError(
            f"{where}: skill name {name!r} must be a bare directory name — Kraft "
            "never reads a method file from outside its own skills directories"
        )
    if skills_dir is not None:
        overlay = Path(skills_dir) / name / "SKILL.md"
        if overlay.is_file():
            return overlay
    bundled = BUNDLED / name / "SKILL.md"
    if bundled.is_file():
        return bundled
    looked = [str(bundled)]
    if skills_dir is not None:
        looked.insert(0, str(Path(skills_dir) / name / "SKILL.md"))
    raise SkillError(f"{where}: skill {name!r} not found at {' or '.join(looked)}")


def path_for(skills_dir: Path | None, name: str, *, where: str) -> Path:
    """The file a bare skill name resolves to, or raise. Not for plugin refs."""
    return _local_path(skills_dir, name, where)


def validate(skills_dir: Path | None, value, *, where: str) -> None:
    """The value is a usable skill reference, or raise.

    A plugin reference is accepted without a lookup: whether the agent's
    installation has it is not knowable here, and the injected text tells the
    agent to stop with `needs_context` if it cannot load it.
    """
    if not isinstance(value, str) or not value:
        raise SkillError(f"{where}: 'skill' must be a non-empty string")
    if is_plugin_ref(value):
        return
    _local_path(skills_dir, value, where)


def read(skills_dir: Path | None, value: str) -> str:
    """The method text to inject. Called at dispatch, after `validate`."""
    if is_plugin_ref(value):
        return PLUGIN_PROMPT.format(ref=value)
    path = _local_path(skills_dir, value, "skill")
    try:
        return path.read_text()
    except (OSError, ValueError) as exc:
        raise SkillError(f"skill: cannot read {value!r} at {path}: {exc}") from exc
```

In `pyproject.toml`, extend the package-data line:

```toml
[tool.setuptools.package-data]
kraft = ["_bundled/**/*", "skills/**/*"]
```

- [ ] **Step 4: Run the tests**

```bash
uv run pytest tests/test_skill.py tests/test_chain_review_skill.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A src/kraft/skill.py src/kraft/skills src/kraft/paths.py pyproject.toml tests/test_skill.py tests/test_chain_review_skill.py
git commit -m "Add the skill module: bundled method files with an operator overlay"
```

---

### Task 3: Registry validation for `skill:` and `artifact:`

Both keys are agent-only. `artifact:` becomes a path segment, so it is restricted to a bare lowercase identifier.

**Files:**
- Modify: `src/kraft/templates.py:63` (`load_registry` signature), `:92` (`agent_only`), `:104-113` (agent validation)
- Modify: `src/kraft/api.py:92`, `:1282`, `:1552`, `:1646` (the `load_registry` call sites) and the lifespan
- Test: `tests/test_templates.py`

**Interfaces:**
- Consumes: `kraft.skill.validate`, `kraft.paths.default_skills_dir`.
- Produces: `load_registry(path, *, steering_dir: Path | None = None, skills_dir: Path | None = None) -> Registry`. `skills_dir=None` means `default_skills_dir()`.
- Produces: `app.state.skills_dir: Path`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_templates.py`, following that file's existing write-a-registry-then-load idiom:

```python
def _registry(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "registry.yaml"
    path.write_text(body)
    return path


def test_an_agent_hook_accepts_skill_and_artifact(tmp_path):
    path = _registry(
        tmp_path,
        "hooks:\n"
        "  on.spec.requested: { kind: agent, command: claude, "
        "skill: chain-review, artifact: spec }\n",
    )
    reg = templates.load_registry(path, skills_dir=tmp_path / "skills")
    assert reg.hooks["on.spec.requested"]["artifact"] == "spec"


def test_an_unknown_skill_fails_the_registry(tmp_path):
    path = _registry(
        tmp_path,
        "hooks:\n  on.spec.requested: { kind: agent, command: claude, skill: nope }\n",
    )
    with pytest.raises(templates.RegistryError, match="nope"):
        templates.load_registry(path, skills_dir=tmp_path / "skills")


def test_a_bad_artifact_kind_fails_the_registry(tmp_path):
    path = _registry(
        tmp_path,
        "hooks:\n  on.spec.requested: { kind: agent, command: claude, artifact: ../etc }\n",
    )
    with pytest.raises(templates.RegistryError, match="artifact"):
        templates.load_registry(path, skills_dir=tmp_path / "skills")


def test_skill_and_artifact_are_agent_only(tmp_path):
    path = _registry(tmp_path, "hooks:\n  on.merge: { kind: builtin, handler: noop, skill: spec }\n")
    with pytest.raises(templates.RegistryError, match="only to an agent hook"):
        templates.load_registry(path, skills_dir=tmp_path / "skills")
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest tests/test_templates.py -k "skill or artifact" -v
```

Expected: FAIL — `load_registry() got an unexpected keyword argument 'skills_dir'`.

- [ ] **Step 3: Write the implementation**

In `src/kraft/templates.py`, add at the top:

```python
import re

from kraft import skill as _skill
from kraft import steering as _steering
from kraft.paths import default_skills_dir
```

and a module constant next to `GATE_NAMES`:

```python
#: An `artifact:` value becomes a path segment (`.engineering/<kind>s/<id>.md`),
#: so it is a bare lowercase identifier — not a path, not a pattern.
_ARTIFACT_KIND = re.compile(r"[a-z][a-z0-9_-]*")
```

Change the signature and add the default:

```python
def load_registry(
    path: str | Path,
    *,
    steering_dir: Path | None = None,
    skills_dir: Path | None = None,
) -> Registry:
    path = Path(path)
    steering_dir = steering_dir if steering_dir is not None else path.parent / "steering"
    # Not `path.parent / "skills"`: a method file is shipped in the package and
    # only *overlaid* from $KRAFT_HOME, so the default is the home directory,
    # not a sibling of whichever registry file is being validated.
    skills_dir = skills_dir if skills_dir is not None else default_skills_dir()
```

Extend `agent_only` (line 92):

```python
        agent_only = ("profile", "model", "escalate_model", "deny_tools", "steering", "skill", "artifact")
```

(Wrap it to stay under 100 columns — put the tuple on its own indented lines.)

Inside the `if kind == "agent":` block, after the existing `_steering.validate` try/except, add:

```python
            if "skill" in binding:
                try:
                    _skill.validate(skills_dir, binding["skill"], where=path.name)
                except _skill.SkillError as exc:
                    raise RegistryError(str(exc)) from exc
            if "artifact" in binding:
                art = binding["artifact"]
                if not isinstance(art, str) or not _ARTIFACT_KIND.fullmatch(art):
                    raise RegistryError(
                        f"{path.name}: hook {hook!r} 'artifact' must be a bare lowercase "
                        f"kind like 'spec' or 'plan'; got {art!r}"
                    )
```

In `src/kraft/api.py`, in `lifespan`, right after `app.state.templates_dir = templates_dir`:

```python
    # Where an operator may override a bundled method file. Absent on almost
    # every install; `kraft.skill` falls back to the packaged copy.
    app.state.skills_dir = Path(os.environ.get("KRAFT_SKILLS_DIR") or default_skills_dir())
```

Import `default_skills_dir` from `kraft.paths` alongside the existing names, then pass `skills_dir=` at each `load_registry` call site:

- line 92: `registry = load_registry(templates_dir / "registry.yaml", skills_dir=app.state.skills_dir)`
- line 1282 (`_reload_templates`) becomes exactly:
  ```python
      st.registry = load_registry(st.templates_dir / "registry.yaml", skills_dir=st.skills_dir)
  ```
  It passes no `steering_dir` today and keeps passing none: `load_registry` defaults it to `path.parent / "steering"`, which is the same directory.
- line 1552 (`put_registry`): add `skills_dir=st.skills_dir` to the existing call, keeping its `steering_dir=`.
- line 1646 (steering validation against a scratch dir): add `skills_dir=st.skills_dir`, keeping its `steering_dir=scratch`.

- [ ] **Step 4: Run the tests**

```bash
uv run pytest tests/test_templates.py tests/test_api.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/kraft/templates.py src/kraft/api.py tests/test_templates.py
git commit -m "Validate skill: and artifact: on agent hook bindings"
```

---

### Task 4: Inject the artifact contract and the method

The adapter gains the two things a spec/plan agent needs that an implementation agent does not: the exact path to write, and the method to follow.

**Files:**
- Modify: `src/kraft/adapters/agent.py`
- Modify: `src/kraft/executor.py` (`LaunchContext`, `_dispatch`)
- Modify: `src/kraft/api.py` (`_launch`)
- Test: `tests/test_adapters_agent.py`

**Interfaces:**
- Consumes: `kraft.skill.read`, `kraft.skill.HEADING`, `kraft.skill.UNAVAILABLE`.
- Produces: `artifact_path(kind: str, work_item_id: str) -> str` in `kraft.adapters.agent` — returns `.engineering/{kind}s/{work_item_id}.md`. **This is the one definition of the artifact path; Task 7 calls it, nothing re-spells it.**
- Produces: `Invocation` gains a sixth field `method_text: str | None = None`.
- Produces: `resolve_invocation(binding, repo_entry, steering_dir, *, skills_dir: Path | None = None, escalate: bool = False)`.
- Produces: `run_agent_task(..., artifact: str | None = None, method_text: str | None = None)`.
- Produces: `LaunchContext(repo_entry, steering_dir, skills_dir=None)`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_adapters_agent.py`, reusing that file's helpers exactly as they are defined: `_capture_cmd(monkeypatch)` returns the dict `seen` and the argv lands at `seen["cmd"]`; `_system_prompt(cmd)` takes the **argv list**, so it is always called as `_system_prompt(seen["cmd"])` (see `tests/test_adapters_agent.py:385-405, 493`). `_run(**overrides)` already defaults `work_item_id="w1"` and forwards every override into `run_agent_task`. Add `from kraft import skill` to the imports.

```python
def test_an_artifact_binding_names_the_exact_path_in_the_system_prompt(monkeypatch):
    seen = _capture_cmd(monkeypatch)
    _run(artifact="spec")
    prompt = _system_prompt(seen["cmd"])
    assert ".engineering/specs/w1.md" in prompt
    assert "commit" in prompt


def test_no_artifact_and_no_method_leave_the_system_prompt_byte_identical(monkeypatch):
    seen = _capture_cmd(monkeypatch)
    _run()
    without = _system_prompt(seen["cmd"])
    seen2 = _capture_cmd(monkeypatch)
    _run(artifact=None, method_text=None)
    assert _system_prompt(seen2["cmd"]) == without


def test_method_text_is_injected_under_the_method_heading(monkeypatch):
    seen = _capture_cmd(monkeypatch)
    _run(method_text="Write it in one page.")
    prompt = _system_prompt(seen["cmd"])
    assert skill.HEADING + "Write it in one page." in prompt
    assert prompt.endswith(skill.UNAVAILABLE)


def test_the_artifact_block_precedes_the_method_block(monkeypatch):
    """Spec §2.6 fixes the order: what to produce, then how to produce it."""
    seen = _capture_cmd(monkeypatch)
    _run(artifact="spec", method_text="Write it in one page.")
    prompt = _system_prompt(seen["cmd"])
    assert prompt.index(".engineering/specs/w1.md") < prompt.index(skill.HEADING)


def test_resolve_invocation_reads_the_skill(tmp_path):
    inv = agent.resolve_invocation(
        {"command": "c", "skill": "chain-review"}, None, None, skills_dir=tmp_path
    )
    assert inv.method_text.startswith("---")


def test_resolve_invocation_without_a_skill_carries_no_method(tmp_path):
    inv = agent.resolve_invocation({"command": "c"}, None, None, skills_dir=tmp_path)
    assert inv.method_text is None


def test_artifact_path_is_the_kind_pluralised():
    assert agent.artifact_path("spec", "w1") == ".engineering/specs/w1.md"
    assert agent.artifact_path("plan", "w1") == ".engineering/plans/w1.md"
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest tests/test_adapters_agent.py -k "artifact or method" -v
```

Expected: FAIL — `run_agent_task() got an unexpected keyword argument 'artifact'`.

- [ ] **Step 3: Write the implementation**

In `src/kraft/adapters/agent.py`, add the import and the two new module-level pieces:

```python
from kraft import skill as _skill
```

```python
def artifact_path(kind: str, work_item_id: str) -> str:
    """Where a hook with `artifact: <kind>` must write its document.

    Derived on both sides — injected here, read back by
    `GET /work-items/{wid}/artifact` — rather than stored on the work item.
    Nothing to migrate, nothing to go stale, and a rerun after a rejected gate
    revises the same file instead of leaving a stale pointer behind.
    """
    return f".engineering/{kind}s/{work_item_id}.md"


#: The contract a hook with an `artifact:` binding is held to. Not part of the
#: method: the method says *how* to think about a spec, this says where the
#: result goes and how a reviewer will find it. Swapping the method must not be
#: able to lose the contract.
_ARTIFACT = (
    "\n\nWrite your {kind} to {path}, relative to the repo root, and to no other "
    "path. Start it with YAML front matter carrying exactly these keys:\n"
    "---\n"
    "work_item_ids: [{work_item_id}]\n"
    "node_id: {node_id}\n"
    "hook_point: {hook_point}\n"
    "kind: {kind}s\n"
    "title: <a one-line title for this {kind}>\n"
    "---\n"
    "If that file already exists, a human has read it and asked for changes: "
    "revise it in place rather than starting a new one.\n"
    "Before you exit, `git add` that file and commit it. A human reviews it at "
    "the gate that follows this node, and an uncommitted file is invisible to "
    "them."
)
```

Add the field to `Invocation` (last, so positional construction in tests keeps working):

```python
class Invocation(NamedTuple):
    command: str
    profile: str
    model: str | None
    deny_tools: tuple[str, ...]
    steering_texts: tuple[str, ...]
    method_text: str | None = None
```

In `resolve_invocation`, add the keyword-only parameter and resolve the method just before the `return`:

```python
def resolve_invocation(
    binding: dict,
    repo_entry: dict | None,
    steering_dir: Path | None,
    *,
    skills_dir: Path | None = None,
    escalate: bool = False,
) -> Invocation:
```

```python
    # Hook-level only, deliberately: a method is what this *hook* does, where
    # steering is what a repo demands of every hook. A repo-level default would
    # make one hook's method depend on which repo it ran in.
    method_text = _skill.read(skills_dir, binding["skill"]) if binding.get("skill") else None
```

and add `method_text=method_text,` to the returned `Invocation`.

In `run_agent_task`, add the two parameters after `review_package`:

```python
    review_package: str | None = None,
    artifact: str | None = None,
    method_text: str | None = None,
) -> str:
```

and extend the ctx assembly. Spec §2.6 fixes the order — `_CTX`, artifact block, review-package note, method text, steering — so these two blocks are inserted **immediately after `ctx = _CTX.format(...)` and before the `if review_package:` block**, with the method block after the review-package block:

Directly after `ctx = _CTX.format(...)`:

```python
    if artifact:
        ctx += _ARTIFACT.format(
            kind=artifact,
            path=artifact_path(artifact, work_item_id),
            work_item_id=work_item_id,
            node_id=node_id,
            hook_point=hook_point,
        )
```

Then the existing `if review_package:` block, unchanged, and directly after it:

```python
    if method_text:
        # After the contract, before steering: the agent reads what it must
        # produce, then how to produce it, then the house rules that apply to
        # everything. Steering stays last so it is never buried.
        ctx += _skill.HEADING + method_text + _skill.UNAVAILABLE
```

Then the existing `if steering_texts:` block, unchanged.

In `src/kraft/executor.py`, add the third field to `LaunchContext`:

```python
    skills_dir: Path | None = None
```

with a line in its docstring: *"`skills_dir` is where an operator may override a bundled method file; `None` means the packaged copies only."*

In `_dispatch`, pass it through:

```python
        inv = _agent.resolve_invocation(
            binding,
            launch.repo_entry if launch else None,
            launch.steering_dir if launch else None,
            skills_dir=launch.skills_dir if launch else None,
            escalate=escalate,
        )
```

and add to the `run_agent_task(...)` call:

```python
            artifact=binding.get("artifact"),
            method_text=inv.method_text,
```

In `src/kraft/api.py`, in `_launch`, add `skills_dir=st.skills_dir` to **both** `LaunchContext(...)` constructions (the degraded one at line ~1423 and the normal one at ~1424).

- [ ] **Step 4: Run the tests**

```bash
uv run pytest tests/test_adapters_agent.py tests/test_executor.py -v
```

Expected: PASS, including `test_no_steering_leaves_the_system_prompt_byte_identical` — an implementation hook with neither key must produce exactly the prompt it produced before.

- [ ] **Step 5: Commit**

```bash
git add src/kraft/adapters/agent.py src/kraft/executor.py src/kraft/api.py tests/test_adapters_agent.py
git commit -m "Inject the artifact contract and the hook's method into the agent prompt"
```

---

### Task 5: The spec and plan method files

Two Markdown files. They describe *how to think*, and must not restate the contract — the path, the front matter and the commit come from `_ARTIFACT`, and duplicating them here is how they drift.

**Files:**
- Create: `src/kraft/skills/spec/SKILL.md`
- Create: `src/kraft/skills/plan/SKILL.md`
- Test: `tests/test_planning_skills.py`

**Interfaces:**
- Consumes: `kraft.skill.read(None, "spec")`, `kraft.skill.read(None, "plan")`.
- Produces: nothing importable — data read by Task 3's validation and Task 4's injection.

- [ ] **Step 1: Write the failing test**

Create `tests/test_planning_skills.py`:

```python
"""The two shipped methods are data, so what can be tested is what they must
not do: re-state the contract (which would drift from `_ARTIFACT`), or assume
an interactive human (a hook agent runs headless)."""

import pytest

from kraft import skill

#: Per skill, because the plan method legitimately names its *input* directory
#: (`.engineering/specs/`, where the approved spec is) while nothing may name
#: its own *output* path — that is `_ARTIFACT`'s to state.
FORBIDDEN = {
    "spec": (".engineering/", "front matter", "git add", "git commit"),
    "plan": (".engineering/plans", "front matter", "git add", "git commit"),
}


@pytest.mark.parametrize("name", ["spec", "plan"])
def test_the_method_loads_and_has_front_matter(name):
    text = skill.read(None, name)
    assert text.startswith("---\n")
    assert f"name: {name}" in text


@pytest.mark.parametrize("name", ["spec", "plan"])
def test_the_method_does_not_restate_the_injected_contract(name):
    text = skill.read(None, name).lower()
    for phrase in FORBIDDEN[name]:
        assert phrase.lower() not in text, f"{name}: the contract block owns {phrase!r}"


@pytest.mark.parametrize("name", ["spec", "plan"])
def test_the_method_routes_questions_through_needs_context(name):
    assert "needs_context" in skill.read(None, name)
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest tests/test_planning_skills.py -v
```

Expected: FAIL — `SkillError: skill: skill 'spec' not found`.

- [ ] **Step 3: Write the method files**

`src/kraft/skills/spec/SKILL.md`:

```markdown
---
name: spec
description: Turn a work item title into a design a human can approve or reject in one read.
---

# Writing a spec, headless

You are writing the design for one work item, with no human in the loop. A
human will read what you produce and either approve it or reject it with a
note. Write for that reader.

## Before you write

Read the code the change touches. Trace the actual flow end to end — the
files, the callers, the tests that cover them. A design argued from a guess
about the codebase is worse than no design, because it reads as authoritative.

## Asking

You cannot ask a question and keep working. If something is genuinely
undecidable from the repo — a product choice, a tradeoff only the requester
can settle — stop with status `needs_context` and put **every** open question
in the one `question` field, numbered. Stopping once per question costs a full
relaunch each time.

Do not stop for anything you can decide yourself. Pick the option a competent
engineer on this codebase would pick, and say in the spec that you picked it
and why.

## What the spec contains

- **The problem**, in the terms of this repo: what is broken or missing today,
  with file and function names.
- **The approach**, and the two or three you rejected, one sentence each on
  why. A reviewer disagreeing with the choice needs to see the alternatives to
  say so.
- **Sections scaled to their complexity.** A component that is three lines gets
  a sentence. Do not pad.
- **What is explicitly out of scope.** Half of what a reviewer rejects is scope
  they did not expect.
- **How it will be verified**: the tests that must exist, named.

Leave no "TBD", no "to be determined later", no section that describes what a
decision will be about instead of making it. If you cannot resolve something,
that is a `needs_context` stop, not a placeholder.

## If you are revising

An existing document at your output path means a human read it and asked for
changes. Their note leads your task instruction. Address it directly: change
what they objected to, and leave what they did not object to alone. Do not
rewrite the whole thing to look new.
```

`src/kraft/skills/plan/SKILL.md`:

```markdown
---
name: plan
description: Turn an approved spec into an implementation plan of bite-sized, independently testable tasks.
---

# Writing a plan, headless

The spec for this work item is already written and already approved by a human:
read `.engineering/specs/` for the document whose name matches this work item
before you do anything else. If it is not there, the chain skipped the spec node
because a human attached one at intake — look for the attached document instead.
Your plan implements that spec and argues from it. If you find yourself
disagreeing with the spec, say so in the plan; do not quietly design something
else.

## Asking

Same rule as any hook: if the spec leaves something genuinely undecidable, stop
with status `needs_context` and put every open question in the one `question`
field. Anything you can settle by reading the code, settle by reading the code.

## What the plan contains

A list of tasks. A task is the smallest unit that carries its own test cycle
and is worth a fresh reviewer's gate. Fold setup, configuration and
documentation into the task whose deliverable needs them. Split only where a
reviewer could reject one task and approve its neighbour.

Each task names:

- the exact files it creates, modifies (with line numbers) and tests;
- what it consumes from earlier tasks and what later tasks consume from it —
  exact names, exact types;
- the failing test, written out in full;
- the implementation, written out in full;
- the command that runs the test, and what it prints when it passes.

Write for an engineer who is a strong developer and knows nothing about this
codebase. "Add appropriate error handling", "similar to task 3", "write tests
for the above" are plan failures — the reader may be reading your tasks out of
order and cannot resolve any of them.

## Check your own plan before you finish

Walk the spec section by section and point at the task that implements each
one. A section with no task is a gap: add the task. Then check that a name you
used in a late task is spelled the same way as where you defined it.
```

- [ ] **Step 4: Run the tests**

```bash
uv run pytest tests/test_planning_skills.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/kraft/skills tests/test_planning_skills.py
git commit -m "Add the spec and plan method files"
```

---

### Task 6: Link specs and plans to their work item in the index

`scan_repo` reads `work_item_ids` front matter only from session summaries, so a spec carrying that key produces no link rows and never appears in `kraft docs`. This is Kraft-2k4, and it is what makes the new artifacts discoverable.

**Files:**
- Modify: `src/kraft/index/ingest.py:~154`
- Test: `tests/test_index_ingest.py`

**Interfaces:**
- Consumes: `links_from_front_matter(fm) -> list[LinkRow]` (unchanged).
- Produces: no signature change.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_index_ingest.py`:

```python
def test_a_spec_with_work_item_ids_is_linked(tmp_path):
    repo = make_repo_with_engineering(
        tmp_path,
        {
            ".engineering/specs/w1.md": (
                "---\nwork_item_ids: [w1]\nkind: specs\ntitle: A spec\n---\n\nbody\n"
            )
        },
    )
    doc = next(d for d in ingest.scan_repo(repo) if d.path.endswith("specs/w1.md"))
    assert [link.work_item_id for link in doc.links] == ["w1"]
```

`make_repo_with_engineering(tmp_path, files)` (`tests/support/harness.py:32`) takes the repo-relative files as a required dict and commits them, so the test needs no `_git` calls of its own.

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest tests/test_index_ingest.py -k spec_with_work_item_ids -v
```

Expected: FAIL — `assert [] == ['w1']`.

- [ ] **Step 3: Write the implementation**

In `src/kraft/index/ingest.py`, in the `ScannedDoc(...)` construction inside `scan_repo`, replace:

```python
                links=(
                    tuple(links_from_front_matter(fm)) if source_kind == "session_summary" else ()
                ),
```

with:

```python
                # Every document, not only session summaries: a spec or plan an
                # agent wrote carries the same `work_item_ids:` key, and gating
                # on the source kind is what kept them out of `kraft docs`
                # (Kraft-2k4). A document without the key still links to
                # nothing — `links_from_front_matter` returns an empty list.
                links=tuple(links_from_front_matter(fm)),
```

- [ ] **Step 4: Run the tests**

```bash
uv run pytest tests/test_index_ingest.py -v
```

Expected: PASS, including `test_scan_repo_classifies_sessions` — a spec with no front matter still has no links.

- [ ] **Step 5: Commit**

```bash
git add src/kraft/index/ingest.py tests/test_index_ingest.py
git commit -m "Link any document with work_item_ids, not only session summaries"
```

---

### Task 7: Serve the gate's artifact

Two additions to the API: the detail payload says whether the pending gate has an artifact and where, and one endpoint returns its content.

**Files:**
- Modify: `src/kraft/api.py` — a `_gate_artifact` helper near `_pending_gate` (~line 464), a field in `get_work_item` (~line 636), and a new endpoint after `get_work_item_diff` (~line 765)
- Test: `tests/test_artifact_api.py`

**Interfaces:**
- Consumes: `agent.artifact_path(kind, wid)`, `_pending_gate`, `_gate_node_index`, `ingest.split_front_matter`, `ingest.derive_title`, `DIFF_MAX_BYTES`.
- Produces: `gate_artifact: str | None` on `GET /work-items/{wid}`, and `GET /work-items/{wid}/artifact` returning `{work_item_id, path, title, content, truncated, artifact_max_bytes}`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_artifact_api.py`. Its fixtures follow `tests/test_diff_api.py:29-56` exactly — same env vars, same `TestClient(api.app)` — except that the templates directory's registry is rewritten so `on.spec.requested` is an agent hook carrying `artifact: spec` (`artifact` is agent-only, so a noop binding cannot carry it), and the chain is a one-node `default`-shaped template whose gate is `spec_approval`.

```python
"""GET /work-items/{wid}/artifact — what a human reads at a spec or plan gate.

The document is read off the worktree, not out of the index: a reviewer sitting
at an open gate must see the file the agent just wrote, whether or not the
indexer has caught up.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from support.harness import fake_templates_dir, isolated_bd, make_repo

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FAKE_CLAUDE = _REPO_ROOT / "fixtures" / "fake-claude.sh"


def _templates(tmp_path: Path) -> Path:
    """fake_templates_dir, with the spec hook bound to the fake agent and a
    one-node template that stops at spec_approval."""
    d = fake_templates_dir(tmp_path, str(_FAKE_CLAUDE))
    registry = yaml.safe_load((d / "registry.yaml").read_text())
    registry["hooks"]["on.spec.requested"] = {
        "kind": "agent",
        "command": str(_FAKE_CLAUDE),
        "artifact": "spec",
    }
    (d / "registry.yaml").write_text(yaml.safe_dump(registry))
    (d / "spec-only.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "spec-only",
                "nodes": [
                    {
                        "id": "spec",
                        "tasks": ["on.spec.requested"],
                        "gate_after": "spec_approval",
                    }
                ],
            }
        )
    )
    return d


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("KRAFT_BD_CWD", str(isolated_bd(tmp_path)))
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(_templates(tmp_path)))
    # No operator overlay: the bundled method files are what the hook resolves.
    monkeypatch.setenv("KRAFT_SKILLS_DIR", str(tmp_path / "no-skills"))
    monkeypatch.setenv(
        "KRAFT_FRONTEND_DIST", os.environ.get("KRAFT_FRONTEND_DIST") or str(tmp_path / "no-dist")
    )
    import kraft.api as api

    with TestClient(api.app) as c:
        yield c


def _await_gate(client, wid, gate, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if client.get(f"/work-items/{wid}").json().get("pending_gate") == gate:
            return
        time.sleep(0.2)
    raise AssertionError(f"{wid} never reached {gate}")


@pytest.fixture
def item_at_spec_gate(client, tmp_path):
    repo = make_repo(tmp_path)
    wid = client.post(
        "/work-items",
        json={"repo": str(repo), "title": "add a flag", "chain_template": "spec-only"},
    ).json()["id"]
    _await_gate(client, wid, "spec_approval")
    return wid


@pytest.fixture
def worktree(client, item_at_spec_gate) -> Path:
    return Path(client.get(f"/work-items/{item_at_spec_gate}").json()["worktree_path"])


def _write_artifact(worktree: Path, wid: str, text: str) -> Path:
    """The document a compliant agent would have written.

    Written by the test, not by the fake agent: `fixtures/fake-claude.sh` does
    not honour the artifact contract until Task 8, and these tests are about
    the endpoint, not about the fake.
    """
    path = worktree / ".engineering" / "specs" / f"{wid}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_gate_artifact_names_the_file_the_hook_wrote(client, item_at_spec_gate, worktree):
    _write_artifact(worktree, item_at_spec_gate, "---\ntitle: A spec\n---\n\nthe body\n")

    body = client.get(f"/work-items/{item_at_spec_gate}").json()
    assert body["gate_artifact"] == f".engineering/specs/{item_at_spec_gate}.md"

    art = client.get(f"/work-items/{item_at_spec_gate}/artifact").json()
    assert art["title"] == "A spec"
    assert art["content"].strip() == "the body"
    assert art["truncated"] is False


def test_a_gate_whose_agent_wrote_nothing_reports_no_artifact(client, item_at_spec_gate):
    """An agent can report done without honouring the contract. The gate stays
    answerable; there is just nothing to read."""
    assert client.get(f"/work-items/{item_at_spec_gate}").json()["gate_artifact"] is None
    assert client.get(f"/work-items/{item_at_spec_gate}/artifact").status_code == 404


def test_a_symlink_out_of_the_worktree_is_a_404(client, item_at_spec_gate, worktree, tmp_path):
    outside = tmp_path / "secret.md"
    outside.write_text("not yours")
    path = worktree / ".engineering" / "specs" / f"{item_at_spec_gate}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(outside)

    assert client.get(f"/work-items/{item_at_spec_gate}/artifact").status_code == 404
```

One more test belongs in `tests/test_diff_api.py`, where a completed quick-task item with no pending gate already exists:

```python
def test_gate_artifact_is_none_without_a_pending_gate(client, seeded_item):
    assert client.get(f"/work-items/{seeded_item}").json()["gate_artifact"] is None
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest tests/test_artifact_api.py -v
```

Expected: FAIL — `KeyError: 'gate_artifact'` and 404 on an endpoint that does not exist.

- [ ] **Step 3: Write the implementation**

In `src/kraft/api.py`, add the imports:

```python
from kraft.adapters import agent as agent_mod
from kraft.index import ingest as ingest_mod
```

Add the helper below `_gate_node_index`:

```python
def _gate_artifact(st, row, gate: str | None) -> str | None:
    """The document the pending gate is a decision *about*, or None.

    Derived from the binding, not stored: the gate's node names its hooks, a
    hook with `artifact:` names a kind, and the kind plus the work item id is
    the path (`agent.artifact_path`). Nothing here to migrate and nothing to go
    stale when a rerun revises the same file.

    None when there is no pending gate, when none of the node's hooks produce
    an artifact, or when the file is not on disk — the last case is an agent
    that reported done without honouring the contract, and the gate is still
    answerable, just without a document to read.
    """
    if not gate:
        return None
    chain = json.loads(row["chain_definition"])
    try:
        node = chain["nodes"][_gate_node_index(chain, gate)]
    except StopIteration:
        return None
    worktree = st.run_dirs.worktrees / row["id"]
    for task in node["tasks"]:
        kind = st.registry.hooks.get(task, {}).get("artifact")
        if not kind:
            continue
        rel = agent_mod.artifact_path(kind, row["id"])
        if (worktree / rel).is_file():
            return rel
    return None
```

In `get_work_item`, replace the `"pending_gate": _pending_gate(st, wid),` line with a computed local so the gate is resolved once:

```python
    pending = _pending_gate(st, wid)
```

placed above the `return`, then in the returned dict:

```python
        "pending_gate": pending,
        # The document the gate is a decision about — the spec at
        # spec_approval, the plan at plan_approval. The detail screen offers
        # "Review spec" only when this is set.
        "gate_artifact": _gate_artifact(st, row, pending),
```

Add the endpoint after `get_work_item_diff`:

```python
@app.get("/work-items/{wid}/artifact")
async def get_work_item_artifact(wid: str, request: Request):
    """The pending gate's document, for a reviewer with no filesystem access.

    Read off disk rather than out of the index: the index ingests committed
    files on its own schedule, and a reviewer who is looking at the gate right
    now must see what the agent just wrote.
    """
    st = request.app.state
    row = _work_item_row(st, wid)  # 404s on an unknown work item
    rel = _gate_artifact(st, row, _pending_gate(st, wid))
    if rel is None:
        raise HTTPException(404, "this work item's gate has no artifact")
    worktree = st.run_dirs.worktrees / wid
    try:
        root = worktree.resolve(strict=True)
        target = (worktree / rel).resolve(strict=True)
    except OSError:
        raise HTTPException(404, "this work item's gate has no artifact") from None
    if not target.is_relative_to(root):
        # A symlink out of the worktree is the one way a derived, unstored path
        # can still point somewhere it should not. Same answer as a missing
        # file: a reviewer's browser learns nothing about the server's disk.
        logger.warning("artifact for %s resolves outside its worktree: %s", wid, target)
        raise HTTPException(404, "this work item's gate has no artifact")
    try:
        with target.open("rb") as fh:
            # Capped at the read, not just at the response: an agent that writes
            # a multi-gigabyte file by mistake must not be able to make the
            # server read it into memory to decide it is too big (spec §4). One
            # byte over the cap is how `truncated` is known without a stat race.
            data = fh.read(DIFF_MAX_BYTES + 1)
    except OSError:
        raise HTTPException(404, "this work item's gate has no artifact") from None
    truncated = len(data) > DIFF_MAX_BYTES
    # Slicing bytes can land mid-codepoint; `errors="replace"` is what makes
    # that a single replacement character instead of a 500.
    text = data[:DIFF_MAX_BYTES].decode(errors="replace")
    fm, body = ingest_mod.split_front_matter(text)
    return {
        "work_item_id": wid,
        "path": rel,
        "title": ingest_mod.derive_title(rel, fm, body),
        # Front matter stripped: it is the contract's plumbing, not the
        # document, and a reviewer reading a spec should not have to skip it.
        "content": body,
        "truncated": truncated,
        "artifact_max_bytes": DIFF_MAX_BYTES,
    }
```

*Deviation from the spec, deliberate:* §4 describes recording the refusal as a per-item event. This logs a warning and 404s instead. An event is a thing a reviewer sees on the timeline and would have to interpret; a symlinked artifact is an operator-side problem, and the log line is where an operator already looks. `ponytail:` upgrade path — if this ever fires in the field and nobody notices, promote it to an event.

- [ ] **Step 4: Run the tests**

```bash
uv run pytest tests/test_artifact_api.py tests/test_api.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/kraft/api.py tests/test_artifact_api.py
git commit -m "Serve the pending gate's artifact over the API"
```

---

### Task 8: Bind the hooks and prove the chain end to end

The registry change that makes it real, plus a fake agent that honours the contract so the whole spec → gate → reject → revise → approve → plan path runs in a test.

**Files:**
- Modify: `templates/registry.yaml`
- Modify: `fixtures/fake-claude.sh`
- Modify: `tests/support/harness.py` (`fake_registry`)
- Test: `tests/test_planning_chain.py`

**Interfaces:**
- Consumes: everything from Tasks 1–7.
- Produces: no importable API. `fake_registry(python_exe, fake_agent_path)` additionally rebinds `on.spec.requested` and `on.plan.requested` to the fake agent.

**Known breakage this task causes:** `tests/test_gates.py` builds its registry with `fake_registry`, which today rebinds only `on.implementation.start` (`tests/support/harness.py:84-91`), and drives the real `default` chain (`test_walk_stops_at_first_gate`, `test_approving_all_four_gates_completes_chain`). Once `templates/registry.yaml` binds the spec hook to `command: claude`, those tests would launch the operator's real agent — or fail on a missing binary and land in `needs_human` instead of `awaiting_gate`. Step 3 fixes `fake_registry` for exactly this reason.

- [ ] **Step 1: Write the failing test**

Create `tests/test_planning_chain.py`. The fixtures are `tests/test_artifact_api.py`'s (Task 7), minus the `spec-only.yaml` template — this one drives the shipped `default` chain — and with both hooks rebound:

```python
"""The planning chain end to end against the fake agent: the spec node writes
and commits a document, the gate offers it, a rejection re-runs the node with
the human's note, and approving moves on to the plan node."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from support.harness import fake_templates_dir, isolated_bd, make_repo

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FAKE_CLAUDE = _REPO_ROOT / "fixtures" / "fake-claude.sh"


def _templates(tmp_path: Path) -> Path:
    d = fake_templates_dir(tmp_path, str(_FAKE_CLAUDE))
    registry = yaml.safe_load((d / "registry.yaml").read_text())
    for hook, kind in (("on.spec.requested", "spec"), ("on.plan.requested", "plan")):
        registry["hooks"][hook] = {
            "kind": "agent",
            "command": str(_FAKE_CLAUDE),
            "skill": kind,
            "artifact": kind,
        }
    (d / "registry.yaml").write_text(yaml.safe_dump(registry))
    return d


@pytest.fixture
def prompt_log(tmp_path) -> Path:
    return tmp_path / "prompts.log"


@pytest.fixture
def client(tmp_path, monkeypatch, prompt_log):
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("KRAFT_BD_CWD", str(isolated_bd(tmp_path)))
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(_templates(tmp_path)))
    monkeypatch.setenv("KRAFT_SKILLS_DIR", str(tmp_path / "no-skills"))
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE_PROMPT_LOG", str(prompt_log))
    monkeypatch.setenv(
        "KRAFT_FRONTEND_DIST", os.environ.get("KRAFT_FRONTEND_DIST") or str(tmp_path / "no-dist")
    )
    import kraft.api as api

    with TestClient(api.app) as c:
        yield c


def _await_gate(client, wid, gate, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if client.get(f"/work-items/{wid}").json().get("pending_gate") == gate:
            return
        time.sleep(0.2)
    raise AssertionError(f"{wid} never reached {gate}")


@pytest.mark.slow
def test_spec_gate_offers_the_document_then_reject_and_approve(client, tmp_path, prompt_log):
    repo = make_repo(tmp_path)
    wid = client.post(
        "/work-items",
        json={"title": "add a flag", "repo": str(repo), "chain_template": "default"},
    ).json()["id"]

    _await_gate(client, wid, "spec_approval")
    body = client.get(f"/work-items/{wid}").json()
    assert body["gate_artifact"] == f".engineering/specs/{wid}.md"
    assert client.get(f"/work-items/{wid}/artifact").json()["title"] == "fake spec"

    client.post(f"/work-items/{wid}/gates/spec_approval/reject", json={"note": "too vague"})
    _await_gate(client, wid, "spec_approval")
    # The rejection note reaches the relaunched agent as its instruction, which
    # is the whole of how "revise the same document" works (spec §5).
    assert "too vague" in prompt_log.read_text()

    client.post(f"/work-items/{wid}/gates/spec_approval/approve")
    _await_gate(client, wid, "plan_approval")
    assert client.get(f"/work-items/{wid}").json()["gate_artifact"] == f".engineering/plans/{wid}.md"
    assert client.get(f"/work-items/{wid}/artifact").json()["title"] == "fake plan"
```

Note `chain_template`, not `template_id` — `WorkItemCreate.chain_template` is the field (`src/kraft/api.py:351`), and pydantic silently ignores an unknown key, which would run quick-task and hang the gate wait.

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest tests/test_planning_chain.py -v
```

Expected: FAIL — `gate_artifact` is `None`, because the fake agent writes no artifact.

- [ ] **Step 3: Write the implementation**

In `templates/registry.yaml`, move the two hooks out of the placeholder block:

```yaml
hooks:
  on.env.prepare:          { kind: builtin,    handler: env_setup }
  on.spec.requested:       { kind: agent,      command: claude, skill: spec, artifact: spec }
  on.plan.requested:       { kind: agent,      command: claude, skill: plan, artifact: plan }
  on.implementation.start: { kind: agent,      command: claude }
  on.test.run:             { kind: subprocess, command: [pytest, -q] }
  # --- placeholder bindings: each replaced by its plugin effort (see 03_plugin_adapters) ---
  on.chain.review_ready:     { kind: builtin, handler: noop }
  on.review.local.run:       { kind: builtin, handler: noop }
  on.mr.open:                { kind: builtin, handler: noop }
  on.ci.poll:                { kind: builtin, handler: noop }
  on.review.mr.run:          { kind: builtin, handler: noop }
  on.human_review.requested: { kind: builtin, handler: noop }
  on.merge:                  { kind: builtin, handler: noop }
```

In `tests/support/harness.py`, extend `fake_registry` so a test driving the `default` chain never launches the operator's real agent:

```python
def fake_registry(python_exe: str, fake_agent_path: Path) -> Registry:
    base = load_registry(_REPO_ROOT / "templates" / "registry.yaml")
    hooks = dict(base.hooks)
    fake = f"{python_exe} {fake_agent_path}"
    hooks["on.implementation.start"] = {"kind": "agent", "command": fake}
    # The shipped registry binds these to `claude`. A test that drives the
    # default chain must not shell out to the operator's real agent, and a
    # missing binary would land the item in needs_human rather than at a gate.
    for hook in ("on.spec.requested", "on.plan.requested"):
        hooks[hook] = {**hooks[hook], "command": fake}
    return Registry(hooks=hooks)
```

Spreading the shipped binding rather than writing a fresh dict keeps `skill:` and `artifact:` — `tests/test_gates.py` then exercises the real contract with a fake agent behind it.

In `fixtures/fake-claude.sh`, after the block that writes the session summary (it already has `ctx` and `field`), add:

```bash
# A hook with `artifact:` in the registry is contractually required to write and
# commit a document. Honour it for the two planning hooks, so the gate, the
# artifact endpoint and the indexer all see the real inputs.
hook="$(field 'Hook point')"
item="$(field 'Work item')"
case "$hook" in
  on.spec.requested) kind="spec" ;;
  on.plan.requested) kind="plan" ;;
  *) kind="" ;;
esac
if [ -n "$kind" ] && [ -n "$item" ]; then
  mkdir -p ".engineering/${kind}s"
  cat > ".engineering/${kind}s/${item}.md" <<EOF
---
work_item_ids: [${item}]
node_id: $(field 'Node')
hook_point: ${hook}
kind: ${kind}s
title: fake ${kind}
---

fake ${kind} body
EOF
  git add ".engineering/${kind}s/${item}.md" >/dev/null 2>&1 || true
  git -c user.name=fake -c user.email=fake@kraft \
      commit -q -m "fake ${kind}" -- ".engineering/${kind}s/${item}.md" >/dev/null 2>&1 || true
fi
```

- [ ] **Step 4: Run the tests**

```bash
uv run pytest tests/test_planning_chain.py -v
uv run pytest tests/test_gates.py -v
uv run pytest tests/ -q
```

Expected: PASS. `tests/test_gates.py` is the one that would have broken and is fixed by the `fake_registry` change above; if any other test asserts a session count or a hook kind for the two planning hooks, fix the expectation rather than reverting the binding.

- [ ] **Step 5: Commit**

```bash
git add templates/registry.yaml fixtures/fake-claude.sh tests/support/harness.py tests/test_planning_chain.py
git commit -m "Bind on.spec.requested and on.plan.requested to real agent hooks"
```

---

### Task 9: `kraft artifact`

CLI and MCP parity: every API verb a reviewer needs is also a subcommand and a tool.

**Files:**
- Modify: `src/kraft/client.py` (near `diff`, ~line 321, and `get_work_item`'s `keep` tuple, ~line 178)
- Modify: `src/kraft/cli.py` (a `_cmd_artifact` beside `_cmd_doc` at ~line 364; a parser beside `doc` at ~line 521)
- Modify: `src/kraft/mcp.py`
- Modify: `CLAUDE.md`
- Test: `tests/test_client_read.py`, `tests/test_mcp.py`

**Interfaces:**
- Consumes: `GET /work-items/{wid}/artifact` (Task 7), `client._target`.
- Produces: `client.artifact(work_item_id: str | None = None) -> dict`; CLI `kraft artifact [ID] [--json] [--no-pager]`; MCP tool `get_gate_artifact(work_item_id: str | None = None)`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_client_read.py`, in that file's own style — it drives `api.app.router.lifespan_context` explicitly inside one `asyncio.run`, because `httpx.ASGITransport` does not run the lifespan. Copy the closest existing test in that file (the one covering `client.diff`) and change the call and the assertion:

```python
def test_artifact_defaults_to_the_work_item_you_are_standing_in(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_WORK_ITEM_ID", "w1")

    async def scenario():
        # Same lifespan/seed scaffolding as this file's diff test, with an item
        # sitting at spec_approval and `.engineering/specs/w1.md` on disk.
        ...
        payload = await client.artifact()
        assert payload["path"] == ".engineering/specs/w1.md"

    asyncio.run(scenario())
```

The scaffolding is genuinely file-specific; lift it verbatim from the neighbouring test rather than inventing a second harness.

Update `tests/test_mcp.py:24` to expect the tenth tool:

```python
    assert names == {
        "list_work_items",
        "get_work_item",
        "get_gate_artifact",
        "search",
        "create_work_item",
        "ensure_repo",
        "approve_gate",
        "reject_gate",
        "pause_work_item",
        "resume_work_item",
    }
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/test_mcp.py tests/test_client_read.py -v
```

Expected: FAIL — the tool-name set differs; `client` has no `artifact`.

- [ ] **Step 3: Write the implementation**

In `src/kraft/client.py`, after `diff`:

```python
async def artifact(work_item_id: str | None = None) -> dict:
    """The document the item's pending gate is a decision about.

    404s when there is no pending gate or the hook produced no document —
    reading a gate's artifact is only meaningful while the gate is open.
    """
    return await _get(f"/work-items/{await _target(work_item_id)}/artifact")
```

and add `"gate_artifact",` to `get_work_item`'s `keep` tuple, after `"pending_gate"`. That one line is all `kraft show` needs: `_render_show` (`src/kraft/cli.py:204`) renders every key the client keeps, and `--json` emits the payload whole — so spec §4's "`kraft show` reports it, `--json` carries it" is satisfied without a rendering change.

In `src/kraft/cli.py`, beside `_cmd_doc`:

```python
def _cmd_artifact(ns: argparse.Namespace) -> None:
    payload = asyncio.run(client.artifact(ns.id))
    if ns.json:
        emit(payload, str, True)
        return
    render.page(payload.get("content", ""), force_plain=ns.no_pager)
```

and beside the `doc` parser:

```python
    artifact = subs.add_parser(
        "artifact", parents=[common], help="the document the pending gate is about"
    )
    artifact.add_argument("id", nargs="?")
    artifact.add_argument("--no-pager", action="store_true")
    artifact.set_defaults(func=_cmd_artifact)
```

In `src/kraft/mcp.py`, beside the `get_work_item` tool, following that file's wrapper shape exactly:

```python
    @server.tool()
    async def get_gate_artifact(work_item_id: str | None = None) -> dict:
        """The spec or plan the work item's pending gate is a decision about."""
        return await client.artifact(work_item_id)
```

In `CLAUDE.md`, under the `kraft` command list, after the `kraft docs` line:

```
kraft artifact [ID]                    # the doc the pending gate is about
```

- [ ] **Step 4: Run the tests**

```bash
uv run pytest tests/test_mcp.py tests/test_client_read.py tests/test_cli_reviewing.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/kraft/client.py src/kraft/cli.py src/kraft/mcp.py CLAUDE.md tests/test_mcp.py tests/test_client_read.py
git commit -m "Add kraft artifact to the CLI, client and MCP surface"
```

---

### Task 10: Doctor tells an operator their registry is stale

`templates/` is seeded once and never overwritten, so an operator who installed before this change keeps `builtin: noop` on both hooks forever and gets an empty gate with no explanation. Doctor is where that gets said.

**Files:**
- Modify: `src/kraft/doctor.py` (`_config_checks`, ~line 83)
- Test: `tests/test_cli_doctor.py`

**Interfaces:**
- Consumes: `kraft.paths.BUNDLED`, `default_templates_dir()`, `_check`.
- Produces: a check named `hooks`. **Always `ok=True`** — doctor has no WARN state and `cli._cmd_doctor` exits 1 on any `not ok`; a stale registry is the operator's choice to make, not a failure.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_cli_doctor.py`. Its helper is `_by_name(rows, name) -> dict` (two arguments, `tests/test_cli_doctor.py:19`) and `run_checks` is driven with `asyncio.run`, as at line 40:

```python
def test_hooks_check_names_a_hook_left_on_noop(tmp_path, monkeypatch):
    bundled = tmp_path / "bundled"
    (bundled / "templates").mkdir(parents=True)
    (bundled / "templates" / "registry.yaml").write_text(
        "hooks:\n"
        "  on.spec.requested: { kind: agent, command: claude, skill: spec, artifact: spec }\n"
    )
    live = tmp_path / "templates"
    live.mkdir()
    (live / "registry.yaml").write_text(
        "hooks:\n  on.spec.requested: { kind: builtin, handler: noop }\n"
    )
    monkeypatch.setattr(doctor, "BUNDLED", bundled)
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(live))

    check = _by_name(asyncio.run(doctor.run_checks()), "hooks")
    assert check["ok"] is True  # an operator's choice, not a failure
    assert "on.spec.requested" in check["detail"]


def test_hooks_check_is_skipped_without_a_bundled_registry(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor, "BUNDLED", tmp_path / "absent")
    check = _by_name(asyncio.run(doctor.run_checks()), "hooks")
    assert check["skipped"] is True
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/test_cli_doctor.py -k hooks -v
```

Expected: FAIL — `KeyError: 'hooks'`.

- [ ] **Step 3: Write the implementation**

In `src/kraft/doctor.py`, add the imports:

```python
import yaml

from kraft.paths import BUNDLED, RunDirs, default_run_dir, default_templates_dir
```

Add the check function and call it from `_config_checks`:

```python
def _is_noop(binding) -> bool:
    return isinstance(binding, dict) and binding.get("handler") == "noop"


def _hooks_check() -> dict:
    """Hooks this version ships a real binding for, still on `builtin: noop`
    locally.

    `templates/` is seeded once and never overwritten (`cli.seed_home`), so an
    operator who installed before a hook was implemented keeps the placeholder
    forever — and a placeholder gate shows an empty card with nothing to read
    and no reason why. Always `ok`: which hooks to run is the operator's
    decision, and `kraft doctor` exits 1 on any failed check.
    """
    shipped_path = BUNDLED / "templates" / "registry.yaml"
    # `KRAFT_TEMPLATES_DIR` first, exactly as `_config_checks` reads it
    # (src/kraft/doctor.py:84): doctor must inspect the directory the running
    # server actually loaded, not the one $KRAFT_HOME implies.
    live_path = (
        Path(os.environ.get("KRAFT_TEMPLATES_DIR") or default_templates_dir()) / "registry.yaml"
    )
    if not shipped_path.is_file():
        # A source checkout has no `_bundled/`: there is nothing to compare to.
        return _check("hooks", True, "skipped: not an installed Kraft", skipped=True)
    try:
        shipped = yaml.safe_load(shipped_path.read_text())["hooks"]
        live = yaml.safe_load(live_path.read_text())["hooks"]
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError) as exc:
        return _check("hooks", True, f"skipped: cannot compare ({exc})", skipped=True)
    stale = sorted(h for h, b in shipped.items() if not _is_noop(b) and _is_noop(live.get(h)))
    if not stale:
        return _check("hooks", True, f"{len(live)} bound, none left on the placeholder")
    return _check(
        "hooks",
        True,
        f"{', '.join(stale)} still builtin:noop in {live_path} but bound in this "
        "version's defaults — Settings → Hooks, or edit that file",
    )
```

and in `_config_checks`, append `checks.append(_hooks_check())` after the `access.yaml` check (before `_token_check()`).

- [ ] **Step 4: Run the tests**

```bash
uv run pytest tests/test_cli_doctor.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/kraft/doctor.py tests/test_cli_doctor.py
git commit -m "doctor: name hooks left on the placeholder binding"
```

---

### Task 11: "Review spec" at the gate

The `Gate` component already has an `artifact` slot; `WorkItemDetail` fills it only for `human_review_approval`. Fill it for any gate the API reports an artifact for, and add the modal that shows it.

**Files:**
- Modify: `frontend/src/types.ts`, `frontend/src/api.ts`
- Create: `frontend/src/components/ArtifactModal.tsx`
- Modify: `frontend/src/views/WorkItemDetail.tsx:~296` and the modal-render block at `:~363`
- Test: `frontend/src/components/ArtifactModal.test.tsx`, `frontend/src/views/WorkItemDetail.test.tsx`

**Interfaces:**
- Consumes: `GET /work-items/{wid}/artifact` (Task 7), `useModal`, `react-markdown`.
- Produces: `WorkItemArtifact` type, `api.getWorkItemArtifact(id)`, `<ArtifactModal workItemId onClose />`.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/ArtifactModal.test.tsx`, following `DiffModal.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import * as api from "../api";
import { ArtifactModal } from "./ArtifactModal";

describe("ArtifactModal", () => {
  it("renders the document's title and markdown body", async () => {
    vi.spyOn(api, "getWorkItemArtifact").mockResolvedValue({
      work_item_id: "w1",
      path: ".engineering/specs/w1.md",
      title: "A spec",
      content: "# Heading\n\nbody text",
      truncated: false,
    });
    render(<ArtifactModal workItemId="w1" onClose={() => {}} />);
    expect(await screen.findByText("A spec")).toBeTruthy();
    expect(await screen.findByRole("heading", { name: "Heading" })).toBeTruthy();
  });

  it("says so when the server has nothing to show", async () => {
    vi.spyOn(api, "getWorkItemArtifact").mockRejectedValue(new Error("404: no artifact"));
    render(<ArtifactModal workItemId="w1" onClose={() => {}} />);
    expect(await screen.findByText(/no artifact/)).toBeTruthy();
  });
});
```

Add to `frontend/src/views/WorkItemDetail.test.tsx`:

```tsx
it("offers the spec at a spec gate", async () => {
  setup({ pending_gate: "spec_approval", gate_artifact: ".engineering/specs/w1.md" });
  renderDetail();
  expect(await screen.findByText(/Review spec/)).toBeTruthy();
});

it("offers nothing when the gate has no artifact", async () => {
  setup({ pending_gate: "spec_approval", gate_artifact: null });
  renderDetail();
  expect(screen.queryByText(/Review spec/)).toBeNull();
});
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
just test-ui
```

Expected: FAIL — `Cannot find module './ArtifactModal'`.

- [ ] **Step 3: Write the implementation**

In `frontend/src/types.ts`, add `gate_artifact?: string | null;` to `WorkItem` beside `pending_gate`, and:

```ts
/** The document a gate is a decision about, read off the worktree. */
export interface WorkItemArtifact {
  work_item_id: string;
  path: string;
  title: string;
  content: string;
  truncated: boolean;
}
```

In `frontend/src/api.ts`, beside `getWorkItemDiff`:

```ts
export const getWorkItemArtifact = (id: string) =>
  req<WorkItemArtifact>(`/work-items/${encodeURIComponent(id)}/artifact`);
```

adding `WorkItemArtifact` to that file's type import.

Create `frontend/src/components/ArtifactModal.tsx`:

```tsx
import { useEffect, useState } from "react";
import { X } from "@phosphor-icons/react";
import Markdown from "react-markdown";
import * as api from "../api";
import type { WorkItemArtifact } from "../types";
import { useModal } from "../useModal";

/**
 * The spec or plan the pending gate is a decision about, read-only.
 *
 * Sibling to DiffModal rather than a mode of it, for the same reason DiffModal
 * is a sibling of DocumentModal: this one renders markdown and that one colours
 * a diff, and neither shares the other's body.
 *
 * Not DocumentModal itself: this reads the *worktree*, before the indexer has
 * seen the file. A reviewer at an open gate must see what the agent just wrote.
 */
export function ArtifactModal({
  workItemId,
  onClose,
}: {
  workItemId: string;
  onClose: () => void;
}) {
  const ref = useModal<HTMLDivElement>(onClose);
  const [doc, setDoc] = useState<WorkItemArtifact | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api
      .getWorkItemArtifact(workItemId)
      .then(setDoc)
      .catch((e) => setErr(e instanceof Error ? e.message : String(e)));
  }, [workItemId]);

  return (
    <div className="dialog-backdrop" role="dialog" aria-modal="true" aria-label="document">
      <div className="dialog diff-modal" ref={ref}>
        <header className="diff-modal-head">
          <span>{doc?.title ?? "—"}</span>
          <span className="mono">{doc?.path}</span>
          <button className="btn btn-ghost" onClick={onClose} aria-label="close">
            <X size={14} />
          </button>
        </header>
        <div className="doc-modal-body">
          {err && <p className="control-hint">{err}</p>}
          {doc && <Markdown>{doc.content}</Markdown>}
          {doc?.truncated && (
            <p className="control-hint">
              This document is too large to show whole; the rest is in the worktree.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
```

`doc-modal-body` is `DocumentModal`'s own markdown-body class (`frontend/src/components/DocumentModal.tsx:199`, styled at `frontend/src/styles.css:491`). No new CSS for this component.

In `frontend/src/views/WorkItemDetail.tsx`, import `ArtifactModal`, add `const [showArtifact, setShowArtifact] = useState(false);` beside `showDiff`, and replace the `artifact={...}` prop:

```tsx
          artifact={
            gate === "human_review_approval" ? (
              <button className="btn btn-secondary" onClick={() => setShowDiff(true)}>
                Review changes
              </button>
            ) : item.gate_artifact ? (
              <button className="btn btn-secondary" onClick={() => setShowArtifact(true)}>
                {gate === "spec_approval" ? "Review spec" : "Review plan"}
              </button>
            ) : undefined
          }
```

and beside the `showDiff` render:

```tsx
      {showArtifact && (
        <ArtifactModal workItemId={item.id} onClose={() => setShowArtifact(false)} />
      )}
```

- [ ] **Step 4: Run the tests**

```bash
just test-ui
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/types.ts frontend/src/api.ts frontend/src/components/ArtifactModal.tsx frontend/src/components/ArtifactModal.test.tsx frontend/src/views/WorkItemDetail.tsx frontend/src/views/WorkItemDetail.test.tsx
git commit -m "Offer the spec or plan at its gate"
```

---

### Task 12: Quality gates and close-out

**Files:**
- Modify: `CLAUDE.md` (if Task 9's line was missed)
- No new code.

**Interfaces:** none.

- [ ] **Step 1: Run every gate**

```bash
just test
just test-ui
just lint
```

Expected: all green. Fix anything red before continuing — a failure here is a defect in an earlier task, not a new task.

- [ ] **Step 2: Smoke-test a dev instance**

```bash
just dev-reset && just dev
```

`kraft create` files an item **paused** and on the repo's default chain, so the smoke test has to start it and has to be on `default`. Easiest path: open the UI on `:5173`, create the item choosing the `default` chain, and let it run. From a second shell:

```bash
kraft list                  # wait for the item to show needs-you at spec_approval
kraft show <ID>             # gate_artifact is .engineering/specs/<ID>.md
kraft artifact <ID>         # the fake agent's spec prints
kraft doctor                # exits 0
```

Then reject the gate in the UI with a note, watch the item re-run the spec node, and approve — it must land at `plan_approval` with a plan artifact. If the connected repo's default chain is not `default`, set it in Settings → Repos first.

- [ ] **Step 3: File anything left over**

Any residual found while implementing gets a bead in the same session, not a note in the handoff:

```bash
bd create --title="<what>" --description="<why it matters, where it is>" --type=task --priority=3
```

- [ ] **Step 4: Close the beads**

```bash
bd close Kraft-pqu Kraft-bmp Kraft-2k4
git status
```

- [ ] **Step 5: Hand off**

Report changed files, which gates ran and their results, the beads closed, and the proposed commit/merge commands. **Do not push and do not merge to `main`** — conservative profile.

---

## Self-Review

**Spec coverage.** §1 ensure_worktree → Task 1. §2.1 artifact contract → Task 4. §2.2 front matter and the ingest link condition → Tasks 4 and 6. §2.3 skill resolution, bare name vs plugin ref, unavailable-method instruction → Task 2, injected in Task 4. §2.4 overlay not seeded, package-data, chain-review moved → Task 2. §2.5 `load_registry` validation and `skills_dir` → Task 3. §2.6 injection order → Task 4. §3 the two methods → Task 5. §4 `gate_artifact`, the endpoint, the modal, CLI/MCP → Tasks 7, 9, 11. §5 rejection unchanged — covered by Task 8's reject-then-revise assertion; no code change, correctly. §6 registry bindings and the doctor check → Tasks 8 and 10. §7 tests — every task carries its own. §8 acceptance → Task 12's smoke test.

**Deviations, stated where they occur:** Task 7 logs a warning instead of appending an event on a symlinked artifact.

**Review pass (2026-09-07).** A reviewer checked every code block against the real files. Corrections folded in: all test code moved to this suite's sync `asyncio.run(scenario())` idiom (there is no async pytest plugin); `store.create_work_item` calls given their required `bead_id`/`chain_template`; Task 1's executor test switched to an inline `Registry` so Task 8's rebinding cannot silently change what it tests; Task 4's tests corrected to `_system_prompt(seen["cmd"])`; the artifact block moved ahead of the review-package block to match spec §2.6; Task 6's `make_repo_with_engineering` given its required `files` dict; Task 7's read capped at `DIFF_MAX_BYTES + 1` bytes rather than reading the whole file, and its fixtures written out in full; Task 8's POST corrected to `chain_template` and `fake_registry` extended, which is what keeps `tests/test_gates.py` from launching a real agent; Task 10's `_hooks_check` taught to read `KRAFT_TEMPLATES_DIR` and its test corrected to `_by_name(rows, "hooks")`; Task 11's markdown container corrected to `doc-modal-body`; Task 12's smoke test corrected for `kraft create` filing items paused on the repo's default chain.

Verified correct and left alone: the four `load_registry` call-site line numbers, `app.state.skills_dir` landing before the reattach `_launch`, `_gate_node_index` raising `StopIteration` (handled) when an intake attachment removed the gate's node, rejection re-entry reaching `ensure_worktree` because it sits at the top of both `run` and `resume`, `decode(errors="replace")` being mid-codepoint safe, and fake-claude's commit running in the worktree with inline `-c user.*`.

**Type consistency.** `artifact_path(kind, work_item_id)` is defined once in Task 4 and called by name in Tasks 4 and 7. `ensure_worktree(db, run_dirs, *, repo, work_item_id) -> Path` is defined in Task 1 and called in Task 1 only. `skill.read(skills_dir, value)` is defined in Task 2 and called in Task 4. `Invocation.method_text` is added in Task 4 and read in Task 4. `LaunchContext.skills_dir` is added in Task 4 and set in Task 4 (`api._launch`). `gate_artifact` is the same key in Tasks 7, 9 and 11.
