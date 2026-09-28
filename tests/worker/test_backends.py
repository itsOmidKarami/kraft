import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from kraft import store
from kraft.adapters import subprocess as sp
from kraft.worker import backends, reattach
from kraft.worker.backends import docker as docker_backend
from kraft.worker.sandbox import SandboxNotReady

_CHAIN = """
- id: implementation
  kind: exec
  tasks: [{id: implement, kind: agent, harness: fake, prompt: p}]
"""
#: A definitely-dead pid, with a start time.
DEAD = (2_000_000_000, 123.0)


class Remote:
    """A backend sharing no filesystem with Kraft: the command writes its
    result somewhere of its own, and only `collect` brings it back."""

    kind = "remote"

    def __init__(self, tmp_path: Path):
        self.outbox = tmp_path / "remote-outbox"
        self.outbox.mkdir()
        self.closed: list[str] = []
        #: What `prepare` hands back (or, a str, the problem it raises), and
        #: the `ca_bundle` each `wrap` was given.
        self.prepared: Path | str | None = None
        self.wrapped_with: list = []

    def home(self, run_dirs, work_item_id):
        return run_dirs.base / "remote-home" / work_item_id

    async def probe(self, sandbox, executable, env):
        return None

    async def prepare(self, sandbox):
        if isinstance(self.prepared, str):
            raise SandboxNotReady(self.prepared)
        return self.prepared

    def code_in(self, run_base, cwd, branch, **kw):
        return None

    def wrap(self, cmd, cwd, sandbox, results_dir, env=None, *, session_id=None, **kw):
        self.wrapped_with.append(kw.get("ca_bundle"))
        return ["env", f"KRAFT_RESULT_PATH={self.outbox / f'{session_id}.json'}", *cmd]

    def launch_failed(self, cidfile):
        return False

    def code_out(self, refs, session_id):
        return None

    def code_out_item(self, run_base, work_item_id):
        return []

    async def collect(self, session_id, result_path):
        sent = self.outbox / f"{session_id}.json"
        if sent.exists():
            shutil.copyfile(sent, result_path)

    async def close(self, session_id):
        self.closed.append(session_id)

    async def sweep(self, keep_sessions):
        return []

    def release(self, run_dirs, worktree, work_item_id):
        pass

    async def health(self, sandbox):
        return True, "remote"


@pytest.fixture
def remote(tmp_path, monkeypatch):
    backend = Remote(tmp_path)
    monkeypatch.setitem(backends._BACKENDS, backend.kind, backend)
    return backend


@pytest.fixture
def docker_closed(monkeypatch):
    closed = []

    async def teardown(session_id):
        closed.append(session_id)

    monkeypatch.setattr(docker_backend, "teardown", teardown)
    return closed


@pytest.mark.parametrize(
    ("kind", "asked"),
    [("docker", ["docker"]), (None, ["docker", "remote"]), ("retired", ["docker", "remote"])],
    ids=["recorded", "none-recorded", "no-longer-registered"],
)
def test_a_session_is_closed_by_the_backend_it_recorded_else_by_every_one(remote, kind, asked):
    assert [b.kind for b in backends.for_session(kind)] == asked


async def _run_on_remote(database, run_dirs, tmp_path) -> str:
    await database.write(
        lambda c: store.create_work_item(
            c,
            id="w1",
            bead_id="B",
            title="t",
            repo="/r",
            chain_template="quick-task",
            chain_definition="{}",
        )
    )
    return await sp.run_task(
        database,
        run_dirs,
        session_id="s1",
        work_item_id="w1",
        cmd=["sh", "-c", """printf '{"status": "done_with_concerns"}' > "$KRAFT_RESULT_PATH" """],
        node_id="verify",
        hook_point="on.test.run",
        cwd=tmp_path,
        sandbox={"kind": "remote"},
    )


async def test_a_result_reaches_kraft_only_through_collect(database, run_dirs, tmp_path, remote):
    """The seam's one rule: nothing reads a sandboxed session's result before
    its backend has collected it, so a backend with no shared filesystem
    works unchanged."""
    status = await _run_on_remote(database, run_dirs, tmp_path)

    assert status == "done_with_concerns"
    row = database.read(
        lambda c: c.execute("SELECT sandbox FROM worker_sessions WHERE id = 's1'").fetchone()
    )
    assert (row["sandbox"], remote.closed) == ("remote", ["s1"])


async def test_reattach_closes_a_dead_session_in_the_backend_it_ran_in(
    item_on, database, run_dirs, remote, docker_closed
):
    item = await item_on(_CHAIN, "implementation")
    await item.session("s1", "implementation.main.implement", running=DEAD, sandbox="remote")

    await reattach.reattach(database, run_dirs)

    assert (remote.closed, docker_closed) == (["s1"], [])


async def test_reattach_closes_a_session_that_recorded_no_backend_everywhere(
    item_on, database, run_dirs, remote, docker_closed
):
    """A row written before the column existed may have run in docker."""
    item = await item_on(_CHAIN, "implementation")
    await item.session("s1", "implementation.main.implement", running=DEAD)

    await reattach.reattach(database, run_dirs)

    assert (remote.closed, docker_closed) == (["s1"], ["s1"])


async def test_a_collect_that_fails_still_publishes_the_sessions_commits(
    database, run_dirs, tmp_path, remote, monkeypatch
):
    """A backend that copies results out can fail to; the branch the session
    committed must reach the repository all the same."""
    published = []

    async def fail(session_id, result_path):
        raise OSError("copy-out failed")

    monkeypatch.setattr(remote, "code_in", lambda *a, **kw: SimpleNamespace(carried=None))
    monkeypatch.setattr(remote, "code_out", lambda refs, sid: published.append(sid))
    monkeypatch.setattr(remote, "collect", fail)
    await database.write(
        lambda c: store.create_work_item(
            c,
            id="w1",
            bead_id="B",
            title="t",
            repo="/r",
            chain_template="quick-task",
            chain_definition="{}",
        )
    )

    with pytest.raises(OSError, match="copy-out failed"):
        await sp.run_task(
            database,
            run_dirs,
            session_id="s1",
            work_item_id="w1",
            cmd=["true"],
            node_id="verify",
            hook_point="on.test.run",
            cwd=tmp_path,
            sandbox={"kind": "remote"},
        )

    assert (published, remote.closed) == (["s1"], ["s1"])


@pytest.mark.parametrize(
    "prepared", [Path("/run/sandbox-ca/abc.pem"), None], ids=["bundle", "no-extra-ca"]
)
async def test_a_session_is_wrapped_with_exactly_what_prepare_built(
    database, run_dirs, tmp_path, remote, prepared
):
    """Handed over, not remembered: no CA means no `ca_bundle` at all."""
    remote.prepared = prepared
    assert await _run_on_remote(database, run_dirs, tmp_path) == "done_with_concerns"
    assert remote.wrapped_with == [prepared]


async def test_a_session_whose_backend_is_not_ready_stops_before_it_launches(
    database, run_dirs, tmp_path, remote
):
    remote.prepared = "image 'x' roots could not be read"
    assert await _run_on_remote(database, run_dirs, tmp_path) == "config_error"
    assert remote.wrapped_with == []
    log = (run_dirs.logs / "s1.log").read_text()
    assert "roots could not be read" in log
