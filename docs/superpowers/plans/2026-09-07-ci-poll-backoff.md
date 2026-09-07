# ci_poll poll-with-backoff Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the `ci_poll` forge node wait for a pending pipeline with backoff instead of resolving `pending` to a failed session.

**Architecture:** The wait lives inside `adapters/forge.run_task`'s `ci_poll` branch as an async loop with exponential backoff and a deadline. No new session status and no orchestrator change: a timed-out poll still records `failed`, but its log line says `timed out ... still pending` so a human — and Kraft-cbr's future fix loop — can tell it apart from a red pipeline. Timeout and interval are optional per-node keys on the `on.ci.poll` registry binding.

**Tech Stack:** Python 3.14, asyncio, pytest / pytest-asyncio, PyYAML registry templates.

**Spec:** No spec file — bounded change, design approved in chat 2026-09-07. Tracked as Kraft-bvq; blocks Kraft-cbr.

## Global Constraints

- Never sleep in tests. `FakeForge.ci_states` scripts pending-then-green, and `poll_interval=0` passed to `run_task` removes the wait. **Superseded by 057ea12:** `poll_interval: 0` is rejected in the *registry* as a hot loop, so only the `run_task` keyword accepts it.
- `poll_timeout` / `poll_interval` are accepted **only** on a forge binding whose `handler` is `ci_poll`. A key nobody reads is a setting that silently does nothing — the rule `templates.py` already states for its `unknown` check.
- The shipped `templates/registry.yaml` stays bare: defaults come from the module constants, not from config.
- `_FORGE_HANDLERS` / `_FORGE_BACKENDS` remain duplicated between `templates.py` and `forge.resolve` on purpose. Do not unify them.

---

### Task 1: Poll loop in the forge adapter

**Files:**
- Modify: `src/kraft/adapters/forge.py` (constants near `CIState`; new `_poll_ci`; `run_task` signature ~line 221; `ci_poll` branch lines 254-262)
- Test: `tests/test_forge.py`

**Interfaces:**
- Consumes: `Forge.ci_status`, `CIStatus`, `MR`, `FakeForge` — all existing.
- Produces:
  - `DEFAULT_POLL_TIMEOUT: float = 1800.0`, `DEFAULT_POLL_INTERVAL: float = 5.0`, `_MAX_POLL_INTERVAL: float = 60.0`
  - `async def _poll_ci(forge: Forge, *, repo: Path, branch: str, timeout: float, interval: float) -> tuple[CIStatus, bool]` — returns `(status, timed_out)`
  - `run_task(..., poll_timeout: float = DEFAULT_POLL_TIMEOUT, poll_interval: float = DEFAULT_POLL_INTERVAL)`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_forge.py`. Match the file's existing fixture/helper style for building `db` and `run_dirs` — read a neighbouring `run_task` test and copy how it calls in, rather than inventing a new harness.

```python
async def test_ci_poll_waits_out_a_pending_pipeline(...):
    forge = FakeForge(ci_states=["pending", "pending", "success"])
    # patch resolve() to return this forge, the way the existing tests do
    status = await run_task(..., handler="ci_poll", backend="fake", poll_interval=0)
    assert status == "done"
    assert forge.ci_states == ["success"]  # both pendings consumed


async def test_ci_poll_times_out_still_pending(...):
    forge = FakeForge(ci_states=["pending"])
    status = await run_task(..., handler="ci_poll", backend="fake",
                            poll_timeout=0, poll_interval=0)
    assert status == "failed"
    assert "timed out" in log_text_of(session)


async def test_ci_poll_red_pipeline_fails_without_waiting(...):
    forge = FakeForge(ci_states=["failed"])
    status = await run_task(..., handler="ci_poll", backend="fake", poll_interval=0)
    assert status == "failed"
    assert "timed out" not in log_text_of(session)
```

- [ ] **Step 2: Run them and confirm they fail for the right reason**

Run: `just test tests/test_forge.py -k ci_poll -v`
Expected: `test_ci_poll_waits_out_a_pending_pipeline` FAILS with `status == "failed"` (today's bug), and the timeout test FAILS on the unexpected keyword `poll_timeout`. A pass here means the test is pinning nothing — stop and fix the test.

- [ ] **Step 3: Implement**

Constants beside `CIState`:

```python
#: How long a `ci_poll` node waits for a pipeline before giving up, and how
#: long it sleeps between checks. Both are overridable per node in the
#: registry; a pipeline that takes longer than this needs a human either way.
DEFAULT_POLL_TIMEOUT = 1800.0
DEFAULT_POLL_INTERVAL = 5.0
_MAX_POLL_INTERVAL = 60.0
```

New helper above `run_task`:

```python
async def _poll_ci(
    forge: Forge, *, repo: Path, branch: str, timeout: float, interval: float
) -> tuple[CIStatus, bool]:
    """Wait for a pipeline to settle. Returns the last status and whether the
    wait ran out with it still pending.

    Backoff, not a fixed interval: a five-minute pipeline should not cost sixty
    CLI invocations, and the first checks are the cheap ones to get right.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        ci = await forge.ci_status(repo=repo, mr=MR(number=0, url=""), branch=branch)
        if ci.state != "pending":
            return ci, False
        if loop.time() >= deadline:
            return ci, True
        await asyncio.sleep(min(interval, max(0.0, deadline - loop.time())))
        interval = min(interval * 2, _MAX_POLL_INTERVAL)
```

`run_task` gains the two keyword arguments (defaulting to the constants), and the branch becomes:

```python
            case "ci_poll":
                # Both CLIs resolve the merge request from the checked-out
                # branch, so the number is not threaded between nodes.
                ci, timed_out = await _poll_ci(
                    forge,
                    repo=repo,
                    branch=branch,
                    timeout=poll_timeout,
                    interval=poll_interval,
                )
                head = (
                    f"pipeline timed out after {poll_timeout:g}s, still pending"
                    if timed_out
                    else f"pipeline {ci.state}"
                )
                log = f"{head}: {ci.url}\n" + "".join(f"  {j}\n" for j in ci.jobs)
                status = "done" if ci.state == "success" else "failed"
```

Delete the `ponytail:` comment this replaces.

- [ ] **Step 4: Run the tests**

Run: `just test tests/test_forge.py -v`
Expected: PASS, including the pre-existing forge tests.

- [ ] **Step 5: Commit**

```bash
git add src/kraft/adapters/forge.py tests/test_forge.py
git commit -m "fix(forge): poll a pending pipeline instead of failing the node"
```

---

### Task 2: Per-node poll config on the registry binding

**Files:**
- Modify: `src/kraft/templates.py` (forge validation ~lines 103-115; the forge `known` set ~line 181)
- Modify: `src/kraft/executor.py` (the `kind == "forge"` branch, lines 332-345)
- Test: `tests/test_templates.py` (or whichever file already covers `RegistryError`)

**Interfaces:**
- Consumes: `run_task(..., poll_timeout=, poll_interval=)` from Task 1.
- Produces: `on.ci.poll` bindings may carry `poll_timeout` / `poll_interval` as non-negative numbers.

- [ ] **Step 1: Write the failing tests**

```python
def test_poll_keys_accepted_on_ci_poll(tmp_path):
    reg = write_registry(tmp_path, {"on.ci.poll": {
        "kind": "forge", "handler": "ci_poll", "backend": "glab",
        "poll_timeout": 600, "poll_interval": 10}})
    load_registry(reg)  # no raise


def test_poll_keys_rejected_on_other_forge_handlers(tmp_path):
    reg = write_registry(tmp_path, {"on.mr.open": {
        "kind": "forge", "handler": "open_mr", "backend": "glab",
        "poll_timeout": 600}})
    with pytest.raises(RegistryError, match="poll_timeout"):
        load_registry(reg)


def test_negative_poll_timeout_rejected(tmp_path):
    reg = write_registry(tmp_path, {"on.ci.poll": {
        "kind": "forge", "handler": "ci_poll", "backend": "glab",
        "poll_timeout": -1}})
    with pytest.raises(RegistryError):
        load_registry(reg)
```

Use the file's existing registry-writing helper; do not add a new one.

- [ ] **Step 2: Run and confirm they fail**

Run: `just test tests/test_templates.py -k poll -v`
Expected: the accept-case FAILS with `unknown key(s) ['poll_interval', 'poll_timeout']`; the reject-cases FAIL by raising the wrong message (they currently raise on the unknown key, not on the handler/value rule — check the message, not just that it raised).

- [ ] **Step 3: Implement**

In `templates.py`, inside the `if kind == "forge":` block, after the backend check:

```python
            for key in ("poll_timeout", "poll_interval"):
                if key not in binding:
                    continue
                if handler != "ci_poll":
                    raise RegistryError(
                        f"{path.name}: forge hook {hook!r} has {key!r}, which applies "
                        "only to a ci_poll handler"
                    )
                value = binding[key]
                # Superseded by 057ea12: poll_timeout keeps `>= 0` (a zero
                # timeout is a single-shot check), poll_interval requires `> 0`
                # (zero is a hot loop), and both reject .inf/.nan.
                if not isinstance(value, int | float) or isinstance(value, bool) or value < 0:
                    raise RegistryError(
                        f"{path.name}: forge hook {hook!r} {key!r} must be a non-negative number"
                    )
```

and widen the forge `known` set:

```python
        elif kind == "forge":
            known = {"kind", "handler", "backend", "poll_timeout", "poll_interval"}
```

In `executor.py`, in the forge branch, pass only what the binding actually sets so the adapter's defaults stay the single source of truth:

```python
        poll = {
            k: binding[k] for k in ("poll_timeout", "poll_interval") if k in binding
        }
        return await _forge.run_task(
            ...,
            **poll,
            **common,
        )
```

- [ ] **Step 4: Run the tests**

Run: `just test tests/test_templates.py tests/test_forge.py tests/test_executor.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/kraft/templates.py src/kraft/executor.py tests/test_templates.py
git commit -m "feat(registry): per-node poll_timeout and poll_interval on ci_poll"
```
