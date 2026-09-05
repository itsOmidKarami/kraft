# Agent Invocation Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a hook point choose its model, be denied tools it does not need, and carry per-repo standards — without hardwiring one vendor's CLI flags into user-facing config.

**Architecture:** A `PROFILES` table maps Kraft's neutral options onto one CLI's flags, so config says `model:` rather than `--model`. Steering names are **validated at config load and read at dispatch** — never resolved into the config dict, because that dict is returned by `GET /registry`, edited in Settings and written straight back. A single `resolve_invocation` folds the hook binding and the repo entry into one `Invocation`, so precedence lives in one place.

**Tech Stack:** Python 3.14, pytest.

**Spec:** `docs/superpowers/specs/2026-09-04-sub-project-c-agent-invocation-contract-design.md`

**Beads:** `Kraft-8mu.3`. Blocks `Kraft-8mu.5`, `Kraft-8mu.7.1`, `Kraft-n70`.

## Global Constraints

- Python 3.14+. Unparenthesized `except A, B:` (PEP 758) is correct in this codebase — do not "fix" it.
- **A config setting none of the new keys must produce a byte-identical command line.** `tests/test_adapters_agent.py`, `tests/test_executor.py`, `tests/test_resume.py`, `tests/test_fix_loop.py`, `tests/test_gates.py` stay green without weakening edits.
- **Never write resolved steering text into `registry.yaml` or `repos.yaml`.** See "The mistake the first draft made" below — this is the single most important constraint here.
- Kraft does not validate a model string. It has no model list, the list changes without Kraft changing, and an unknown model is an error the CLI reports well.
- A typo in `profile`, `deny_tools` or `steering` fails at **config load**, not at the first launch three nodes into a chain.
- Steering total is capped at 8 KB, counting the injected header and separators, not just the file bodies.
- No MCP config in the agent adapter (`01_conceptual_model.md` §1.2).
- Run `just lint` before each commit.

## The mistake the first draft made

A plan review killed the previous draft. Recorded so it is not re-made:

**It resolved steering names into the binding dict at load time.** `GET /registry` (`api.py:1307-1308`) returns `st.registry.hooks` verbatim; Settings → Plugins spreads those bindings and `PUT`s them back; `put_registry` writes `body.hooks` straight to `registry.yaml`. So one click of the steerable toggle would have written the inlined steering **bodies** into `registry.yaml` and deleted the `steering:` names permanently. `repos.yaml` had the identical hazard through `add_repo`/`update_repo`/`remove_repo`, which all `load_repos` → `save_repos`.

**And it validated steering against `path.parent / "steering"`.** `put_registry` validates a candidate written into a `tempfile.TemporaryDirectory()`, where that path is an empty directory — so any registry naming a steering file would have 422'd on save, blocking the feature end to end.

Hence: validate at load against the **real** steering directory, read the text at dispatch, and leave the config dict exactly as the operator wrote it.

## Scope decisions

- **No Settings screen for steering files** (spec §4 asks for one). The contract unblocks three beads; the editor is a convenience and the files are hand-editable meanwhile. Child bead filed. Note in passing: without it there is no API to list or read steering files at all.
- **`deny_tools` is accepted on repo entries too.** Spec §3's prose is hook-only but §6's test list requires "hook **and** repo lists both reach the command line". Union of the two, repo first, deduplicated, order preserved.

---

### Task 1: Agent profiles

**Files:**
- Modify: `src/kraft/adapters/agent.py`
- Test: `tests/test_adapters_agent.py`

**Interfaces:**
- Produces: `agent.Profile` (`NamedTuple`), `agent.PROFILES: dict[str, Profile]` with a `"claude"` entry, and `run_agent_task(..., profile: str = "claude", model: str | None = None, deny_tools: tuple[str, ...] = (), steering_texts: tuple[str, ...] = ())`.

Note all four new parameters land here, in this task, including `steering_texts` — Task 5 only fills it in.

- [ ] **Step 1: Write the failing tests**

`tests/test_adapters_agent.py` has **no command-line capture** — its three tests run `tests/support/fake_agent.py` as a real subprocess and assert on repo files and DB rows. Do not go looking for a mechanism to reuse; add one. The cheapest is to monkeypatch the adapter's own call into the subprocess adapter and keep the argv:

```python
def _capture_cmd(monkeypatch):
    """The command line `run_agent_task` would have run."""
    seen = {}

    async def fake_run_task(db, run_dirs, *, cmd, **kw):
        seen["cmd"] = cmd
        return "done"

    monkeypatch.setattr("kraft.adapters.agent._subprocess.run_task", fake_run_task)
    return seen
```

Then:

```python
def test_default_profile_reproduces_todays_command_line(tmp_path, monkeypatch):
    """Regression guard, not a red-green test: it passes before this task too.

    That is the point — the whole sub-project's compatibility claim is that a
    config setting none of the new keys builds the same argv as before.
    """
    # assert cmd == [*shlex.split(command), "-p", instr,
    #                "--append-system-prompt", ctx, "--output-format", "json"]


def test_model_is_passed_through_as_a_flag(...):        # ["--model", "opus"], nothing else
def test_no_model_emits_no_model_flag(...):             # "--model" not in cmd  (regression guard)
def test_deny_tools_become_one_comma_joined_flag(...):  # ["--disallowed-tools", "WebFetch,Bash"]
def test_empty_deny_tools_emits_no_flag(...):           # regression guard
def test_unknown_profile_raises_naming_the_profile(...) # ValueError mentioning "nope"
```

Mark the three regression guards in their docstrings. Step 2 will not turn them red, and a plan that claims otherwise teaches an implementer to distrust its own expectations.

- [ ] **Step 2: Run tests to verify they fail**

Run: `just test tests/test_adapters_agent.py -v`
Expected: the model, deny-tools and unknown-profile tests FAIL (`run_agent_task() got an unexpected keyword argument`). The three regression guards pass already — that is correct and expected.

- [ ] **Step 3: Write the implementation**

Add `from typing import NamedTuple` to the imports — it is not there today.

```python
class Profile(NamedTuple):
    """How one agent CLI spells the options Kraft names neutrally.

    A fact about a CLI, not user configuration — a user editing this is a user
    reporting a bug. It exists so `registry.yaml` can say `model:` rather than
    `--model`, which is what stops one vendor's flags leaking into the config an
    operator edits by hand.
    """

    prompt: tuple[str, ...]
    system_prompt: tuple[str, ...]
    output_json: tuple[str, ...]
    model: tuple[str, ...]
    deny_tools: tuple[str, ...]


PROFILES: dict[str, Profile] = {
    "claude": Profile(
        prompt=("-p",),
        system_prompt=("--append-system-prompt",),
        output_json=("--output-format", "json"),
        model=("--model",),
        deny_tools=("--disallowed-tools",),
    ),
}
```

In `run_agent_task`, after `ctx` is built:

```python
    try:
        prof = PROFILES[profile]
    except KeyError:
        raise ValueError(
            f"unknown agent profile {profile!r}; known: {sorted(PROFILES)}"
        ) from None

    cmd = [
        *shlex.split(command),
        *prof.prompt,
        task_instruction,
        *prof.system_prompt,
        ctx,
        *prof.output_json,
    ]
    if model:
        cmd += [*prof.model, model]
    if deny_tools:
        cmd += [*prof.deny_tools, ",".join(deny_tools)]
```

Leave `_envelope_is_error` alone — Claude-shaped, already isolated behind `post_resolve`, and generalizing a parser against one known format is guesswork.

- [ ] **Step 4: Run tests to verify they pass**

Run: `just test tests/test_adapters_agent.py -v`

- [ ] **Step 5: Commit**

```bash
git add src/kraft/adapters/agent.py tests/test_adapters_agent.py
git commit -m "feat: agent profiles map neutral options onto one CLI's flags"
```

---

### Task 2: Steering files — validate and read, separately

**Files:**
- Create: `src/kraft/steering.py`
- Create: `templates/steering/README.md`
- Test: `tests/test_steering.py`

**Interfaces:**
- Produces: `steering.MAX_BYTES = 8192`; `steering.SteeringError`; `steering.validate(steering_dir, names, *, where) -> None`; `steering.read(steering_dir, names) -> tuple[str, ...]`.

Two functions on purpose: `validate` runs at config load against the real directory, `read` runs at dispatch. Nothing writes the text back into config.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_steering.py`:

```python
import os

import pytest

from kraft import steering


def _dir(tmp_path, files):
    d = tmp_path / "steering"
    d.mkdir()
    for name, body in files.items():
        (d / f"{name}.md").write_text(body)
    return d


def test_read_returns_bodies_in_the_order_named(tmp_path):
    d = _dir(tmp_path, {"a": "alpha", "b": "beta"})
    assert steering.read(d, ["b", "a"]) == ("beta", "alpha")


def test_no_names_never_touches_the_directory(tmp_path):
    """Keeps every existing fixture green: none of them ship a steering/ dir."""
    assert steering.read(tmp_path / "absent", []) == ()
    steering.validate(tmp_path / "absent", [], where="x")


def test_validate_names_the_missing_file_and_the_config_that_asked(tmp_path):
    d = _dir(tmp_path, {"a": "alpha"})
    with pytest.raises(steering.SteeringError, match="nope"):
        steering.validate(d, ["nope"], where="registry.yaml")


def test_validate_rejects_a_traversing_name(tmp_path):
    """`../../repo/CLAUDE` would read a file inside a target repo, which is the
    exact channel the context-injection boundary forbids."""
    d = _dir(tmp_path, {"a": "alpha"})
    for bad in ("../secret", "nested/thing", "/etc/passwd"):
        with pytest.raises(steering.SteeringError):
            steering.validate(d, [bad], where="x")


def test_validate_rejects_an_over_budget_total(tmp_path):
    d = _dir(tmp_path, {"big": "x" * steering.MAX_BYTES})
    with pytest.raises(steering.SteeringError, match="8192"):
        steering.validate(d, ["big"], where="x")


def test_the_budget_is_the_total_not_per_file(tmp_path):
    half = "x" * (steering.MAX_BYTES // 2 + 10)
    d = _dir(tmp_path, {"a": half, "b": half})
    with pytest.raises(steering.SteeringError):
        steering.validate(d, ["a", "b"], where="x")


@pytest.mark.skipif(os.getuid() == 0, reason="root ignores the mode bits")
def test_a_permissions_error_is_not_reported_as_missing(tmp_path):
    d = _dir(tmp_path, {"a": "alpha"})
    (d / "a.md").chmod(0o000)
    try:
        with pytest.raises(steering.SteeringError, match="cannot read"):
            steering.validate(d, ["a"], where="x")
    finally:
        (d / "a.md").chmod(0o644)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `just test tests/test_steering.py`
Expected: FAIL — no module `kraft.steering`.

- [ ] **Step 3: Write the implementation**

Create `src/kraft/steering.py`:

```python
"""Per-repo and per-hook standards, injected through the system prompt.

The context-injection boundary (`00_overview.md` glossary) bans `CLAUDE.md`,
`AGENTS.md` and any repo file as a context channel. That rule governs the
*channel*, not the existence of standards — this is the other half: authored,
Kraft-owned files under `$KRAFT_HOME/templates/steering/`, reaching the agent
through the per-invocation system prompt the adapter already builds. Kraft never
reads or writes a steering file inside a target repository, which is why a name
containing a path separator is rejected outright.

`validate` and `read` are separate because the text must never be written back
into `registry.yaml` or `repos.yaml`: those dicts round-trip through
`GET /registry` and the Settings screens, so resolving into them would inline the
bodies into the operator's config and delete the names.
"""

from __future__ import annotations

from pathlib import Path

#: Total injected budget, counting the header and separators, not just bodies.
#: An oversized system prompt degrades every launch and costs money on each.
MAX_BYTES = 8192

#: What `adapters/agent.py` wraps the bodies in. Defined here so the budget and
#: the injection cannot drift apart; Task 5 asserts the two stay equal.
HEADING = "\n\n## Project standards\n\n"
_OVERHEAD = len(HEADING.encode())


class SteeringError(Exception):
    pass


def _path(steering_dir: Path, name: str, where: str) -> Path:
    if "/" in name or "\\" in name or name in ("", ".", "..") or name.startswith("."):
        raise SteeringError(
            f"{where}: steering name {name!r} must be a bare file name — Kraft "
            "never reads a steering file from outside its own templates directory"
        )
    return Path(steering_dir) / f"{name}.md"


def validate(steering_dir: Path, names: list[str], *, where: str) -> None:
    """Every name resolves and the total fits the budget, or raise.

    Runs at config load, against the *real* steering directory — not against
    whatever directory a candidate file was written into for validation.
    """
    total = 0
    for name in names:
        path = _path(steering_dir, name, where)
        if not path.is_file():
            raise SteeringError(f"{where}: steering {name!r} not found at {path}")
        try:
            total += len(path.read_text().encode())
        except OSError as exc:
            raise SteeringError(f"{where}: cannot read steering {name!r} at {path}: {exc}") from exc
    if names and total + _OVERHEAD > MAX_BYTES:
        raise SteeringError(
            f"{where}: steering totals {total} bytes, over the {MAX_BYTES} byte budget"
        )


def read(steering_dir: Path, names: list[str]) -> tuple[str, ...]:
    """Bodies for `names`, in order. Called at dispatch, after validation."""
    return tuple(_path(steering_dir, n, "steering").read_text() for n in names)
```

`HEADING` lives here rather than in the adapter so the budget and the injection
cannot drift: Task 5 imports it and asserts `len(HEADING.encode()) == _OVERHEAD`.
An implementer who instead invents a placeholder number here and forgets to
reconcile it ships a budget that is silently wrong by a few bytes — the same
two-tasks-disagree defect this plan warns about elsewhere.

Create `templates/steering/README.md` — a short note saying what the directory is for. **Not `.gitkeep`:** `pyproject.toml`'s package-data glob does not match leading-dot names and an empty directory has no wheel entry, so a dotfile would leave `$KRAFT_HOME/templates/steering/` uncreated on a real install.

- [ ] **Step 4: Run tests to verify they pass**

Run: `just test tests/test_steering.py -v`

- [ ] **Step 5: Commit**

```bash
git add src/kraft/steering.py templates/steering/README.md tests/test_steering.py
git commit -m "feat: steering files, validated at load and read at dispatch"
```

---

### Task 3: The new config keys, validated only

**Files:**
- Modify: `src/kraft/templates.py` (`load_registry`)
- Modify: `src/kraft/config.py` (`load_repos`)
- Modify: `src/kraft/api.py` (`put_registry`, `RepoBody`, `RepoPatch`, `add_repo`)
- Test: `tests/test_templates.py`, `tests/test_settings_api.py`

**Interfaces:**
- `load_registry(path, *, steering_dir: Path | None = None)` — defaults to `path.parent / "steering"`; validates agent-only keys. **Bindings are not mutated.**
- `load_repos(path, *, steering_dir: Path | None = None)` — same shape. Entries keep `steering` as names; `default_model` and `deny_tools` default in like `forge`/`project` already do.

- [ ] **Step 1: Write the failing tests**

`tests/test_templates.py` — new cases:

- an agent hook with none of the new keys loads exactly as before
- `profile: nope` → `RegistryError` naming it and listing the known profiles
- `model: 3` → `RegistryError`; `model: "anything-at-all"` → accepted, because Kraft does not validate model strings
- `deny_tools: "WebFetch"` (not a list) → `RegistryError`
- `steering: ["missing"]` → `RegistryError`
- **the binding dict is unchanged after load** — `"steering_texts" not in binding`, `binding["steering"] == ["house-style"]`, no `profile` key materialized. This is the regression guard for the config-corruption defect
- a `subprocess` hook carrying `model:` → `RegistryError` (the key applies only to agent hooks)

`tests/test_settings_api.py` — new cases:

- `PUT /registry` with a hook naming a real steering file **succeeds**, validated against the real templates dir rather than the temp candidate dir. This is the regression guard for the 422 defect
- `GET /registry` → `PUT /registry` round trip leaves `registry.yaml` byte-identical when nothing was edited
- `POST /repos` with `default_model` and `steering` round-trips through `repos.yaml` with the names intact
- a repo naming a missing steering file fails to load

The `templates_dir` fixture (`tests/support/harness.py` `fake_templates_dir`) creates no `steering/` directory — the steering tests must create one.

- [ ] **Step 2: Run tests to verify they fail**

Run: `just test tests/test_templates.py tests/test_settings_api.py -v`

- [ ] **Step 3: Write the implementation**

`load_registry` gains the keyword and validates without mutating:

```python
def load_registry(path: str | Path, *, steering_dir: Path | None = None) -> Registry:
    path = Path(path)
    steering_dir = steering_dir if steering_dir is not None else path.parent / "steering"
```

then, per hook, after the existing per-kind checks:

```python
        agent_only = ("profile", "model", "deny_tools", "steering")
        if kind == "agent":
            profile = binding.get("profile", "claude")
            if profile not in _agent_profiles():
                raise RegistryError(
                    f"{path.name}: hook {hook!r} has unknown profile {profile!r}; "
                    f"known: {sorted(_agent_profiles())}"
                )
            if binding.get("model") is not None and not isinstance(binding["model"], str):
                raise RegistryError(f"{path.name}: hook {hook!r} 'model' must be a string")
            for key in ("deny_tools", "steering"):
                v = binding.get(key, [])
                if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
                    raise RegistryError(
                        f"{path.name}: hook {hook!r} {key!r} must be a list of strings"
                    )
            try:
                _steering.validate(steering_dir, binding.get("steering", []), where=path.name)
            except _steering.SteeringError as exc:
                raise RegistryError(str(exc)) from exc
        else:
            for key in agent_only:
                if key in binding:
                    raise RegistryError(
                        f"{path.name}: hook {hook!r} is kind {kind!r}; {key!r} applies "
                        "only to an agent hook"
                    )
```

`_agent_profiles()` is a function-local import so `templates.py` does not depend
on the adapter layer at module scope. The reason is layering, not import weight —
`psutil` is a hard dependency and always installed, and there is no cycle today.
A config loader that imports an adapter invites one later.

`load_repos` needs `path = Path(path)` **added** — it does not convert today, it delegates to `read_yaml`, so `path.parent` would `AttributeError` on a `str` caller. Then default `default_model` to `None` and `deny_tools`/`steering` to `[]` in
`_normalize_forge`'s neighbourhood, and validate the names — wrapping
`SteeringError` as `ConfigError` exactly as `load_registry` wraps it as
`RegistryError`. `ConfigError` is the contract every other `repos.yaml` failure
raises, and letting a third exception type escape this loader would break the
first caller that tries to handle it.

`put_registry` must pass the **real** steering directory, not the temp one:

```python
            registry = load_registry(candidate, steering_dir=st.templates_dir / "steering")
```

`RepoBody`/`RepoPatch` gain `default_model: str | None = None`, `deny_tools: list[str] | None = None`, `steering: list[str] | None = None`; `add_repo` persists them.

- [ ] **Step 4: Run tests to verify they pass**

Run: `just test -v`

- [ ] **Step 5: Commit**

```bash
git add src/kraft/templates.py src/kraft/config.py src/kraft/api.py tests/
git commit -m "feat: validate profile, model, deny_tools and steering at config load"
```

---

### Task 4: `resolve_invocation`, and the launch context through the executor

**Files:**
- Modify: `src/kraft/adapters/agent.py`
- Modify: `src/kraft/executor.py`
- Modify: `src/kraft/api.py`
- Modify: `tests/support/fake_agent.py` (record full argv — see Step 1)
- Test: `tests/test_adapters_agent.py`, `tests/test_executor.py`

**Interfaces:**
- `agent.Invocation` (`command, profile, model, deny_tools, steering_texts`) and `agent.resolve_invocation(binding, repo_entry, steering_dir) -> Invocation`.
- `executor.LaunchContext` (`repo_entry: dict | None`, `steering_dir: Path | None`), threaded **keyword-only** as `launch: LaunchContext | None = None`.

- [ ] **Step 1: Write the failing tests**

`tests/test_adapters_agent.py`:

```python
def test_hook_model_beats_repo_default():
def test_repo_default_model_applies_when_the_hook_is_silent():
def test_no_model_anywhere_leaves_it_unset():
def test_deny_tools_union_repo_first_deduplicated():
    # repo ["Bash"], hook ["WebFetch", "Bash"] -> ("Bash", "WebFetch")
def test_steering_is_repo_first_then_hook():
    # assert against a TUPLE — Invocation fields are tuples, and
    # ("repo", "hook") == ["repo", "hook"] is False
def test_a_repo_entry_of_none_behaves_like_an_empty_one():
    assert resolve_invocation({"command": "c"}, None, d) == resolve_invocation({"command": "c"}, {}, d)
```

`tests/test_executor.py`: one case proving a repo `default_model` reaches the agent launch, and one proving the **fix-cycle** dispatch gets it too — that is a separate `_dispatch` call site and missing it means the measuring and fix agents disagree on model.

To assert on argv from the executor, extend `tests/support/fake_agent.py` to dump its full `sys.argv` to `KRAFT_FAKE_AGENT_ARGV_LOG` when set. Today `_record_prompt` captures only the `-p` value and breaks out of the loop, so `--model` is invisible. Add the file to this task's Files list.

- [ ] **Step 2: Run tests to verify they fail**

Run: `just test tests/test_adapters_agent.py tests/test_executor.py -v`

- [ ] **Step 3: Write the implementation**

```python
class Invocation(NamedTuple):
    command: str
    profile: str
    model: str | None
    deny_tools: tuple[str, ...]
    steering_texts: tuple[str, ...]


def resolve_invocation(binding: dict, repo_entry: dict | None, steering_dir) -> Invocation:
    """Fold a hook binding and a repo entry into one launch.

    Precedence lives here and only here. Spelling it out at each call site is how
    three features that touch the same twenty lines end up disagreeing.
    """
    repo = repo_entry or {}
    deny: list[str] = []
    for name in (*repo.get("deny_tools", ()), *binding.get("deny_tools", ())):
        if name not in deny:
            deny.append(name)
    names = [*repo.get("steering", ()), *binding.get("steering", ())]
    return Invocation(
        command=binding["command"],
        profile=binding.get("profile", "claude"),
        model=binding.get("model") or repo.get("default_model"),
        deny_tools=tuple(deny),
        # repo first, then hook: the wider context before the narrower one, and
        # fixed rather than merged cleverly — a reader debugging a prompt has to
        # be able to predict what the agent saw.
        steering_texts=_steering.read(steering_dir, names) if names and steering_dir else (),
    )
```

In `executor.py`, add `LaunchContext` and thread it **keyword-only** with a `None` default through, in this order:

| Function | line today | reached from |
|---|---|---|
| `run` | 458 | `api.py:357, 646, 722, 806, 865` |
| `resume` | 605 | `api.py:108` |
| `_reconcile_current_node` | 512 | `resume` (`:647`) |
| `_walk_node` | 296 | `run` (`:484`), `_reconcile_current_node` (`:546, :565`), `resume` (`:667`) |
| `_measure_node` | 196 | `_walk_node` (`:311, :340`) |
| `_dispatch` | 128 | `_measure_node` (`:212`), the fix cycle (`:422`) |

Keyword-only matters: `tests/test_fix_loop.py:169` and `tests/test_resume.py:59,61,115,116` call `_walk_node` **positionally** with seven arguments. A positional parameter inserted after `worktree` breaks all five.

In `_dispatch`'s agent branch, unpack the context defensively — every existing
test calls down this path with no `launch` at all, so this must not dereference
`None`:

```python
        inv = _agent.resolve_invocation(
            binding,
            launch.repo_entry if launch else None,
            launch.steering_dir if launch else None,
        )
```

then pass `inv.command`, `inv.profile`, `inv.model`, `inv.deny_tools` and
`inv.steering_texts` to `run_agent_task` in place of the bare
`command=binding["command"]`.

In `api.py`, build the context once per call and reuse the **existing** `_connected` helper for the repo lookup — do not write a fresh `r["path"] == repo` matcher. `_connected` exists precisely because `POST /repos` stores git's symlink-resolved `--show-toplevel` while callers pass the unresolved path; on macOS a naive match returns `None` and silently drops the repo's config:

```python
def _launch(st, repo: str) -> executor.LaunchContext:
    repos = config_mod.load_repos(_repos_path(st))
    return executor.LaunchContext(
        repo_entry=_connected(repos, repo),
        steering_dir=st.templates_dir / "steering",
    )
```

Pass it at all six call sites — the five `executor.run` and the one `executor.resume` at `api.py:108`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `just test -v`

- [ ] **Step 5: Commit**

```bash
git add src/kraft/adapters/agent.py src/kraft/executor.py src/kraft/api.py tests/
git commit -m "feat: resolve_invocation and the launch context through the executor"
```

---

### Task 5: Inject the steering

**Files:**
- Modify: `src/kraft/adapters/agent.py`
- Test: `tests/test_adapters_agent.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_steering_is_appended_under_a_heading(...):
def test_steering_order_is_preserved_in_the_prompt(...):   # repo body before hook body
def test_no_steering_leaves_the_system_prompt_byte_identical(...):  # regression guard
def test_the_prompt_never_names_a_repo_file(...):
    # the boundary, asserted: no CLAUDE.md / AGENTS.md path is read or referenced
```

- [ ] **Step 2: Run tests to verify they fail**

- [ ] **Step 3: Write the implementation**

In `run_agent_task`, after `ctx` is formatted:

```python
    if steering_texts:
        # The context-injection boundary bans CLAUDE.md, AGENTS.md and any repo
        # file as a context channel. It governs the *channel*: this is the
        # sanctioned one — the per-invocation system prompt — carrying files
        # Kraft owns under $KRAFT_HOME/templates/steering/. Kraft reads nothing
        # from inside the target repo. Written here because a future reader
        # finding a steering feature beside a rule banning steering files will
        # assume the rule was forgotten.
        ctx += _steering.HEADING + "\n\n".join(steering_texts)
```

where the heading is `steering.HEADING`, imported rather than redeclared, so the
injected text and the budget that sized it cannot drift. Add a module-level
`assert len(steering.HEADING.encode()) == steering._OVERHEAD` — or better, just
use `steering.HEADING` directly and delete the local name.

- [ ] **Step 4: Run tests to verify they pass**

- [ ] **Step 5: Commit**

```bash
git add src/kraft/adapters/agent.py tests/test_adapters_agent.py
git commit -m "feat: steering reaches the agent through the system prompt"
```

---

### Task 6: Verify

- [ ] **Step 1: Full gates**

Run: `just test && just test-ui && just lint`

- [ ] **Step 2: Prove the compatibility claim**

There is no logged command line to inspect — `adapters/subprocess.py` redirects only the child's stdout and stderr, and the command is never stored. So assert it in the suite instead: `test_default_profile_reproduces_todays_command_line` (Task 1) is the guard, and it must be passing with the stock `templates/registry.yaml`. Report that it is, rather than claiming a log check that cannot be done.

- [ ] **Step 3: Config round-trip check**

With a steering file named from `registry.yaml`, `GET /registry` then `PUT /registry` unchanged, and confirm `registry.yaml` on disk is byte-identical — no `steering_texts`, no inlined bodies, names intact. Same for `repos.yaml` through a `PATCH /repos`. This is the defect that killed the first draft; prove it is gone.

- [ ] **Step 4: Hand back**

Report the gate numbers. Bead bookkeeping, the MR and the merge are the coordinator's.

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| §1 profiles, `profile:` key, unknown fails at load, envelope parser untouched | 1, 3 |
| §2 model precedence hook > repo > unset; no model validation | 3, 4 |
| §2 chain templates cannot set a model | already true — `templates.materialize` copies only `id`/`tasks`/`gate_after`/`fix_loop`, so a node key is dropped. No task needed; noted so nobody "adds" it |
| §3 tier-1 `deny_tools` through the profile, hook **and** repo | 1, 3, 4 |
| §3 tier-2 OS sandbox | no task — spec defers it |
| §4 steering location, both config sites, repo-then-hook, missing name fails at load, 8 KB budget | 2, 3, 4, 5 |
| §4 the boundary preserved and said so in the code | 5 |
| §4 Settings screen | child bead — scope decision above |
| §5 one `resolve_invocation` | 4 |
| §6 testing | every task |

**Known deviation from the spec:** §5 says `run_agent_task` "takes an `Invocation` and does no lookup of its own", but this plan passes unpacked kwargs and looks up `PROFILES[profile]` inside. Flagged rather than hidden; the alternative is threading a dataclass through the subprocess adapter's signature for no gain.

**Not verifiable as the spec's acceptance states it:** "`on.ci.poll` and `on.implementation.start` run different models" cannot be shown on stock config — `on.ci.poll` is `{kind: builtin, handler: noop}`, and Task 3's `else` branch will actively reject `model:` on it. Demonstrate with two agent-kind hooks instead.

**Placeholders:** test names and intents are given where the assertion mechanism had to be invented (Task 1's `_capture_cmd`, Task 4's argv log); both are specified concretely rather than pointed at something that does not exist.

**The risk this plan carries:** Task 4 touches six functions and six external call sites. The `None` default keeps everything compiling, so a missed site fails silently as lost config rather than as an exception. The fix-cycle `_dispatch` at `executor.py:422` is the easiest to miss and has its own test for that reason.
