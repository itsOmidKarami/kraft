"""`api.deps.spawn`/`cancel`: one live task per key, and a cancel that
actually finishes before it returns."""

from __future__ import annotations

import asyncio
import gc
import json
import os
import shutil
import sqlite3
import time
import warnings
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from fastapi import HTTPException
from support import permissions

from kraft import store
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
        "repos:\n  - path: /work/repo\n    models: {claude: opus}\n"
        "  - {path: /work/repo/libs/a, id: lib-a}\n  - {path: /work/other}\n"
    )
    ctx = deps.launch(st, "/work/repo")
    assert ctx.repo_entry.models == {"claude": "opus"}
    # What a task fanned out to a workspace member reads: every entry by id.
    assert {k: v.path for k, v in ctx.repositories.items()} == {"lib-a": "/work/repo/libs/a"}


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


@pytest.mark.parametrize(
    "timeout", [pytest.param(None, id="unbounded"), pytest.param(5.0, id="bounded")]
)
async def test_cancel_does_not_swallow_a_cancellation_aimed_at_its_caller(timeout):
    """Kraft-1zhnj: a caller cancelled while it waits here (the queue's
    scheduler task at shutdown) ends cancelled. The walk's `CancelledError`
    and its own look the same at that await."""
    app = _app()
    unwinding = asyncio.Event()

    async def slow_to_unwind():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            unwinding.set()
            await asyncio.sleep(0.05)
            raise

    walk = deps.spawn(app, "w1", slow_to_unwind())
    await asyncio.sleep(0)  # let it reach its own `await`, as above
    caller = asyncio.ensure_future(deps.cancel(app, "w1", timeout=timeout))
    await unwinding.wait()
    caller.cancel()
    await asyncio.gather(caller, walk, return_exceptions=True)
    assert caller.cancelled()


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


async def test_guard_marks_needs_human_infra_on_an_ordinary_crash(item_on):
    """An executor task crashing outside `AssertionError` is `infra`
    (Kraft UI v2 · B1): nothing about the code is wrong, the daemon just
    dropped its own task."""
    it = await item_on(
        "[{id: implementation, kind: exec, tasks: [{id: t, kind: subprocess, command: 'true'}]}]"
    )

    async def boom():
        raise ValueError("executor exploded")

    await deps.guard(it.database, it.id, boom())

    assert it.status() == "needs_human"
    assert it.row()["stop_kind"] == "infra"
    assert it.events("work_item_needs_human")[-1]["payload"]["facts"] == {"cause": "crash"}


def test_load_library_resolves_skills_against_the_operator_overlay(tmp_path):
    """Kraft-vhcop: a chain's `skill:` is checked when the chain resolves, so
    the app's library must know the operator's overlay -- or a method only the
    operator ships makes every chain selecting it unresolvable."""
    import yaml

    templates = tmp_path / "templates"
    (templates / "chains").mkdir(parents=True)
    task = {"kind": "agent", "harness": "codex", "prompt": "p", "skill": "house"}
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
    names the id -- `chain {id!r}: ` is what `resolve_chain_or_422`
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
    assert excinfo.value.detail.startswith("chain 'nope': ")
    assert "no chain 'nope'" in excinfo.value.detail


@pytest.fixture
def templates_dir(tmp_path, monkeypatch):
    return permissions.templates(tmp_path, monkeypatch)


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", "/api/work-items/w1/gates/review/approve", {}),
        ("POST", "/api/work-items/w1/gates/review/reject", {"note": "no"}),
        ("POST", "/api/work-items/w1/pause", None),
        ("POST", "/api/work-items/w1/resume", {}),
        ("POST", "/api/work-items/w1/skip", {}),
        ("POST", "/api/work-items/w1/abandon", None),
        ("POST", "/api/work-items/w1/retry", {}),
        ("POST", "/api/work-items/w1/complete", {"reason": "done"}),
        ("POST", "/api/work-items/w1/cancel", {"reason": "no"}),
        ("POST", "/api/work-items/w1/escalate", {"message": "help"}),
        ("PATCH", "/api/work-items/w1", {"attachments": {"spec": None}}),
        ("PATCH", "/api/work-items/w1", {"agent_overrides": {}}),
        ("PATCH", "/api/work-items/w1", {"node_overrides": {"implementation": {}}}),
        ("PATCH", "/api/work-items/w1", {"policy": {}}),
        ("PATCH", "/api/work-items/w1", {"budget_usd": 100}),
        ("POST", "/api/work-items/w1/budget/raise", {"budget_usd": 50.0}),
        ("GET", "/api/work-items/w1/draft", None),
        ("PUT", "/api/work-items/w1/draft", {"ops": []}),
        ("DELETE", "/api/work-items/w1/draft", None),
        ("POST", "/api/work-items/w1/draft/apply", None),
    ],
    ids=[
        "approve",
        "reject",
        "pause",
        "resume",
        "skip",
        "abandon",
        "retry",
        "complete",
        "cancel",
        "escalate",
        "set-attachments",
        "agent-overrides",
        "node-overrides",
        "policy",
        "budget",
        "raise-budget",
        "get-draft",
        "put-draft",
        "discard-draft",
        "apply-draft",
    ],
)
@pytest.mark.parametrize(
    ("caller", "refused"),
    [("s-own", True), ("s-escalation", False), ("s-other", False), (None, False)],
    ids=["own-worker", "own-escalation", "other-items-worker", "no-header"],
)
def test_a_worker_session_cannot_act_on_its_own_item(client, method, path, body, caller, refused):
    """`deps.forbid_self_action`, the server's twin of the CLI's guard (design
    §6 rule 2): a consistency check, not a boundary, as a caller can omit the
    header. An escalation turn is not a worker, so it may, except approve or
    reject its own item's gate."""
    permissions.seed_session(sid="s-escalation", wid="w1", hook_point="escalation")
    permissions.seed_session(sid="s-other", wid="w2")
    db = Path(os.environ["KRAFT_RUN_DIR"]) / "orchestrator.db"
    with closing(sqlite3.connect(db)) as conn, conn:
        store.create_session(
            conn,
            id="s-own",
            work_item_id="w1",
            node_id="implementation",
            hook_point=permissions.PATH,
            log_path="/tmp/kraft-test.log",
            result_path="/tmp/kraft-test.json",
        )
    headers = {"X-Kraft-Session-Id": caller} if caller else {}
    # Kraft-9efnk.16 / .29: a gate and a spending cap are a human's, not the
    # escalation agent's.
    spending = path.endswith("/budget/raise") or (body or {}).keys() & {"budget_usd", "policy"}
    if caller == "s-escalation" and (path.endswith(("/approve", "/reject")) or spending):
        refused = True
    r = client.request(method, path, json=body, headers=headers)
    assert (r.status_code == 403) is refused, r.text
    if refused:
        assert "cannot act on its own work item (w1)" in r.json()["detail"]


def test_a_missing_worktree_says_removed_once_the_item_has_run(client, repo):
    """An item that never started has no worktree "yet"; one that ran and
    lost its directory must not read the same, on all four routes that need it,
    and the detail says which it is."""
    wid = client.post(
        "/api/work-items",
        json={"repo": str(repo), "title": "t", "chain_template": "quick-task", "autostart": False},
    ).json()["id"]
    conn = sqlite3.connect(Path(os.environ["KRAFT_RUN_DIR"]) / "orchestrator.db")
    conn.execute("UPDATE work_items SET base_ref = 'abc' WHERE id = ?", (wid,))
    conn.commit()

    def refusals():
        calls = [
            client.get(f"/api/work-items/{wid}/diff"),
            client.get(f"/api/work-items/{wid}/compare"),
            client.post(f"/api/work-items/{wid}/open-worktree", json={"editor": None}),
            client.post(f"/api/work-items/{wid}/mr-labels", json={"labels": ["x"]}),
        ]
        assert [r.status_code for r in calls] == [404] * 4
        return {r.json()["detail"] for r in calls}

    assert not client.get(f"/api/work-items/{wid}").json()["worktree_exists"]
    assert refusals() == {"this work item has not started, so it has no worktree yet"}

    conn.execute("UPDATE work_items SET current_node_id = 'implementation' WHERE id = ?", (wid,))
    conn.commit()
    conn.close()
    assert refusals() == {"this work item's worktree was removed from disk"}

    worktree = Path(client.get(f"/api/work-items/{wid}").json()["worktree_path"])
    worktree.mkdir(parents=True)
    assert client.get(f"/api/work-items/{wid}").json()["worktree_exists"]


# ── installed plugins in the running library ──


def _install_release(client, tmp_path, **spec):
    from support.plugins import AGENT, chain, install, make_collection

    st = client.app.state
    plugin = {"library": {"tasks": {"base": AGENT}}, "chains": {"ship": chain("base")}, **spec}
    store = install(
        st.templates_dir,
        st.run_dirs.plugins,
        make_collection(tmp_path, {"release": plugin}),
        "release",
    )
    deps._reload_templates(st)
    return store


def test_the_running_library_holds_the_installed_plugins(client, tmp_path):
    _install_release(client, tmp_path)
    assert "release:ship" in client.app.state.library.chain_ids
    assert client.get("/api/health").json()["status"] == "ok"


def _edit_a_stored_file(st, store):
    (store / "library.yaml").chmod(0o644)
    (store / "library.yaml").write_text("tasks: {}\n")


def _delete_the_store(st, store):
    for path in [store, *store.rglob("*")]:
        path.chmod(0o755)
    shutil.rmtree(store)


def _disable(st, store):
    (st.templates_dir / "plugins.yaml").write_text(
        (st.templates_dir / "plugins.yaml")
        .read_text()
        .replace("release@acme: true", "release@acme: false")
    )


def _break_plugins_yaml(st, store):
    (st.templates_dir / "plugins.yaml").write_text("plugins: [not, a, mapping]\n")


@pytest.mark.parametrize(
    ("change", "key", "why", "kept"),
    [
        (_edit_a_stored_file, "plugin release@acme", "do not match the locked digest", False),
        (_delete_the_store, "plugin release@acme", "store is missing", False),
        (_disable, None, None, False),
        # A file that does not read is refused: what is running stays.
        (_break_plugins_yaml, "plugins.yaml", "plugins.yaml", True),
    ],
    ids=["store-edited", "store-deleted", "disabled-is-no-fault", "plugins-yaml-unreadable"],
)
def test_health_names_a_plugin_that_did_not_load(client, tmp_path, change, key, why, kept):
    """A plugin left out at load degrades health like a chain that does not
    resolve: the instance is not running what its lock says."""
    store = _install_release(client, tmp_path)
    st = client.app.state
    change(st, store)
    deps._reload_templates(st)
    body = client.get("/api/health").json()
    assert ("release:ship" in st.library.chain_ids) is kept
    assert body["status"] == ("degraded" if key else "ok")
    if key:
        assert why in body["invalid_templates"][key]


def test_a_plugin_reload_leaves_the_operators_pending_edits_pending(client, tmp_path):
    """A plugin verb applies its own change: an unsaved-to-the-server edit of
    `policy.yaml` is not taken along."""
    from support.plugins import AGENT, chain, install, make_collection

    st = client.app.state
    release = {"library": {"tasks": {"base": AGENT}}, "chains": {"ship": chain("base")}}
    install(
        st.templates_dir,
        st.run_dirs.plugins,
        make_collection(tmp_path, {"release": release}),
        "release",
    )
    policy = st.templates_dir / "policy.yaml"
    policy.write_text(policy.read_text() + "\n# edited by hand\n")
    # A lock a teammate's pull brought in is a pending reload like any hand edit.
    assert [i["file"] for i in client.get("/api/apply").json()["reload"]] == [
        "policy.yaml",
        "plugins.yaml",
        "plugins.lock",
    ]

    r = client.post("/api/templates/reload", json={"only": ["plugins.yaml", "plugins.lock"]})

    assert r.status_code == 200 and "release:ship" in r.json()["valid"]
    assert [i["file"] for i in client.get("/api/apply").json()["reload"]] == ["policy.yaml"]
    assert client.post("/api/templates/reload", json={"only": ["policy.yaml"]}).status_code == 422
    client.post("/api/templates/reload")
    assert client.get("/api/apply").json()["reload"] == []


@pytest.mark.parametrize("file", ["plugins.yaml", "plugins.lock"])
def test_a_reload_refuses_a_plugin_file_that_does_not_read(client, tmp_path, file):
    """The running instance keeps its plugins, and the file stays pending with
    the reason, until it reads again."""
    from support.plugins import AGENT, chain, install, make_collection

    st = client.app.state
    release = {"library": {"tasks": {"base": AGENT}}, "chains": {"ship": chain("base")}}
    install(
        st.templates_dir,
        st.run_dirs.plugins,
        make_collection(tmp_path, {"release": release}),
        "release",
    )
    client.post("/api/templates/reload")
    assert "release:ship" in st.library.chain_ids
    path = st.templates_dir / file
    good = path.read_text()
    path.write_text("not_a_key: 1\n")

    body = client.post("/api/templates/reload").json()

    assert "release:ship" in body["valid"]
    assert "plugins.yaml" in body["invalid_templates"]
    assert [i["file"] for i in client.get("/api/apply").json()["reload"]] == [file]
    path.write_text(good)
    client.post("/api/templates/reload")
    assert client.get("/api/apply").json()["reload"] == []


def test_a_reload_restores_a_store_the_lock_names_and_this_machine_lacks(client, tmp_path):
    """A config directory copied to a new machine, or a teammate's lock pulled in."""
    from support.plugins import drop_store

    store = _install_release(client, tmp_path)
    drop_store(store)
    deps._reload_templates(client.app.state)
    assert "plugin release@acme" in client.get("/api/health").json()["invalid_templates"]

    client.post("/api/templates/reload")
    _plugin_task_done(client)

    assert store.is_dir() and "release:ship" in client.app.state.library.chain_ids
    assert client.get("/api/health").json()["status"] == "ok"


def test_a_store_a_launch_put_back_is_loaded_by_the_next_check(client, tmp_path):
    """The server started without it (offline, say) and left the plugin out;
    a work item pinned to it then restored the store by itself."""
    from support.plugins import drop_store

    st = client.app.state
    store = _install_release(client, tmp_path)
    kept = tmp_path / "kept"
    shutil.copytree(store, kept)
    drop_store(store)
    deps._reload_templates(st)
    assert "plugin release@acme" in client.get("/api/health").json()["invalid_templates"]
    shutil.copytree(kept, store)

    client.portal.call(deps.restore_plugins, client.app)

    assert "release:ship" in st.library.chain_ids
    assert client.get("/api/health").json()["status"] == "ok"


def test_intake_on_a_plugin_whose_store_is_away_says_retry_not_unknown(client, tmp_path):
    from support.plugins import drop_store

    st = client.app.state
    store = _install_release(client, tmp_path)
    with pytest.raises(HTTPException) as no_such_chain:
        deps.resolve_chain_or_422(st, "release:nope")
    assert no_such_chain.value.status_code == 422
    drop_store(store)
    deps._reload_templates(st)

    with pytest.raises(HTTPException) as away:
        deps.resolve_chain_or_422(st, "release:ship")
    with pytest.raises(HTTPException) as unknown:
        deps.resolve_chain_or_422(st, "nope")
    with pytest.raises(HTTPException) as longer_name:
        # `release:` is the end of this name, not the plugin's namespace.
        deps.resolve_chain_or_422(st, "pre-release:ship")

    assert (away.value.status_code, away.value.detail) == (
        503,
        "plugin release@acme is being restored; retry",
    )
    assert unknown.value.status_code == longer_name.value.status_code == 422


def _plugin_task_done(client):
    """Wait for the server's background plugin task: a reload starts one."""

    async def done():
        await client.app.state.restore_task

    client.portal.call(done)


def _other_version(st, tmp_path):
    """A second extracted plugin in the store that the lock does not name."""
    from support.plugins import extracted

    from kraft.plugins import fetch

    return fetch.write_store(st.run_dirs.plugins, extracted(skills={"old": "an older method"}))


@pytest.mark.parametrize(
    "status, pinned, kept",
    [
        ("paused", True, True),
        ("needs_human", True, True),
        ("completed", True, False),
        ("paused", False, False),
        ("paused", "run_chain", True),
    ],
    ids=[
        "pinned-by-paused-item",
        "pinned-by-stopped-item",
        "pinned-by-ended-item",
        "pinned-by-nothing",
        "pinned-by-a-retried-items-run-chain",
    ],
)
def test_gc_keeps_what_the_lock_the_library_and_unfinished_items_read(
    client, tmp_path, status, pinned, kept
):

    from support.store_fixtures import mk_item

    st = client.app.state
    locked = _install_release(client, tmp_path)
    old = _other_version(st, tmp_path)
    (st.run_dirs.plugins / "staging" / "half-written").mkdir(parents=True)
    snapshot = {"plugins": {"release": {"id": "release@acme", "digest": f"sha256:{old.name}"}}}
    stored = json.dumps(snapshot if pinned else {})
    column = "run_chain" if pinned == "run_chain" else "materialized_chain"

    async def an_item():
        await mk_item(st.db)
        await st.db.write(
            lambda c: c.execute(
                f"UPDATE work_items SET {column} = ?, status = ? WHERE id = 'w1'",
                (stored, status),
            )
        )

    client.portal.call(an_item)

    removed = client.portal.call(deps.collect_plugin_stores, st)

    assert (old.is_dir(), removed) == (kept, [] if kept else [old.name])
    assert locked.is_dir() and not (st.run_dirs.plugins / "staging").exists()


def test_gc_runs_after_a_reload_and_never_while_the_lock_does_not_read(client, tmp_path):
    st = client.app.state
    locked = _install_release(client, tmp_path)
    old = _other_version(st, tmp_path)
    lock = st.templates_dir / "plugins.lock"
    good = lock.read_text()
    lock.write_text("plugins: [\n")
    client.post("/api/templates/reload")
    _plugin_task_done(client)
    assert old.is_dir()

    lock.write_text(good)
    client.post("/api/templates/reload")
    _plugin_task_done(client)

    assert not old.exists()
    # No library is loaded to say what it reads: the lock alone keeps its stores.
    (st.templates_dir / "library.yaml").write_text("tasks: [\n")
    client.post("/api/templates/reload")
    _plugin_task_done(client)
    assert st.library is None and locked.is_dir()


def test_gc_does_not_wait_for_a_plugin_change_that_is_running(client, tmp_path):
    """It runs on the event loop: a lock someone holds is skipped, and the
    next collection takes what this one left."""
    from kraft.plugins import update as plugin_update

    st = client.app.state
    _install_release(client, tmp_path)
    old = _other_version(st, tmp_path)

    started = time.monotonic()
    with plugin_update.write_lock(st.run_dirs.plugins):
        assert client.portal.call(deps.collect_plugin_stores, st) == []
    assert old.is_dir() and time.monotonic() - started < plugin_update.LOCK_WAIT_S / 2

    assert client.portal.call(deps.collect_plugin_stores, st) == [old.name]


def test_a_plugin_background_task_that_fails_says_so(client, caplog):
    """Nobody awaits it: what it raises is logged, not dropped."""

    async def breaks(app):
        raise OSError("disk full")

    async def run():
        await deps.in_background(client.app, breaks)

    client.portal.call(run)

    assert "plugin background task failed" in caplog.text and "disk full" in caplog.text


def test_a_running_server_checks_for_auto_updates_again(client, monkeypatch):
    """Not only at start: a server run as a service may not restart for weeks."""
    checks = []

    async def check(app):
        checks.append(app)

    async def two_checks():
        task = asyncio.ensure_future(deps.auto_update_daily(client.app))
        while len(checks) < 2:
            await asyncio.sleep(0.01)
        task.cancel()

    assert not client.app.state.plugin_update_task.done()
    monkeypatch.setattr(deps, "AUTO_UPDATE_EVERY_S", 0.01)
    monkeypatch.setattr(deps, "auto_update_plugins", check)

    client.portal.call(asyncio.wait_for, two_checks(), 5)

    assert checks[:2] == [client.app, client.app]


def test_the_server_takes_an_auto_update_and_loads_it(client, tmp_path):
    """After a start: the update is applied, the library is rebuilt on it, the
    old store goes, and health reports the outcome without being degraded."""
    from support.plugins import publish

    st = client.app.state
    old = _install_release(client, tmp_path)
    written = yaml.safe_load((st.templates_dir / "plugins.yaml").read_text())
    written["plugins"]["release@acme"] = {"auto_update": True}
    (st.templates_dir / "plugins.yaml").write_text(yaml.safe_dump(written))
    repo = Path(written["collections"]["acme"]["git"].removeprefix("file://"))
    publish(repo, "release", version="1.1.0", skills={"notes": "new in 1.1.0"})

    client.portal.call(deps.auto_update_plugins, client.app)

    assert [p.version for p in st.library.plugins] == ["1.1.0"] and not old.exists()
    health = client.get("/api/health").json()
    assert (health["status"], health["plugin_updates"]["release@acme"]["outcome"]) == (
        "ok",
        "applied",
    )


def test_a_held_or_failed_auto_update_is_reported_and_not_degraded(client, tmp_path):
    from kraft.plugins import update as plugin_update

    st = client.app.state
    _install_release(client, tmp_path)
    held = {"at": "2026-10-10T12:00:00+00:00", "outcome": "held", "kind": None, "message": "gate"}
    (st.run_dirs.plugins / plugin_update.STATUS_FILE).write_text(json.dumps({"release@acme": held}))

    health = client.get("/api/health").json()

    assert (health["status"], health["plugin_updates"]) == ("ok", {"release@acme": held})
