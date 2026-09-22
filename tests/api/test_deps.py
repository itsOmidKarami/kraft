"""`api.deps.spawn`/`cancel`: one live task per key, and a cancel that
actually finishes before it returns."""

from __future__ import annotations

import asyncio
import gc
import warnings
from types import SimpleNamespace

import pytest
import yaml
from fastapi import HTTPException

from kraft.api import deps


def _app():
    return SimpleNamespace(state=SimpleNamespace(tasks={}))


def _st(tmp_path):
    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    return SimpleNamespace(templates_dir=templates_dir, skills_dir=None)


def test_launch_returns_the_matching_repo_entry(tmp_path):
    st = _st(tmp_path)
    (st.templates_dir / "repos.yaml").write_text(
        "repos:\n  - path: /work/repo\n    models: {claude_review: opus}\n"
        "  - {path: /work/repo/libs/a, id: lib-a}\n  - {path: /work/other}\n"
    )
    ctx = deps.launch(st, "/work/repo")
    assert ctx.repo_entry["models"] == {"claude_review": "opus"}
    # What a task fanned out to a workspace member reads: every entry by id.
    assert {k: v["path"] for k, v in ctx.repositories.items()} == {"lib-a": "/work/repo/libs/a"}


def test_launch_on_a_malformed_repos_yaml_does_not_raise(tmp_path):
    """Reattach (`startup.py`) calls this outside any `guard` -- a malformed
    `repos.yaml` must not crash it."""
    st = _st(tmp_path)
    (st.templates_dir / "repos.yaml").write_text(
        "repos:\n  - path: /work/repo\n    sandbox:\n      kind: podman\n      image: x\n"
    )
    ctx = deps.launch(st, "/work/repo")
    assert ctx.repo_entry is not None  # poisoned, not silently None


def test_launch_on_a_malformed_repos_yaml_fails_the_dispatch_that_reads_it(tmp_path):
    """The bare-metal fallback this closes: a broken `repos.yaml` must not let
    a dispatch quietly resolve `repo_entry` to `{}` and run unsandboxed --
    every real reader hits `.get(...)`, which is where this raises."""
    from kraft import config as config_mod

    st = _st(tmp_path)
    (st.templates_dir / "repos.yaml").write_text(
        "repos:\n  - path: /work/repo\n    sandbox:\n      kind: podman\n      image: x\n"
    )
    ctx = deps.launch(st, "/work/repo")
    with pytest.raises(config_mod.ConfigError):
        ctx.repo_entry.get("sandbox")
    with pytest.raises(config_mod.ConfigError):
        ctx.repositories.get("lib-a")  # a fanned-out member fails the same way
    # Falsy fallbacks (`launch.repo_entry or {}`) must not discard the
    # poisoned entry for a harmless empty dict before that `.get` runs.
    assert bool(ctx.repo_entry)
    assert (ctx.repo_entry or {}) is ctx.repo_entry


async def _never_returning():
    await asyncio.Event().wait()


async def test_spawn_refuses_a_second_live_task_for_the_same_id():
    app = _app()
    first = deps.spawn(app, "w1", _never_returning())
    assert app.state.tasks["w1"] is first
    with pytest.raises(deps.AlreadyRunning):
        deps.spawn(app, "w1", _never_returning())
    assert app.state.tasks["w1"] is first  # untouched by the refused call
    first.cancel()
    await asyncio.gather(first, return_exceptions=True)


async def test_spawn_closes_the_refused_coroutine_with_no_leak_warning():
    app = _app()
    first = deps.spawn(app, "w1", _never_returning())
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with pytest.raises(deps.AlreadyRunning):
            deps.spawn(app, "w1", _never_returning())
        gc.collect()
    assert not any("was never awaited" in str(w.message) for w in caught)
    first.cancel()
    await asyncio.gather(first, return_exceptions=True)


async def test_spawn_allows_a_new_task_once_the_old_one_is_done():
    app = _app()

    async def quick():
        return "done"

    first = deps.spawn(app, "w1", quick())
    await first
    await asyncio.sleep(0)  # let the done-callback pop the entry
    assert "w1" not in app.state.tasks
    second = deps.spawn(app, "w1", quick())
    assert app.state.tasks["w1"] is second
    await second


async def test_cancel_awaits_the_task_so_a_fresh_spawn_is_not_refused():
    app = _app()

    async def guarded():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            raise  # deps.guard's own shape: re-raise, never swallow

    task = deps.spawn(app, "w1", guarded())
    await deps.cancel(app, "w1")
    assert task.done() and task.cancelled()
    assert "w1" not in app.state.tasks
    # the point of `cancel`: no await between it and the next spawn, and
    # spawn must still not be refused
    second = deps.spawn(app, "w1", guarded())
    assert app.state.tasks["w1"] is second
    await deps.cancel(app, "w1")


async def test_cancel_with_timeout_returns_before_a_slow_task_finishes_unwinding():
    """Kraft-c5ui: a task parked somewhere that doesn't see the cancellation
    right away (a to_thread git/forge call, in production) must not block
    the caller forever. `timeout` returns at the deadline regardless, and
    the task is left to keep cancelling in the background on its own."""

    app = _app()
    started_cancel = asyncio.Event()

    async def slow_to_unwind():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            started_cancel.set()
            await asyncio.sleep(0.2)  # stands in for a to_thread call still in flight
            raise

    task = deps.spawn(app, "w1", slow_to_unwind())
    await asyncio.sleep(0)  # let it reach its own `await` before cancelling --
    # cancel()'d before its first tick, a task's body never runs at all (its
    # own try/except included), and `started_cancel` would never be set.
    await deps.cancel(app, "w1", timeout=0.01)
    await started_cancel.wait()
    assert not task.done()  # still unwinding -- cancel returned anyway
    assert "w1" in app.state.tasks  # its done-callback hasn't fired yet

    await asyncio.gather(task, return_exceptions=True)
    await asyncio.sleep(0)  # let the done-callback run
    assert "w1" not in app.state.tasks  # pops on its own once it actually exits


async def test_cancel_on_an_absent_or_already_done_task_is_a_noop():
    app = _app()
    await deps.cancel(app, "nope")  # nothing there at all
    assert "nope" not in app.state.tasks

    async def quick():
        return 1

    task = deps.spawn(app, "w1", quick())
    await task
    await asyncio.sleep(0)  # let the done-callback pop "w1" first
    await deps.cancel(app, "w1")  # already done -- must not raise
    assert "w1" not in app.state.tasks


async def test_guard_reraises_assertionerror_instead_of_marking_needs_human():
    """Kraft-hujmb: `deps.guard`'s broad `except Exception` must not treat
    Kraft's own broken invariant (or the real-agent-binary test guard, which
    also raises `AssertionError`) as an ordinary executor crash --
    `dispatch.measure_node` already carves this same exception out and lets
    it propagate (Kraft-cpotk); `guard` must do the same."""
    calls = []

    class _DB:
        async def write(self, fn):
            calls.append(fn)

    async def boom():
        raise AssertionError("broken invariant")

    with pytest.raises(AssertionError):
        await deps.guard(_DB(), "w1", boom())

    assert calls == []  # never tried to mark_needs_human for this


def test_load_library_resolves_skills_against_the_operator_overlay(tmp_path):
    """Kraft-vhcop: a chain's `skill:` is checked when the chain resolves, so
    the app's library must know the operator's overlay -- or a method only the
    operator ships makes every chain selecting it unresolvable."""
    import yaml

    templates = tmp_path / "templates"
    (templates / "chains").mkdir(parents=True)
    task = {"kind": "agent", "harness": "codex_default", "prompt": "p", "skill": "house"}
    (templates / "library.yaml").write_text(yaml.safe_dump({"tasks": {"t": task}}))
    (templates / "chains" / "default.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "default",
                "nodes": [{"id": "n", "kind": "exec", "tasks": [{"id": "a", "extends": "t"}]}],
            }
        )
    )
    skills = tmp_path / "skills"
    (skills / "house").mkdir(parents=True)
    (skills / "house" / "SKILL.md").write_text("ours")

    library, errors = deps.load_library(templates, skills)

    assert errors == []
    assert library.lint() == []


def test_resolve_chain_or_422_prefixes_the_resolvers_own_message(tmp_path):
    """Three intake doors (`POST /work-items`, `POST /triggers`, the
    auto-intake poller) share this function so they answer an unknown chain
    id the same way. `resolve_chain`'s own `TemplateLibraryError` already
    names the id -- `chain template {id!r}: ` is what `resolve_chain_or_422`
    itself adds on top, and that prefix is what says the failure is *this*
    door's `chain_template` field rather than something buried further in the
    library."""
    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "library.yaml").write_text(yaml.safe_dump({"tasks": {}}))
    st = SimpleNamespace(templates_dir=templates, skills_dir=None)
    st.library, errors = deps.load_library(templates)
    assert errors == []

    with pytest.raises(HTTPException) as excinfo:
        deps.resolve_chain_or_422(st, "nope")

    assert excinfo.value.status_code == 422
    assert excinfo.value.detail.startswith("chain template 'nope': ")
    assert "no chain 'nope'" in excinfo.value.detail
