from __future__ import annotations

import os
import shutil
import socket
from pathlib import Path

import httpx
import pytest
from support import api as api_support
from support import harness
from support.fake_beads import Bd, FakeBeads
from support.harness import REAL_AGENT_BINARIES as _REAL_AGENT_BINARIES
from support.harness import entry_of, fake_templates_dir, isolated_bd, make_repo

from kraft import client as kraft_client
from kraft import db
from kraft.paths import RunDirs

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FAKE_CLAUDE = _REPO_ROOT / "fixtures" / "fake-claude.sh"


#: What a test that never built a `LaunchContext` gets instead of "nothing
#: declared". `setup_command: ""` is "deliberately nothing to prepare"; the two
#: empty test fields are the same statement about verification, and they are
#: here rather than inline so the three call sites below cannot drift apart.
_INERT_REPO_ENTRY = {"setup_command": "", "test_command": None, "test_scopes": None}


@pytest.fixture(autouse=True)
def _default_setup_command_for_tests_without_a_launch_context(monkeypatch):
    """`setup_command` has no default (Kraft-kji8w): `ensure_worktree` now
    raises for a bare `repo_entry=None`, which is what every executor test in
    this suite that never built a `LaunchContext` passes. Most of those tests
    are about chain walking, gates, loops or forge dispatch, not repo
    preparation -- so default *only* a bare `None` to "nothing declared,
    deliberately" here. A test that explicitly passes `repo_entry={}` (the
    handful in test_builtins.py that exist to prove the undeclared case
    raises) is untouched: this fixture never sees that call, because `{}` is
    not `None`.

    **Two paths, not one.** `ensure_worktree` was the only one when this fixture
    was written; Task 4a moved `run_setup_command` into `walk.run_once`'s own
    preparation block as `prepare_runtime`. Both shell out with no timeout, so
    keep them saying the same thing from one place.

    The third path -- V1's `kraft.verify_changed_test_scopes` builtin, which
    runs the connected repo's *own* `test_command` -- is deliberately **not**
    defaulted here. There is nothing to key it on: its `repo_entry` is
    `launch.repo_entry or {}`, never `None`, and a test calling
    `dispatch._select_scopes` directly passes a bare `{"test_scopes": [...]}`
    that this fixture cannot tell from a real one without breaking it. It is
    handled where the command comes from instead:
    `tests/support/harness.seed_v1_library` rewrites that builtin out of the
    fixture library, so a V1 chain in a unit test never carries it.
    """
    import kraft.builtins as builtins_mod

    real_ensure_worktree = builtins_mod.ensure_worktree
    real_prepare_runtime = builtins_mod.prepare_runtime

    async def _ensure_worktree_with_default(*args, repo_entry=None, **kwargs):
        if repo_entry is None:
            repo_entry = entry_of(_INERT_REPO_ENTRY)
        return await real_ensure_worktree(*args, repo_entry=repo_entry, **kwargs)

    async def _prepare_runtime_with_default(worktree, repo, repo_entry=None, **kwargs):
        # `prepare_runtime` already no-ops on a bare `None` rather than raising,
        # so this only keeps the two entry points saying the same thing.
        if repo_entry is None:
            repo_entry = entry_of(_INERT_REPO_ENTRY)
        return await real_prepare_runtime(worktree, repo, repo_entry, **kwargs)

    monkeypatch.setattr(builtins_mod, "ensure_worktree", _ensure_worktree_with_default)
    monkeypatch.setattr(builtins_mod, "prepare_runtime", _prepare_runtime_with_default)


@pytest.fixture(autouse=True)
def _no_real_agent_binary(request, monkeypatch):
    """Fail loudly rather than spend tokens: a test that reaches a real agent CLI
    gets an `AssertionError` naming itself and the command.

    At `adapters.subprocess.run_task`, where the argv is final -- not at
    `resolve_invocation`, because the whole class of defect here is a *resolved*
    launch whose command nobody expected. The check is on the command's basename
    only, so every fixture agent (`fixtures/fake-claude.sh`,
    `tests/support/fake_agent.py`, `sys.executable`) passes untouched, and so
    does every non-agent subprocess a node runs (`pytest`, `just`, `git`).

    `real_executor` is the opt-out, the same marker `_no_agent_launch` already
    uses in `tests/test_intake_poller.py` -- and even then this only lets the
    launch through; `KRAFT_E2E` still gates the tests that mean to reach a real
    agent.
    """
    if "real_executor" in request.keywords or _REAL_AGENT_BINARIES & _e2e_binaries(request.node):
        return

    import kraft.adapters.subprocess as sp_mod

    real_run_task = sp_mod.run_task

    async def guarded(*args, cmd=None, **kwargs):
        first = (cmd[0] if isinstance(cmd, list | tuple) and cmd else cmd) or ""
        if Path(str(first)).name in _REAL_AGENT_BINARIES:
            raise AssertionError(
                f"{request.node.nodeid} tried to launch the real agent binary {first!r} "
                f"(argv {list(cmd)!r}). Point it at a fixture agent, or mark the test "
                f"`real_executor` if it genuinely means to."
            )
        return await real_run_task(*args, cmd=cmd, **kwargs)

    monkeypatch.setattr(sp_mod, "run_task", guarded)

    # Reading opencode's usage asks the real binary for the session export
    # (Kraft-ihoen); a test reaching it would read this machine's sessions.
    import kraft.usage as usage_mod

    def no_export(session_id):
        raise AssertionError(
            f"{request.node.nodeid} reached the real `opencode session export "
            f"{session_id}`. Stub `kraft.usage._export_opencode` in the test."
        )

    monkeypatch.setattr(usage_mod, "_export_opencode", no_export)


@pytest.fixture(autouse=True)
def _forward_fake_agent_env_vars_into_worker_env(monkeypatch):
    """A worker's env is now built by `worker_env`'s allowlist rather than
    inherited wholesale from the daemon's own process (Kraft-69atv). Every
    `KRAFT_FAKE_AGENT*`/`KRAFT_FAKE_CLAUDE*` knob `tests/support/fake_agent.py`
    and `fixtures/fake-claude.sh` read relied on that old inheritance to reach
    the fake agent's own process -- forward them here, for the life of the
    test suite, rather than teaching every test that drives a fake agent to
    declare `env_passthrough` for a fixture-only concern the real allowlist
    owes nothing to.
    """
    import kraft.adapters.subprocess as sp_mod
    from kraft.worker.env import worker_env as real_worker_env

    def patched(repo_entry, extra=None):
        names = [k for k in os.environ if k.startswith("KRAFT_FAKE_")]
        entry = repo_entry or entry_of({})
        passthrough = [*entry.env_passthrough, *names]
        return real_worker_env(entry.model_copy(update={"env_passthrough": passthrough}), extra)

    monkeypatch.setattr(sp_mod, "worker_env", patched)


def _e2e_binaries(item) -> set[str]:
    """The real CLIs an `e2e` test names: `@pytest.mark.e2e("bd")`."""
    return {name for mark in item.iter_markers("e2e") for name in mark.args}


def pytest_collection_modifyitems(config, items):
    """An e2e test runs only where every CLI it names is installed, and one
    naming a real agent also needs KRAFT_E2E=1, since it spends tokens. Each
    skip names what is missing. A CLI listed in KRAFT_E2E_REQUIRE (CI's e2e
    job sets `bd`) makes that skip an error, so the job cannot go green having
    run nothing."""
    required = set(filter(None, os.environ.get("KRAFT_E2E_REQUIRE", "").split(",")))
    for item in items:
        if "e2e" not in item.keywords:
            continue
        needs = _e2e_binaries(item)
        if not needs:
            raise pytest.UsageError(f"{item.nodeid}: name its CLIs, e.g. @pytest.mark.e2e('bd')")
        missing = sorted(b for b in needs if not shutil.which(b))
        if missing and required & set(missing):
            raise pytest.UsageError(f"{item.nodeid}: KRAFT_E2E_REQUIRE, but {missing} not on PATH")
        if _REAL_AGENT_BINARIES & needs and os.environ.get("KRAFT_E2E") != "1":
            missing.append("KRAFT_E2E=1 (a real agent spends tokens)")
        if missing:
            item.add_marker(pytest.mark.skip(reason=f"e2e: needs {', '.join(missing)}"))


@pytest.fixture(autouse=True)
def fake_beads(request, monkeypatch):
    """Every test gets an in-memory `kraft.adapters.beads` (Kraft-qmhfc): a
    test that needs a work item to exist should not spawn `bd` (~0.5s a call,
    ~44% of the suite). Two opt-outs keep the real adapter:

    - `@pytest.mark.e2e("bd")`: the real binary, for tests that pin bd's CLI
      contract or assert on bead state bd itself holds.
    - `@pytest.mark.beads_adapter`: tests of the adapter module itself, which
      stub `subprocess.run` or put a stub `bd` on PATH.

    `KRAFT_TEST_REAL_BD=1` turns the fake off suite-wide: the check that the
    fake is not what makes a test pass. Returns the fake (None when off), so
    a test can read or seed its state.
    """
    if "bd" in _e2e_binaries(request.node) or os.environ.get("KRAFT_TEST_REAL_BD") == "1":
        yield None
        return
    # Past here nothing needs a real workspace, so no test pays for `bd init`.
    monkeypatch.setattr(harness, "REAL_BD", False)
    if "beads_adapter" in request.keywords:
        yield None
        return
    import kraft.adapters.beads as beads_mod

    fake = FakeBeads()
    for name in ("intake", "complete", "search", "ready", "blocked_by"):
        monkeypatch.setattr(beads_mod, name, getattr(fake, name))

    # Refused here and re-raised at teardown: Kraft swallows a failed intake
    # or close into a warning, so the raise alone could go unseen.
    reached: list = []

    def _refuse(argv, *a, **k):
        reached.append(argv)
        raise AssertionError(f"real bd reached with the fake installed: {argv!r}")

    monkeypatch.setattr(beads_mod, "subprocess", type("_NoBd", (), {"run": staticmethod(_refuse)}))
    yield fake
    assert not reached, (
        f"{request.node.nodeid} reached the real `bd` through kraft.adapters.beads "
        f"({reached!r}) with the fake installed: fake the new function in "
        f"tests/support/fake_beads.py, or mark the test e2e('bd')."
    )


@pytest.fixture
def bd(request, fake_beads) -> Bd:
    """Bead state to assert on -- `bd.status(id, cwd=...)`, `bd.ids(cwd=...)`,
    `bd.block(id, blocker, cwd=...)`, `bd.init(path)` -- answered by the fake
    in the unit tier and by the real CLI under `e2e("bd")`, so one test body
    serves both (`support.fake_beads.ON_FAKE_AND_REAL_BD`). A `[bd]` case that
    lost its `e2e("bd")` mark would quietly run against the fake: refused."""
    if getattr(request, "param", None) == "bd" and fake_beads is not None:
        pytest.fail(f"{request.node.nodeid}: a [bd] case must be marked e2e('bd')")
    return Bd(fake_beads)


@pytest.fixture(autouse=True)
def _contain_hardened_git_env():
    """`sandbox.harden_host_git_env` pins `GIT_CONFIG_*` on the server's own
    environment on purpose -- that is how every git Kraft spawns inherits it.
    Under pytest the "server process" is the test process, so any test that
    starts a lifespan would otherwise leave those vars set for every test
    after it, silently disabling hooks in suites that assert on them."""
    before = {k: v for k, v in os.environ.items() if k.startswith("GIT_CONFIG_")}
    yield
    for key in [k for k in os.environ if k.startswith("GIT_CONFIG_")]:
        del os.environ[key]
    os.environ.update(before)


@pytest.fixture(autouse=True)
def _isolated_kraft_home(tmp_path, monkeypatch):
    """No test may reach the operator's real `~/.kraft`.

    `kraft_home()` falls back to `~/.kraft` (paths.py:16). Only KRAFT_RUN_DIR and
    KRAFT_TEMPLATES_DIR were ever overridden, so `default_skills_dir()` — and any
    check that reads its env var lazily — resolved against the real home, which
    exists on a developer machine and not in a CI container. Autouse rather than
    part of `app`, so a test cannot reach the home by not opting in.
    """
    monkeypatch.setenv("KRAFT_HOME", str(tmp_path / "kraft-home"))
    # Nor a real `kraft admin start` on the default port 8765: a test that
    # doesn't opt into the `app` fixture's ASGI transport falls through to a
    # real HTTP call in `client.base_url()`, and a developer machine running
    # `kraft` for real answers it — turning an expected ConnectError into a
    # live 401. Pin an ephemeral port nothing is listening on yet instead.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        monkeypatch.setenv("KRAFT_PORT", str(probe.getsockname()[1]))
    # No test may reach the release feed either. `doctor`'s version check and the
    # notice at boot both call out to gitlab.com, which would make this suite
    # depend on that host being up and cost every offline run a timeout. The
    # tests that exercise the check set their own stubs and unset this.
    monkeypatch.setenv("KRAFT_NO_UPDATE_CHECK", "1")
    # Nor the operator's real `~/.beads` (Kraft-t5g): bd's fallback when it finds
    # no `.beads/` walking up from cwd is a hardcoded `~/.beads`, not KRAFT_HOME.
    # Every throwaway repo the suite builds sets its own local git user config
    # (make_repo, _bd_template), so bd's `--actor` default never needs the real
    # `$HOME`'s global one.
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    # `_serve()` dup2s real fds 1/2 to server.log unless told not to -- fine for
    # a real process, but it would stomp pytest's own fd-level capture (and
    # every test after it, in-process) if a `_serve()` call reached that far.
    # Tests that specifically exercise the redirect delenv this and wrap the
    # call in `capfd.disabled()`.
    monkeypatch.setenv("KRAFT_LOG_REDIRECTED", "1")


@pytest.fixture
def run_dirs(tmp_path) -> RunDirs:
    """`tmp_path/run` as a `RunDirs`, directories created: what the executor
    and every `store` reader take alongside `database`."""
    return RunDirs(tmp_path / "run").ensure()


@pytest.fixture
async def database(run_dirs):
    """An open, migrated `db.Database` at `run_dirs.db`, closed after the test.

    Replaces the `async def scenario(): open / try / finally close` +
    `asyncio.run(scenario())` block. Write the test as `async def` and take
    the fixture; anyio runs the fixture and the test on one event loop, which
    the `Database` writer task needs (it is bound to the loop that opened it):

        async def test_x(database):
            await mk_item(database)
            await database.write(lambda c: store.create_session(c, ...))
            assert database.read(lambda c: ...) == ...

    A test that also needs the logs/results/worktrees dirs takes `run_dirs`:
    it is the same `RunDirs` this database lives in.
    """
    database = await db.Database.open(run_dirs.db)
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture
def item_on(request, database, run_dirs):
    """`await item_on(chain, node, ...)`: a V1 work item in `database`, on
    `chain`, standing at `node` -- `support.harness.make_item` with the
    database, run dirs and (unless `repo=` is given) the `repo` fixture
    filled in. Returns a `support.harness.Item`:

        async def test_x(item_on, run_dirs, database):
            it = await item_on(v1_named_chain(run_dirs.base / "t"), "implementation")
            await it.session("s1", "implementation.main.implement", "done")
            ...
            assert it.status() == "completed"
            assert [e["payload"] for e in it.events("node_completed")] == [...]
    """

    async def factory(chain, node=None, *, repo=None, **kwargs):
        repo = repo if repo is not None else request.getfixturevalue("repo")
        return await harness.make_item(database, run_dirs, chain, node, repo=repo, **kwargs)

    return factory


@pytest.fixture
def templates_dir(tmp_path) -> Path:
    """`KRAFT_TEMPLATES_DIR` for the `client` fixture: `fake_templates_dir` with
    every agent on `fixtures/fake-claude.sh`. A file that needs another shape
    (an edited library or chain) overrides this fixture and `client` picks its
    version up."""
    return fake_templates_dir(tmp_path, str(_FAKE_CLAUDE))


@pytest.fixture
def repo(tmp_path) -> Path:
    """A committed copy of `tests/support/sample_repo` (`make_repo`)."""
    return make_repo(tmp_path)


@pytest.fixture
def client(request, tmp_path, monkeypatch, templates_dir):
    """A started `TestClient` on the Kraft app (lifespan entered), hermetic
    under `tmp_path`: its own run dir, bd workspace and `templates_dir`.
    Replaces `with _client(tmp_path, monkeypatch) as client:`.

        def test_x(client, repo):
            wid = client.post("/api/work-items", json={"repo": str(repo), ...}).json()["id"]

    Options (`support.api._client`'s keywords) go on a marker, per test or per
    module (`pytestmark = pytest.mark.api_client(...)`); a test's own mark
    adds to its module's:

        @pytest.mark.api_client(peer=("10.0.0.2", 1), env={"KRAFT_INDEX_REPOS": "..."})
        @pytest.mark.api_client(default_setup=False)   # a test about repo config
        @pytest.mark.api_client(host="0.0.0.0")        # the locked-down posture
        @pytest.mark.api_client(bd_workspace=False)    # no KRAFT_BD_CWD
        @pytest.mark.api_client(edit_templates=fn)     # fn(templates_dir) first

    `edit_templates` does not follow the "closest wins" rule above: a
    module-level `pytestmark` editor and a test's own editor both run
    (module's first, so a test's edits land on top of its module's, not the
    other way round). Nothing else on the marker composes this way — every
    other key is a plain value, so the closest one simply replaces it.

    Env a lifespan reads at startup that depends on another fixture goes in a
    module-level autouse fixture: autouse fixtures are set up first. This is
    the one `client` fixture: a file overrides `templates_dir`, not `client`.

    Monkeypatch what the app reads at request time inside the test; patch
    anything the lifespan reads at startup in a fixture the test lists before
    `client`. The run dir is `tmp_path / "run"` (`support.api._set_status`
    and friends find it through `KRAFT_RUN_DIR`).
    """
    # Every `api_client` mark applies, the closest winning key by key: a
    # module's `pytestmark` sets the file's defaults, a test's own mark adds to
    # them. `edit_templates` is the one exception: a plain `|=` would let a
    # test's own mark silently replace its module's instead of adding to it,
    # so every marker's `edit_templates` is collected and called, module first
    # then test, instead of only the closest one winning.
    options = {}
    edit_templates_fns = []
    for mark in reversed(list(request.node.iter_markers("api_client"))):
        kwargs = dict(mark.kwargs)
        if "edit_templates" in kwargs:
            edit_templates_fns.append(kwargs.pop("edit_templates"))
        options |= kwargs
    # A test's own templates (a policy, a chain, a broken file), written into
    # `templates_dir` before the lifespan reads it.
    for edit_templates in edit_templates_fns:
        edit_templates(templates_dir)
    # A test that also lists `dist` needs KRAFT_FRONTEND_DIST set before this
    # fixture's lifespan starts. Pulling it here — rather than relying on
    # pytest's argument-order setup — means listing `dist` after `client` in a
    # test's signature still works instead of silently building a client with
    # no SPA shell.
    if "dist" in request.fixturenames:
        request.getfixturevalue("dist")
    with api_support._client(
        tmp_path, monkeypatch, templates_dir=templates_dir, **options
    ) as test_client:
        yield test_client


@pytest.fixture
def app(tmp_path, monkeypatch):
    """The app wired to client.transport.http(), with its lifespan entered per call.

    cli.main() runs asyncio.run() itself, so — unlike test_client_read.py, where
    one coroutine owns the loop — the lifespan cannot stay open across the call.
    Each handler opens and closes its own loop, so each gets its own lifespan.
    """
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("KRAFT_BD_CWD", str(isolated_bd(tmp_path)))
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(fake_templates_dir(tmp_path, str(_FAKE_CLAUDE))))
    monkeypatch.setenv(
        "KRAFT_FRONTEND_DIST", os.environ.get("KRAFT_FRONTEND_DIST") or str(tmp_path / "no-dist")
    )
    monkeypatch.delenv("KRAFT_WORK_ITEM_ID", raising=False)
    monkeypatch.delenv("NO_COLOR", raising=False)
    import kraft.api as api

    class Lifespan(httpx.AsyncClient):
        """An AsyncClient that enters the app lifespan for the life of the client."""

        async def __aenter__(self):
            self._ctx = api.app.router.lifespan_context(api.app)
            await self._ctx.__aenter__()
            return await super().__aenter__()

        async def __aexit__(self, *exc):
            await super().__aexit__(*exc)
            await self._ctx.__aexit__(*exc)

    monkeypatch.setattr(
        kraft_client.transport,
        "http",
        lambda: Lifespan(transport=httpx.ASGITransport(app=api.app), base_url="http://kraft"),
    )
    return api


@pytest.fixture
async def stub_app(database, run_dirs):
    """`stub_app(**state)`: a stand-in for the FastAPI app a poller's `tick(app)`
    reads, with `app.state.db`/`.run_dirs`/`.tasks` from the `database` and
    `run_dirs` fixtures plus whatever `state` names (policy, registry, ...).
    Every task a tick spawned into `app.state.tasks` is awaited, not cancelled,
    before the database closes.

        async def test_x(tmp_path, stub_app):
            app = stub_app(policy=policy.Policy(...), templates_dir=tmp_path / "t")
            assert await archive.tick(app) == []
    """
    import asyncio
    from types import SimpleNamespace

    apps = []

    def make(**state):
        app = SimpleNamespace(
            state=SimpleNamespace(db=database, run_dirs=run_dirs, tasks={}, **state)
        )
        apps.append(app)
        return app

    yield make
    for app in apps:
        await asyncio.gather(*app.state.tasks.values(), return_exceptions=True)


@pytest.fixture
def wait_clock(monkeypatch):
    """The clock an external wait reads (`kraft.waits._now`), frozen at
    `wait_clock.start` until a test moves it -- so a wait's backoff and
    timeout are asserted to the second instead of raced against:

        async def test_x(wait_clock, ...):
            ...                                  # a wait observes at t=0
            wait_clock.advance(30)               # 30s later
            assert row["retry_at"] == wait_clock.at(60)
    """
    from datetime import UTC, datetime, timedelta

    from kraft import waits

    class Clock:
        start = datetime(2026, 1, 1, tzinfo=UTC)

        def __init__(self):
            self.now = self.start

        def at(self, seconds: float) -> str:
            return (self.start + timedelta(seconds=seconds)).isoformat()

        def advance(self, seconds: float) -> None:
            self.now += timedelta(seconds=seconds)

    clock = Clock()
    monkeypatch.setattr(waits, "_now", lambda: clock.now.isoformat())
    return clock
