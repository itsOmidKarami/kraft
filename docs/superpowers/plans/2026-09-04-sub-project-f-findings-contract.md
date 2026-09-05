# Findings Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the fix loop read structured findings — gate on severity, recognise a finding it has already seen, and stop early when a cycle changes nothing.

**Architecture:** A new `kraft/findings.py` owns parsing, fingerprinting and the severity vocabulary. `executor._walk_node` collects the round's findings from its measuring sessions' result files, emits a `findings_measured` event, and compares this cycle's loop-eligible fingerprints against the **previous event's** to decide between completing, looping and escalating. `policy.yaml` gains the severity set. The gate shows the Minor findings that never entered the loop.

**Tech Stack:** Python 3.14, SQLite, pytest; React 18 + TypeScript + Vitest.

**Spec:** `docs/superpowers/specs/2026-09-04-sub-project-f-findings-contract-design.md`

**Beads:** `Kraft-8mu.6`. The review-package task is `Kraft-8mu.6.1`, blocked by `Kraft-8mu.2`, and **not** in this plan.

## Global Constraints

- Python 3.14+. Unparenthesized `except A, B:` (PEP 758) is used in this codebase and is correct — do not "fix" it.
- **Backward compatibility is the safety property.** A result file with no `findings` key must behave byte-identically to today, and `tests/test_fix_loop.py` must stay green **unedited**. Every task is bound by this.
- Malformed findings are dropped individually, never raised — matching `read_summary_ref`'s posture at `src/kraft/adapters/subprocess.py:40`.
- A non-clean task status with an empty findings list is **still non-clean**.
- `line` and `severity` are both excluded from a finding's fingerprint.
- **No new table.** Carry-forward reads the `events` log; the previous cycle's fingerprints are never held in a local across loop iterations.
- Do not touch `retry_counters`, `resolve_cap`, or `policy.check`.
- Run `just lint` before each commit.

## Decisions this plan settles

Recorded because the spec left them open and an implementer would otherwise guess:

- **`findings_measured.fingerprints` carries the loop-eligible subset only.** `findings` carries every severity. A Minor finding must not be able to hold the loop open by appearing unchanged.
- **`_collect_findings` reads only the node's *measuring* tasks.** The fix task is dispatched with `round=count` and the next measuring pass runs at that same round, so an unfiltered query returns the fix agent's own session and folds its result file into the cycle.
- **One row per hook point, the most recent.** `_reconcile_current_node` can re-enter `_walk_node` with `round` reset to 0 while the counter continues, leaving stale rows at the same round. Taking the last row per hook point makes the collection correct on that path.

---

### Task 1: `kraft/findings.py`

**Files:**
- Create: `src/kraft/findings.py`
- Test: `tests/test_findings.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Finding` frozen dataclass `(severity: str, message: str, file: str | None, line: int | None, source_plugin: str)`; `Finding.fingerprint -> str` (16 hex chars); `parse(result_path) -> list[Finding]`, never raises; `from_payload(raw: dict) -> Finding`; `SEVERITIES: tuple[str, ...] = ("critical", "important", "minor")`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_findings.py`:

```python
import json

from kraft.findings import Finding, parse


def _write(tmp_path, data):
    p = tmp_path / "r.json"
    p.write_text(json.dumps(data))
    return p


def test_parse_reads_a_finding(tmp_path):
    p = _write(tmp_path, {"status": "failed", "findings": [
        {"severity": "important", "message": "swallowed exception",
         "file": "a.py", "line": 12, "source_plugin": "ponytail"},
    ]})
    (f,) = parse(p)
    assert (f.severity, f.file, f.line, f.source_plugin) == ("important", "a.py", 12, "ponytail")


def test_parse_missing_key_yields_nothing(tmp_path):
    assert parse(_write(tmp_path, {"status": "done"})) == []


def test_parse_non_list_findings_yields_nothing(tmp_path):
    assert parse(_write(tmp_path, {"findings": "nope"})) == []


def test_parse_missing_file_is_not_an_error(tmp_path):
    assert parse(tmp_path / "absent.json") == []


def test_parse_broken_json_is_not_an_error(tmp_path):
    p = tmp_path / "r.json"
    p.write_text("{not json")
    assert parse(p) == []


def test_parse_drops_bad_entries_individually(tmp_path):
    p = _write(tmp_path, {"findings": [
        {"severity": "nonsense", "message": "m", "source_plugin": "p"},
        {"severity": "minor", "source_plugin": "p"},
        {"severity": "minor", "message": "m"},
        "not a mapping",
        {"severity": "critical", "message": "keep me", "source_plugin": "p"},
    ]})
    (f,) = parse(p)
    assert f.message == "keep me"


def test_optional_file_and_line(tmp_path):
    p = _write(tmp_path, {"findings": [
        {"severity": "minor", "message": "m", "source_plugin": "p"},
    ]})
    (f,) = parse(p)
    assert f.file is None and f.line is None


def test_fingerprint_ignores_line(tmp_path):
    a = Finding("important", "same message", "a.py", 10, "p")
    b = Finding("important", "same message", "a.py", 400, "p")
    assert a.fingerprint == b.fingerprint


def test_fingerprint_ignores_severity(tmp_path):
    """The same defect re-reported at a different severity is the same defect."""
    a = Finding("important", "m", "a.py", 1, "p")
    b = Finding("critical", "m", "a.py", 1, "p")
    assert a.fingerprint == b.fingerprint


def test_fingerprint_normalizes_whitespace_and_case(tmp_path):
    a = Finding("important", "Swallowed   exception", "a.py", 1, "p")
    b = Finding("important", "swallowed exception", "a.py", 1, "p")
    assert a.fingerprint == b.fingerprint


def test_fingerprint_separates_file_plugin_and_message(tmp_path):
    base = Finding("important", "m", "a.py", 1, "p")
    assert base.fingerprint != Finding("important", "m", "b.py", 1, "p").fingerprint
    assert base.fingerprint != Finding("important", "other", "a.py", 1, "p").fingerprint
    assert base.fingerprint != Finding("important", "m", "a.py", 1, "q").fingerprint
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `just test tests/test_findings.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'kraft.findings'`.

- [ ] **Step 3: Write the implementation**

Create `src/kraft/findings.py`:

```python
"""The shared finding schema (`03_plugin_adapters.md` §4) and its identity.

Every reader here is best-effort: a plugin that writes a malformed result file
loses its findings, never the session. That matches `read_summary_ref` in
`adapters/subprocess.py`, which swallows a broken file for the same reason.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

SEVERITIES: tuple[str, ...] = ("critical", "important", "minor")

_WS = re.compile(r"\s+")


@dataclass(frozen=True)
class Finding:
    severity: str
    message: str
    file: str | None
    line: int | None
    source_plugin: str

    @property
    def fingerprint(self) -> str:
        """Stable identity across fix cycles.

        `line` is excluded deliberately: the fix task edits the file, so every
        line below the edit shifts, and including it would make every finding
        look new after any fix — exactly the blindness this exists to remove.
        Severity is excluded too: the same defect re-reported at a different
        severity is the same defect.
        """
        norm = _WS.sub(" ", self.message).strip().lower()
        raw = "\0".join((self.source_plugin, self.file or "", norm))
        return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _one(raw: object) -> Finding | None:
    if not isinstance(raw, dict):
        return None
    severity = raw.get("severity")
    message = raw.get("message")
    source_plugin = raw.get("source_plugin")
    if severity not in SEVERITIES:
        return None
    if not isinstance(message, str) or not message:
        return None
    if not isinstance(source_plugin, str) or not source_plugin:
        return None
    file = raw.get("file")
    line = raw.get("line")
    return Finding(
        severity=severity,
        message=message,
        file=file if isinstance(file, str) and file else None,
        line=line if isinstance(line, int) and not isinstance(line, bool) else None,
        source_plugin=source_plugin,
    )


def parse(result_path: str | Path) -> list[Finding]:
    """Findings from a result file. Never raises; a bad file yields none."""
    try:
        data = json.loads(Path(result_path).read_text())
    except OSError, json.JSONDecodeError:
        return []
    if not isinstance(data, dict):
        return []
    raw = data.get("findings")
    if not isinstance(raw, list):
        return []
    return [f for f in (_one(r) for r in raw) if f is not None]


def from_payload(raw: dict) -> Finding:
    """Rebuild a Finding from a `findings_measured` event payload.

    Key-by-key rather than `Finding(**raw)`: a payload written by a later schema
    with an extra key must not raise in a read path.
    """
    return Finding(
        severity=raw.get("severity", ""),
        message=raw.get("message", ""),
        file=raw.get("file"),
        line=raw.get("line"),
        source_plugin=raw.get("source_plugin", ""),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `just test tests/test_findings.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/kraft/findings.py tests/test_findings.py
git commit -m "feat: Finding schema, parsing and stable fingerprints"
```

---

### Task 2: Severity policy, and keeping it durable

**Files:**
- Modify: `src/kraft/policy.py` (`Policy`, `load_policy`)
- Modify: `src/kraft/api.py` (`PolicyBody`, `put_policy`)
- Modify: `templates/policy.yaml`
- Test: `tests/test_policy.py`, `tests/test_settings_api.py`

**Interfaces:**
- Consumes: `findings.SEVERITIES` (Task 1).
- Produces: `Policy.loop_severities: frozenset[str]` defaulting to `{"critical", "important"}`; `policy._DEFAULT_LOOP_SEVERITIES`; a `PUT /policy` that round-trips the `findings` block.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_policy.py`:

```python
def test_loop_severities_default(tmp_path):
    p = tmp_path / "policy.yaml"
    p.write_text("default: {attempts: 3, wall_clock_s: 60}\n")
    assert policy.load_policy(p).loop_severities == frozenset({"critical", "important"})


def test_loop_severities_configured(tmp_path):
    p = tmp_path / "policy.yaml"
    p.write_text(
        "default: {attempts: 3, wall_clock_s: 60}\nfindings: {loop_severities: [critical]}\n"
    )
    assert policy.load_policy(p).loop_severities == frozenset({"critical"})


def test_loop_severities_all_three_restores_old_behaviour(tmp_path):
    p = tmp_path / "policy.yaml"
    p.write_text(
        "default: {attempts: 3, wall_clock_s: 60}\n"
        "findings: {loop_severities: [critical, important, minor]}\n"
    )
    assert policy.load_policy(p).loop_severities == frozenset(
        {"critical", "important", "minor"}
    )


def test_unknown_severity_is_rejected(tmp_path):
    p = tmp_path / "policy.yaml"
    p.write_text(
        "default: {attempts: 3, wall_clock_s: 60}\n"
        "findings: {loop_severities: [critical, urgent]}\n"
    )
    with pytest.raises(policy.PolicyError, match="urgent"):
        policy.load_policy(p)


def test_loop_severities_must_be_a_list(tmp_path):
    p = tmp_path / "policy.yaml"
    p.write_text(
        "default: {attempts: 3, wall_clock_s: 60}\nfindings: {loop_severities: critical}\n"
    )
    with pytest.raises(policy.PolicyError):
        policy.load_policy(p)
```

Append to `tests/test_settings_api.py` — this is the one that matters, because without it the setting is not durable. Follow the file's existing `client` / `templates_dir` fixtures:

```python
def test_saving_the_policy_preserves_the_findings_block(client, templates_dir):
    """A save from the policy screen must not silently erase a block it does not edit."""
    (templates_dir / "policy.yaml").write_text(
        "loops:\n  verify_fix_loop: { attempts: 3, wall_clock_s: 3600 }\n"
        "default: { attempts: 3, wall_clock_s: 3600 }\n"
        "findings:\n  loop_severities: [critical]\n"
    )
    body = client.get("/policy").json()
    assert body["findings"]["loop_severities"] == ["critical"]

    body["loops"]["verify_fix_loop"]["attempts"] = 5
    assert client.put("/policy", json=body).status_code == 200

    on_disk = yaml.safe_load((templates_dir / "policy.yaml").read_text())
    assert on_disk["findings"]["loop_severities"] == ["critical"]
    assert on_disk["loops"]["verify_fix_loop"]["attempts"] == 5
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `just test -k "loop_severities or preserves_the_findings_block"`
Expected: FAIL — `Policy` has no `loop_severities`; the settings test fails because `put_policy` rebuilds the file from `{loops, default}` only.

- [ ] **Step 3: Write the implementation**

In `src/kraft/policy.py`, add the import and the field:

```python
from kraft.findings import SEVERITIES

_DEFAULT_LOOP_SEVERITIES = frozenset({"critical", "important"})


@dataclass(frozen=True)
class Policy:
    loops: dict[str, Cap]
    default: Cap
    loop_severities: frozenset[str] = _DEFAULT_LOOP_SEVERITIES
```

In `load_policy`, before the `return`:

```python
    findings_raw = data.get("findings") or {}
    if not isinstance(findings_raw, dict):
        raise PolicyError(f"{path.name}: 'findings' must be a mapping")
    sev_raw = findings_raw.get("loop_severities")
    if sev_raw is None:
        severities = _DEFAULT_LOOP_SEVERITIES
    else:
        if not isinstance(sev_raw, list):
            raise PolicyError(f"{path.name}: 'findings.loop_severities' must be a list")
        unknown = [s for s in sev_raw if s not in SEVERITIES]
        if unknown:
            raise PolicyError(
                f"{path.name}: unknown severity {unknown[0]!r}; expected one of {SEVERITIES}"
            )
        severities = frozenset(sev_raw)
```

and pass `loop_severities=severities` into the `Policy(...)` construction.

In `src/kraft/api.py`, `PolicyBody` gains `findings: dict | None = None`, and `put_policy` carries it through so a save cannot erase it:

```python
    data = {"loops": body.loops, "default": body.default}
    if body.findings is not None:
        data["findings"] = body.findings
```

Check `get_policy`'s actual return shape before assuming the read side is fine: if it projects a fixed set of keys rather than returning the parsed YAML, add `findings` there too, or the round trip in the test above cannot pass.

In `templates/policy.yaml`, append:

```yaml
# Which finding severities burn a fix cycle. Minor findings are recorded and
# shown at the human_review gate instead — a roll-up nobody reads is a silent
# discard, so they are rendered there, not merely stored.
findings:
  loop_severities: [critical, important]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `just test tests/test_policy.py tests/test_settings_api.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/kraft/policy.py src/kraft/api.py templates/policy.yaml tests/test_policy.py tests/test_settings_api.py
git commit -m "feat: findings.loop_severities in policy, preserved across saves"
```

---

### Task 3: Collect the round's sessions

**Files:**
- Modify: `src/kraft/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `store.sessions_for_round(conn, work_item_id, node_id, round) -> list[sqlite3.Row]`, ordered by `created_at`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_store.py`:

```python
def test_sessions_for_round_filters_by_node_and_round(tmp_path):
    conn = db._connect(tmp_path / "s.db")
    db.migrate(conn)
    store.create_work_item(
        conn, id="w1", bead_id="B", title="t", repo="/r",
        chain_template="default", chain_definition="{}",
    )
    for sid, node, rnd in [
        ("s1", "verify", 0), ("s2", "verify", 0), ("s3", "verify", 1), ("s4", "merge", 0),
    ]:
        store.create_session(
            conn, id=sid, work_item_id="w1", node_id=node, hook_point="on.test.run",
            log_path=f"/l/{sid}", result_path=f"/r/{sid}", round=rnd,
        )
    got = [r["id"] for r in store.sessions_for_round(conn, "w1", "verify", 0)]
    assert got == ["s1", "s2"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `just test -k sessions_for_round`
Expected: FAIL — `store` has no attribute `sessions_for_round`.

- [ ] **Step 3: Write the implementation**

In `src/kraft/store.py`:

```python
def sessions_for_round(
    conn: sqlite3.Connection, work_item_id: str, node_id: str, round: int
) -> list[sqlite3.Row]:
    """Every session this node ran in one fix cycle — the round's result files."""
    return list(
        conn.execute(
            "SELECT * FROM worker_sessions WHERE work_item_id = ? AND node_id = ? "
            "AND round = ? ORDER BY created_at",
            (work_item_id, node_id, round),
        )
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `just test -k sessions_for_round`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/kraft/store.py tests/test_store.py
git commit -m "feat: store.sessions_for_round"
```

---

### Task 4: A measuring task that can emit findings

**Files:**
- Create: `tests/support/fake_reviewer.py`
- Create: `tests/test_findings_loop.py` (harness plus one smoke test; Task 5 fills it)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: a scripted measuring binding that writes `findings[]` to `$KRAFT_RESULT_PATH`, one scripted response per invocation.

**Why this task exists:** nothing in the codebase can currently produce a finding. `on.test.run` is `python -m pytest -q` through the subprocess adapter, and pytest writes no result file; `on.review.local.run` — the hook the real design makes the findings producer (`03_plugin_adapters.md` §4) — is bound to `builtins.noop`. Every test in Task 5 needs a measuring task whose findings a test can dictate per cycle.

**The constraint that shapes it:** `adapters/subprocess.run_task` passes only `KRAFT_RESULT_PATH` into the child env — the round is **not** passed. A per-cycle script therefore cannot read the cycle number and must count its own invocations in a sidecar file.

- [ ] **Step 1: Write the fixture**

Create `tests/support/fake_reviewer.py`:

```python
"""A measuring task whose findings a test dictates, one response per invocation.

Bound to `on.review.local.run` via a registry override. Nothing in the real
codebase can emit a finding yet — `on.test.run` is pytest (no result file) and
`on.review.local.run` is `builtins.noop` — so the fix loop's findings behaviour
has nothing to exercise it without this.

`KRAFT_FAKE_REVIEW_PLAN` points at a JSON file: a list of per-invocation
responses, each `{"status": "done"|"failed", "findings": [...]}`. The Nth
invocation writes the Nth entry; past the end, the last entry repeats. The
invocation count lives in a sidecar next to the plan, because `run_task` passes
no round number into the child.
"""

import json
import os
import pathlib
import sys


def main() -> int:
    plan_path = pathlib.Path(os.environ["KRAFT_FAKE_REVIEW_PLAN"])
    plan = json.loads(plan_path.read_text())

    counter = plan_path.with_suffix(".count")
    n = int(counter.read_text()) if counter.exists() else 0
    counter.write_text(str(n + 1))

    entry = plan[min(n, len(plan) - 1)]

    result = pathlib.Path(os.environ["KRAFT_RESULT_PATH"])
    result.parent.mkdir(parents=True, exist_ok=True)
    result.write_text(json.dumps(entry))
    print(f"fake reviewer invocation {n}: {entry.get('status')}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Write the harness and the smoke test**

Create `tests/test_findings_loop.py`. Follow `tests/test_fix_loop.py`'s shape exactly — module-level helpers plus `asyncio.run(scenario())`, no pytest fixtures, since that file defines none:

```python
"""The fix loop reading findings rather than task names.

Backward compatibility is as much under test as the new behaviour: the
no-findings path must stay byte-identical, which `tests/test_fix_loop.py`
guards by staying green unedited.
"""

import asyncio
import json
import sys
from pathlib import Path

from support.harness import fake_registry, isolated_bd, make_repo

from kraft import db, events, executor, policy
from kraft.paths import RunDirs
from kraft.templates import Registry, Template

_FAKE_AGENT = Path(__file__).parent / "support" / "fake_agent.py"
_FAKE_REVIEWER = Path(__file__).parent / "support" / "fake_reviewer.py"


def _registry():
    """`on.review.local.run` becomes the scripted reviewer; everything else stands."""
    base = fake_registry(sys.executable, _FAKE_AGENT)
    hooks = dict(base.hooks)
    hooks["on.review.local.run"] = {
        "kind": "subprocess",
        "command": [sys.executable, str(_FAKE_REVIEWER)],
    }
    return Registry(hooks=hooks)


def _template() -> Template:
    """env_setup builds the worktree; `review` is the fix-loop node under test."""
    return Template(
        id="findings",
        nodes=[
            {"id": "env_setup", "tasks": ["on.env.prepare"], "gate_after": None, "fix_loop": None},
            {
                "id": "review",
                "tasks": ["on.review.local.run"],
                "gate_after": None,
                "fix_loop": "verify_fix_loop",
            },
        ],
    )


def _policy(tmp_path, *, attempts=3, severities=None) -> policy.Policy:
    p = tmp_path / "policy.yaml"
    text = (
        f"loops:\n  verify_fix_loop: {{ attempts: {attempts}, wall_clock_s: 3600 }}\n"
        f"default: {{ attempts: {attempts}, wall_clock_s: 3600 }}\n"
    )
    if severities is not None:
        text += f"findings:\n  loop_severities: {json.dumps(severities)}\n"
    p.write_text(text)
    return policy.load_policy(p)


def _finding(message, severity="critical", *, file="a.py", line=3):
    return {"severity": severity, "message": message, "file": file,
            "line": line, "source_plugin": "fake"}


def _run(tmp_path, monkeypatch, entries, *, attempts=3, severities=None):
    """Drive one work item through the review node. Returns a dict of what happened."""
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")
    plan = tmp_path / "review-plan.json"
    plan.write_text(json.dumps(entries))
    monkeypatch.setenv("KRAFT_FAKE_REVIEW_PLAN", str(plan))
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    out = {}

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = await executor.intake(
                database, rd, title="review me", repo=str(repo),
                template=_template(), bd_cwd=str(tracker),
            )
            out["result"] = await executor.run(
                database, rd, work_item_id=wid, registry=_registry(),
                bd_cwd=str(tracker),
                policy=_policy(tmp_path, attempts=attempts, severities=severities),
            )
            out["events"] = database.read(lambda c: events.read_after(c, 0, wid))
            out["sessions"] = database.read(
                lambda c: list(c.execute(
                    "SELECT * FROM worker_sessions WHERE work_item_id = ?", (wid,)))
            )
            out["wid"] = wid
        finally:
            await database.close()

    asyncio.run(scenario())
    return out


def _cycles(out):
    return sum(e["type"] == "fix_cycle_started" for e in out["events"])


def _measured(out):
    return [e for e in out["events"] if e["type"] == "findings_measured"]


def _needs_human_reason(out):
    for e in reversed(out["events"]):
        if e["type"] == "work_item_needs_human":
            return e["payload"]["reason"]
    return None


def test_the_scripted_reviewer_drives_the_loop(tmp_path, monkeypatch):
    """Smoke test for the fixture itself: a failing cycle, then a clean one."""
    out = _run(tmp_path, monkeypatch, [
        {"status": "failed", "findings": [_finding("boom")]},
        {"status": "done", "findings": []},
    ])
    assert out["result"] == "completed"
    assert _cycles(out) == 1
```

- [ ] **Step 3: Run it**

Run: `just test tests/test_findings_loop.py -v`
Expected: PASS. The loop already treats a `failed` measuring task as non-clean, so this passes **before** any of Task 5's implementation — that is the point. It proves the fixture drives the real executor.

If it fails, the fixture is wrong and Task 5 has no foundation; fix it here rather than pressing on. Check first that `Registry` and `Template` are importable from `kraft.templates` with those names, and that a `subprocess`-kind hook accepts a `command` list.

- [ ] **Step 4: Commit**

```bash
git add tests/support/fake_reviewer.py tests/test_findings_loop.py
git commit -m "test: a measuring task whose findings a test can dictate"
```

---

### Task 5: Gate the loop on severity, and escalate on no progress

**Files:**
- Modify: `src/kraft/executor.py` (`_FIX_PROMPT` neighbourhood; `_walk_node`'s `fix_loop` branch)
- Test: `tests/test_findings_loop.py`

**Interfaces:**
- Consumes: `findings.parse`, `Finding.fingerprint` (Task 1); `Policy.loop_severities` (Task 2); `store.sessions_for_round` (Task 3); the fixture (Task 4).
- Produces: the `findings_measured` event `{node_id, cycle, findings: [...], fingerprints: [...]}`; a `needs_human` reason `no_progress: N finding(s) unchanged across cycle C`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_findings_loop.py`:

```python
def test_minor_only_findings_complete_the_node(tmp_path, monkeypatch):
    """A `minor` finding does not burn a cycle — it rolls up to the gate."""
    out = _run(tmp_path, monkeypatch, [
        {"status": "failed", "findings": [_finding("naming nit", "minor")]},
    ])
    assert out["result"] == "completed"
    assert _cycles(out) == 0


def test_important_finding_enters_the_loop(tmp_path, monkeypatch):
    out = _run(tmp_path, monkeypatch, [
        {"status": "failed", "findings": [_finding("swallowed error", "important")]},
        {"status": "done", "findings": []},
    ])
    assert out["result"] == "completed"
    assert _cycles(out) == 1


def test_failure_with_no_findings_still_enters_the_loop(tmp_path, monkeypatch):
    """A measuring task can fail without saying why; that is still non-clean."""
    out = _run(tmp_path, monkeypatch, [
        {"status": "failed", "findings": []},
        {"status": "done", "findings": []},
    ])
    assert out["result"] == "completed"
    assert _cycles(out) == 1


def test_unchanged_findings_escalate_before_the_cap(tmp_path, monkeypatch):
    same = [_finding("unfixed")]
    out = _run(tmp_path, monkeypatch, [
        {"status": "failed", "findings": same},
        {"status": "failed", "findings": same},
        {"status": "failed", "findings": same},
    ], attempts=5)
    assert out["result"] == "needs_human"
    assert _needs_human_reason(out).startswith("no_progress:")
    assert _cycles(out) < 5  # escalated at cycle 1, well short of the cap


def test_line_movement_alone_is_not_progress(tmp_path, monkeypatch):
    """The fix edited the file and the finding shifted down. Still no progress."""
    out = _run(tmp_path, monkeypatch, [
        {"status": "failed", "findings": [_finding("unfixed", line=3)]},
        {"status": "failed", "findings": [_finding("unfixed", line=41)]},
    ], attempts=5)
    assert out["result"] == "needs_human"
    assert _needs_human_reason(out).startswith("no_progress:")


def test_a_new_finding_is_progress(tmp_path, monkeypatch):
    out = _run(tmp_path, monkeypatch, [
        {"status": "failed", "findings": [_finding("one")]},
        {"status": "failed", "findings": [_finding("two")]},
        {"status": "done", "findings": []},
    ])
    assert out["result"] == "completed"


def test_a_growing_set_is_progress(tmp_path, monkeypatch):
    out = _run(tmp_path, monkeypatch, [
        {"status": "failed", "findings": [_finding("one")]},
        {"status": "failed", "findings": [_finding("one"), _finding("two", file="b.py")]},
        {"status": "done", "findings": []},
    ])
    assert out["result"] == "completed"


def test_no_progress_does_not_mark_sessions_capped_out(tmp_path, monkeypatch):
    """The sessions did not cap out; only a real cap breach may claim they did."""
    same = [_finding("unfixed")]
    out = _run(tmp_path, monkeypatch, [
        {"status": "failed", "findings": same},
        {"status": "failed", "findings": same},
    ], attempts=5)
    assert _needs_human_reason(out).startswith("no_progress:")
    assert not any(s["status"] == "capped_out" for s in out["sessions"])


def test_findings_measured_carries_all_findings_but_eligible_fingerprints(
    tmp_path, monkeypatch
):
    """`findings` is the record at every severity; `fingerprints` is what the
    no-progress comparison uses, so it must exclude the deferred severities."""
    out = _run(tmp_path, monkeypatch, [
        {"status": "failed", "findings": [
            _finding("nit", "minor"), _finding("real", "important"),
        ]},
        {"status": "done", "findings": []},
    ])
    first = _measured(out)[0]["payload"]
    assert first["cycle"] == 0
    assert len(first["findings"]) == 2
    assert len(first["fingerprints"]) == 1


def test_the_fix_prompt_names_findings_and_marks_repeats(tmp_path, monkeypatch):
    """Cycle 1's prompt must mark the finding it already tried and failed to fix.

    A growing set, not a repeated one: an unchanged set escalates instead of
    dispatching a second fix, so REPEAT is only reachable when something else
    changed too.
    """
    log = tmp_path / "prompts.log"
    monkeypatch.setenv("KRAFT_FAKE_AGENT_PROMPT_LOG", str(log))
    _run(tmp_path, monkeypatch, [
        {"status": "failed", "findings": [_finding("sticky thing")]},
        {"status": "failed", "findings": [
            _finding("sticky thing"), _finding("new thing", file="b.py"),
        ]},
        {"status": "done", "findings": []},
    ])
    prompts = [p for p in log.read_text().split("\n\x00\n") if p.strip()]
    assert "sticky thing" in prompts[0]
    assert "a.py:3" in prompts[0]
    assert "REPEAT" not in prompts[0]
    assert "REPEAT" in prompts[1]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `just test tests/test_findings_loop.py -v`
Expected: FAIL — the minor-only test burns a cycle, there is no `findings_measured` event, and nothing escalates on an unchanged set.

- [ ] **Step 3: Write the implementation**

In `src/kraft/executor.py`, add beside `_FIX_PROMPT` (leave `_FIX_PROMPT` itself unchanged):

```python
#: Appended when the failures came with structured findings. Repeats lead: an
#: agent told it already tried and the reviewer disagreed behaves differently
#: from one seeing the finding fresh.
_FIX_FINDINGS = "\n\nFindings to fix:\n{findings}"
_FIX_REPEAT_NOTE = (
    "\n\nMarked REPEAT above: you attempted this on an earlier cycle and the check "
    "still reports it. Do not repeat the same approach."
)


def _format_findings(found: list[_findings.Finding], repeats: set[str]) -> str:
    lines = []
    for f in found:
        where = f"{f.file}:{f.line}" if f.file and f.line else (f.file or "—")
        tag = "REPEAT " if f.fingerprint in repeats else ""
        lines.append(f"- {tag}[{f.severity}] {where} — {f.message} ({f.source_plugin})")
    return "\n".join(lines)
```

Add two helpers above `_walk_node`:

```python
def _collect_findings(db, work_item_id: str, node: dict, round: int):
    """(findings, hook points that reported at least one) for one cycle.

    Only the node's own measuring tasks: the fix task is dispatched with
    `round=count` and the next measuring pass runs at that same round, so an
    unfiltered query folds the fix agent's result file into the cycle. Only the
    most recent row per hook point, because a resume can re-enter this node with
    `round` reset while stale rows sit at the same number.
    """
    rows = db.read(lambda c: store.sessions_for_round(c, work_item_id, node["id"], round))
    latest: dict[str, object] = {}
    for row in rows:
        if row["hook_point"] in node["tasks"]:
            latest[row["hook_point"]] = row  # ordered by created_at, so last wins
    found: list[_findings.Finding] = []
    reported: set[str] = set()
    for hook, row in latest.items():
        parsed = _findings.parse(row["result_path"])
        if parsed:
            reported.add(hook)
        found.extend(parsed)
    return found, reported


def _previous_fingerprints(db, work_item_id: str, node_id: str) -> list[str] | None:
    """The last `findings_measured` fingerprints for this node, or None if first.

    Read from the event log rather than carried in a local: `_reconcile_current_node`
    re-enters `_walk_node` after a crash or a resume with the counter intact, and a
    loop holding its history in the stack frame forgets everything it has seen —
    on exactly the path that motivates escalation.
    """
    evts = db.read(lambda c: events.read_after(c, 0, work_item_id))
    for e in reversed(evts):
        if e["type"] == "findings_measured" and e["payload"].get("node_id") == node_id:
            return e["payload"].get("fingerprints")
    return None
```

In `_walk_node`'s `fix_loop` branch, **after** the `if verdict == "paused": return "paused"` early return and **before** the `verdict == "ok"` check, insert:

```python
        previous_prints = _previous_fingerprints(db, work_item_id, node["id"])
        found, reported = _collect_findings(db, work_item_id, node, round)
        eligible = [f for f in found if f.severity in policy.loop_severities]
        prints = sorted({f.fingerprint for f in eligible})
        await db.write(
            lambda c, r=round, found=found, prints=prints: events.append(
                c, work_item_id, "findings_measured",
                {
                    "node_id": node["id"],
                    "cycle": r,
                    "findings": [asdict(f) for f in found],
                    "fingerprints": prints,
                },
            )
        )

        # A failed task that produced no findings at all is still non-clean: the
        # findings list refines *why* a task failed, it does not define failure.
        blind_failures = [t for t in failed if t not in reported]
        enters_loop = bool(eligible) or bool(blind_failures)
```

The completion check becomes:

```python
        if verdict == "ok" or not enters_loop:
            await db.write(lambda c: store.complete_node(c, work_item_id, node["id"]))
            return "ok"
```

After the cap check and before `fix_cycle_started`, the no-progress comparison:

```python
        if prints and prints == previous_prints:
            reason = f"no_progress: {len(prints)} finding(s) unchanged across cycle {count}"
            # Deliberately NOT mark_sessions_capped_out: these sessions did not
            # cap out, and only a real cap breach may claim they did.
            await db.write(
                lambda c, reason=reason: store.mark_needs_human(
                    c, work_item_id, node["id"], reason
                )
            )
            return "needs_human"
```

Finally the fix instruction:

```python
        instruction = _FIX_PROMPT.format(node_id=node["id"], failed=", ".join(failed))
        if eligible:
            repeats = set(previous_prints or [])
            instruction += _FIX_FINDINGS.format(findings=_format_findings(eligible, repeats))
            if repeats & set(prints):
                instruction += _FIX_REPEAT_NOTE
```

and pass `instruction_override=instruction` to the `_dispatch` call.

Add `from dataclasses import asdict` and `from kraft import findings as _findings` to the imports.

- [ ] **Step 4: Run tests to verify they pass**

Run: `just test tests/test_findings_loop.py tests/test_fix_loop.py -v`
Expected: PASS — including every pre-existing `test_fix_loop.py` case, **unedited**. That suite going green is the compatibility guarantee.

- [ ] **Step 5: Commit**

```bash
git add src/kraft/executor.py tests/test_findings_loop.py
git commit -m "feat: severity gate, finding carry-forward and no-progress escalation"
```

---

### Task 6: Show the deferred minors at the gate

**Files:**
- Modify: `src/kraft/api.py`
- Modify: `frontend/src/types.ts`, `frontend/src/components/Gate.tsx`, `frontend/src/views/WorkItemDetail.tsx`, `frontend/src/styles.css`
- Test: `tests/test_api.py`, `frontend/src/components/Gate.test.tsx`

**Interfaces:**
- Consumes: the `findings_measured` events (Task 5); `findings.from_payload` (Task 1).
- Produces: `deferred_findings: Finding[]` on `GET /work-items/{wid}`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_api.py`. Seed the events directly — no orchestration is needed to test a read path, and that file has no pytest fixtures, only the `_client(tmp_path, monkeypatch)` helper:

```python
def test_deferred_minor_findings_reach_the_detail_payload(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = client.post(
            "/work-items",
            json={"repo": str(repo), "title": "t", "chain_template": "quick-task"},
        ).json()["id"]
        nit = {"severity": "minor", "message": "naming nit", "file": "a.py",
               "line": 3, "source_plugin": "fake"}
        real = {"severity": "important", "message": "real", "file": "a.py",
                "line": 9, "source_plugin": "fake"}
        payload = {"node_id": "review", "cycle": 0,
                   "findings": [nit, real], "fingerprints": []}
        # twice: the roll-up must deduplicate by fingerprint
        _seed_events(client, wid, [payload, payload])

        body = client.get(f"/work-items/{wid}").json()
        assert [f["message"] for f in body["deferred_findings"]] == ["naming nit"]
```

Write `_seed_events` as a module-level helper in that file. Check `src/kraft/db.py` for the synchronous write path `Database` exposes; if there is only the async `write`, open a plain `sqlite3` connection to the run directory's database and call `events.append` on it, committing before the assertion.

Append to `frontend/src/components/Gate.test.tsx`. That file has no render helper — every test calls `render(<Gate ... />)` inline, so follow that:

```tsx
const deferred = [
  { severity: "minor", message: "naming nit", file: "a.py", line: 3, source_plugin: "fake" },
];

it("lists deferred minor findings at the gate", () => {
  render(<Gate item={item} gate="human_review_approval" deferred={deferred} />);
  expect(screen.getByText(/naming nit/)).toBeInTheDocument();
  expect(screen.getByText(/a\.py:3/)).toBeInTheDocument();
});

it("renders no list when there are none", () => {
  const { container } = render(
    <Gate item={item} gate="human_review_approval" deferred={[]} />,
  );
  expect(container.querySelector(".gate-deferred")).toBeNull();
});
```

Reuse the `item` fixture object the neighbouring tests in that file already build.

- [ ] **Step 2: Run tests to verify they fail**

Run: `just test -k deferred` and `cd frontend && npx vitest run Gate`
Expected: FAIL both — no such key, no such prop.

- [ ] **Step 3: Write the implementation**

In `src/kraft/api.py`, add `from kraft import findings` to the imports and derive the list from the event log — no new storage:

```python
def _deferred_findings(st, wid: str) -> list[dict]:
    """Findings that never entered the loop, for the human at the gate.

    A roll-up nobody reads is a silent discard, so these are rendered at the
    gate rather than merely recorded.
    """
    loop_severities = getattr(
        st.policy, "loop_severities", policy_mod._DEFAULT_LOOP_SEVERITIES
    )
    seen: dict[str, dict] = {}
    for e in st.db.read(lambda c: events.read_after(c, 0, wid)):
        if e["type"] != "findings_measured":
            continue
        for raw in e["payload"].get("findings", []):
            if raw.get("severity") in loop_severities:
                continue
            seen.setdefault(findings.from_payload(raw).fingerprint, raw)
    return list(seen.values())
```

`getattr` rather than a straight `st.policy.loop_severities`: `app.state.policy` is `None` when `policy.yaml` is malformed, and the detail endpoint does not check `invalid_policy` the way intake and approve do — a direct attribute access would break every detail fetch on a broken config. Check the actual import alias for `kraft.policy` in `api.py` and use it rather than assuming `policy_mod`.

Add `"deferred_findings": _deferred_findings(st, wid)` to the work-item detail payload.

In `frontend/src/types.ts`:

```ts
export interface Finding {
  severity: string;
  message: string;
  file: string | null;
  line: number | null;
  source_plugin: string;
}
```

and `deferred_findings?: Finding[];` on the work-item type — **optional**, because `GET /work-items` (the list) does not return the key and the store merges list-shaped payloads over the hydrated item, so a required field would fail `tsc`.

In `frontend/src/components/Gate.tsx`, add the prop and render it above the action row:

```tsx
  /** Findings that never entered the fix loop — the human triages them here. */
  deferred?: Finding[];
```

```tsx
      {deferred && deferred.length > 0 && (
        <ul className="gate-deferred">
          {deferred.map((f) => (
            <li key={`${f.source_plugin}:${f.file}:${f.message}`}>
              <span className="field-hint">{f.severity}</span>{" "}
              <span className="mono">{f.file ? `${f.file}:${f.line ?? "?"}` : "—"}</span>{" "}
              {f.message}
            </li>
          ))}
        </ul>
      )}
```

In `frontend/src/styles.css`, add the rule — `.field-hint` and `.mono` already exist, `.gate-deferred` does not:

```css
.gate-deferred {
  list-style: none; margin: 10px 0 0; padding: 10px 0 0;
  border-top: 1px solid var(--color-divider);
  display: flex; flex-direction: column; gap: 6px;
  font-size: 12px; color: var(--color-neutral-300);
}
```

In `frontend/src/views/WorkItemDetail.tsx`, pass it **only at the review gate**, mirroring how `artifact` is already scoped:

```tsx
  deferred={gate === "human_review_approval" ? item.deferred_findings : undefined}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `just test && just test-ui`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/kraft/api.py frontend/src/types.ts frontend/src/components/Gate.tsx \
        frontend/src/components/Gate.test.tsx frontend/src/views/WorkItemDetail.tsx \
        frontend/src/styles.css tests/test_api.py
git commit -m "feat: deferred minor findings shown at the review gate"
```

---

### Task 7: Verify and hand back

- [ ] **Step 1: Full gates**

Run: `just test && just test-ui && just lint`
Expected: all pass.

- [ ] **Step 2: Confirm the compatibility claim explicitly**

Run: `git diff --stat <the commit before Task 1> -- tests/test_fix_loop.py`
Expected: empty. If that file changed, the backward-compatibility guarantee was not kept, and the change needs justifying rather than accepting.

- [ ] **Step 3: Hand back**

Report the gate numbers. Bead bookkeeping, the merge request and the merge are the coordinator's, not this task's.

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| §1 result-file contract, best-effort parsing, non-clean-with-no-findings | 1, 5 |
| §2 severity gate, config, roll-up rendered at the review gate | 2, 6 |
| §2 config durable across a Settings save | 2 |
| §3 fingerprint, `line` and severity excluded, normalization | 1 |
| §4 `findings_measured`; `fingerprints` = eligible subset only | 5 |
| §4 no new table; previous cycle read from the event log, not a local | 5 |
| §4 no-progress rule; not `capped_out`; counter left as-is | 5 |
| §5 fix prompt carries findings and marks repeats | 5 |
| §6 compatibility — no `findings` key behaves as today | 1, 5, guarded by Task 7 Step 2 |
| §7 what is not ported | no task — deliberate |
| §8 reviewer contract | no task — belongs to the review-plugin effort |
| §9 review package | no task — `Kraft-8mu.6.1`, blocked by `Kraft-8mu.2` |

**Placeholders:** none. Task 4 builds the fixture the later tests need rather than assuming a harness exists. Where a name could not be verified from the plan's vantage — the synchronous DB write path in Task 6, the `kraft.policy` import alias in `api.py`, `get_policy`'s projection in Task 2 — the plan says to check rather than asserting a name.

**Type consistency:** `Finding` is `(severity, message, file, line, source_plugin)` in Task 1, `asdict`-serialised into the event in Task 5, rebuilt with `from_payload` in Task 6, and typed identically in TypeScript. `fingerprint` is a property everywhere. `loop_severities` is a `frozenset[str]` in Task 2, used with `in` in Tasks 5 and 6. `_collect_findings` returns `(list[Finding], set[str])` and both halves are consumed.

**What the previous draft got wrong**, recorded so it is not re-made: it invented a `loop_env` harness that could not exist, because no measuring task can emit findings; it carried the previous cycle's fingerprints in a local, which dies on the resume path the feature exists to serve; it let `PUT /policy` silently erase the new config block; it dereferenced `st.policy`, which can be `None`; it asserted a fingerprint count its own implementation could never produce; its REPEAT test could never reach the REPEAT path; and it used a CSS class it never defined — the third time in this series a plan invented a style.
