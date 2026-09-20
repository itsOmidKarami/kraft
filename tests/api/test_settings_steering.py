"""Steering files: list, round trip, injection budget, and the delete/save
races around the intake poller."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import httpx
import pytest
import yaml
from support.api_settings import _client
from support.harness import fake_templates_dir, make_repo

from kraft import config
from kraft import intake as intake_mod
from kraft import steering as steering_mod

_FAKE_AGENT = Path(__file__).resolve().parents[1] / "support" / "fake_agent.py"


@pytest.fixture
def templates_dir(tmp_path):
    return fake_templates_dir(tmp_path, "claude")


@pytest.fixture
def client(tmp_path, monkeypatch, templates_dir):
    with _client(tmp_path, monkeypatch, templates_dir) as c:
        yield c


def _steering_dir(templates_dir):
    d = templates_dir / "steering"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _files_excluding_fixture(body: dict) -> list[dict]:
    """`fake_templates_dir` seeds `never-signal-processes-you-didnt-start.md`
    itself -- `on.chain.review_ready`'s shipped binding names it, so a config
    load would 422 without it. It is not this test's concern, so drop it
    before asserting on what the test itself wrote."""
    return [f for f in body["files"] if f["name"] != "never-signal-processes-you-didnt-start"]


def test_steering_list_reports_sizes_against_the_injection_budget(client, templates_dir):
    (_steering_dir(templates_dir) / "house-style.md").write_text("prefer stdlib\n")
    body = client.get("/api/steering").json()
    assert body["max_bytes"] == steering_mod.MAX_BYTES
    assert _files_excluding_fixture(body) == [
        {"name": "house-style", "bytes": len(b"prefer stdlib\n")}
    ]


def test_steering_round_trips_a_body(client, templates_dir):
    assert (
        client.put("/api/steering/house-style", json={"body": "prefer stdlib\n"}).status_code == 200
    )
    assert client.get("/api/steering/house-style").json()["body"] == "prefer stdlib\n"
    assert (templates_dir / "steering" / "house-style.md").read_text() == "prefer stdlib\n"


def test_steering_rejects_a_name_that_is_not_a_bare_file_name(client):
    """Kraft never reads a steering file from outside its own templates
    directory, so the editor cannot be the way one gets written there."""
    # a backslash and a leading dot both survive URL routing as one path
    # segment, unlike "../", which the router normalises away before we see it
    assert client.put("/api/steering/..\\escape", json={"body": "x"}).status_code == 400
    assert client.put("/api/steering/.hidden", json={"body": "x"}).status_code == 400
    assert client.get("/api/steering/.hidden").status_code == 400


def test_steering_get_404s_on_a_file_that_is_not_there(client):
    assert client.get("/api/steering/nope").status_code == 404


def test_a_body_over_the_injection_budget_is_refused_and_rolled_back(client, templates_dir):
    """Names resolve at config-load time, so an oversized body breaks a launch
    nowhere near this screen — the save has to fail here instead."""
    (_steering_dir(templates_dir) / "big.md").write_text("small\n")
    registry = yaml.safe_load((templates_dir / "registry.yaml").read_text())
    registry["hooks"]["on.implementation.start"]["steering"] = ["big"]
    (templates_dir / "registry.yaml").write_text(yaml.safe_dump(registry))
    client.put("/api/registry", json={"hooks": registry["hooks"]})

    resp = client.put("/api/steering/big", json={"body": "x" * (steering_mod.MAX_BYTES + 1)})
    assert resp.status_code == 422
    # the file on disk is the one that still loads, not the one that was refused
    assert (templates_dir / "steering" / "big.md").read_text() == "small\n"


def test_deleting_a_steering_file_a_hook_still_names_is_refused(client, templates_dir):
    (_steering_dir(templates_dir) / "house-style.md").write_text("prefer stdlib\n")
    registry = yaml.safe_load((templates_dir / "registry.yaml").read_text())
    registry["hooks"]["on.implementation.start"]["steering"] = ["house-style"]
    (templates_dir / "registry.yaml").write_text(yaml.safe_dump(registry))
    client.put("/api/registry", json={"hooks": registry["hooks"]})

    assert client.delete("/api/steering/house-style").status_code == 422
    assert (templates_dir / "steering" / "house-style.md").is_file()


def test_deleting_a_steering_file_nothing_names_succeeds(client, templates_dir):
    (_steering_dir(templates_dir) / "orphan.md").write_text("unused\n")
    assert client.delete("/api/steering/orphan").status_code == 200
    assert not (templates_dir / "steering" / "orphan.md").exists()
    assert _files_excluding_fixture(client.get("/api/steering").json()) == []


def test_two_overlapping_intake_saves_leave_exactly_one_live_poller(client):
    """The swap awaits the old task's cancellation, so without a lock both
    savers read the same old task, both start a poller, and only the last
    assignment is reachable. The other ticks on past shutdown -- lifespan
    cancels `app.state.intake_task` and nothing else -- and two pollers reading
    `_known_beads` before either inserts can double-start the same bead.
    """
    app = client.app
    body = {
        "enabled": True,
        "interval_s": 60,
        "max_concurrent": 1,
        "priority_ceiling": 2,
        "repos": [],
    }
    started: list[asyncio.Task] = []

    async def never_returning_poller(_app):
        started.append(asyncio.current_task())
        # cancellation is the only way out, which is exactly what the swap owes
        # every poller it retires
        await asyncio.Event().wait()

    async def scenario():
        app.state.intake = dict(config.INTAKE_DEFAULT)
        app.state.intake_task = None
        app.state.intake_lock = asyncio.Lock()
        monkey = intake_mod.poller
        intake_mod.poller = never_returning_poller
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://kraft") as ac:
                # A poller has to already be live, or neither save reaches the
                # `await` that opens the window and the race cannot show.
                assert (await ac.put("/api/intake", json=body)).status_code == 200
                assert app.state.intake_task is not None
                a, b = await asyncio.gather(
                    ac.put("/api/intake", json=body), ac.put("/api/intake", json=body)
                )
            assert (a.status_code, b.status_code) == (200, 200)
            # let any cancellation delivered above actually land
            await asyncio.sleep(0)
            live = app.state.intake_task
            orphans = [t for t in started if t is not live and not t.done()]
            assert orphans == [], f"{len(orphans)} poller(s) left running unreachably"
            assert live is not None and not live.done()
            live.cancel()
            await asyncio.gather(live, return_exceptions=True)
        finally:
            intake_mod.poller = monkey
            # These tasks belong to this loop, not the fixture's; leaving one on
            # app.state would have lifespan shutdown gather it from the wrong one.
            app.state.intake_task = None

    asyncio.run(scenario())


def test_an_unexpected_validation_error_leaves_the_steering_file_untouched(
    client, templates_dir, monkeypatch
):
    """Validating by writing the real file first and undoing it on failure only
    undoes the failures it anticipated. Validation has to happen against a
    scratch copy, so the real directory is never briefly wrong — a dispatch
    reads these files straight off disk.
    """
    (_steering_dir(templates_dir) / "house-style.md").write_text("prefer stdlib\n")

    def boom(*a, **k):
        raise RuntimeError("something nobody predicted")

    from kraft.api.routes import settings as settings_mod

    monkeypatch.setattr(settings_mod, "load_registry", boom)
    with pytest.raises(RuntimeError):
        client.put("/api/steering/house-style", json={"body": "REPLACED\n"})
    assert (templates_dir / "steering" / "house-style.md").read_text() == "prefer stdlib\n"


def test_a_refused_steering_save_never_writes_the_real_file(client, templates_dir):
    """Not "writes it and puts it back" — never writes it. Asserted by watching
    the path itself rather than its final contents, which a rollback also
    satisfies."""
    steering = _steering_dir(templates_dir)
    (steering / "big.md").write_text("small\n")
    registry = yaml.safe_load((templates_dir / "registry.yaml").read_text())
    registry["hooks"]["on.implementation.start"]["steering"] = ["big"]
    (templates_dir / "registry.yaml").write_text(yaml.safe_dump(registry))
    client.put("/api/registry", json={"hooks": registry["hooks"]})

    target = steering / "big.md"
    before = target.stat().st_mtime_ns
    assert (
        client.put(
            "/api/steering/big", json={"body": "x" * (steering_mod.MAX_BYTES + 1)}
        ).status_code
        == 422
    )
    assert target.read_text() == "small\n"
    assert target.stat().st_mtime_ns == before, "the real file was written and then put back"


def test_get_intake_reads_the_file_not_the_cached_state(client, templates_dir):
    """`intake.yaml` was hand-edited until this screen existed, so the screen
    has to show what is on disk. Returning `app.state` hides an edit made since
    boot, and the next save silently overwrites it."""
    (templates_dir / "intake.yaml").write_text(
        "enabled: false\ninterval_s: 900\nmax_concurrent: 4\npriority_ceiling: 1\nrepos: []\n"
    )
    body = client.get("/api/intake").json()
    assert body["interval_s"] == 900
    assert body["max_concurrent"] == 4


def test_saving_steering_reloads_nothing(client, templates_dir, monkeypatch):
    """No `app.state` holds steering bodies — they are read from disk at
    dispatch — so a reload here is dead code that tells the next reader state
    caches them."""
    called = []
    from kraft.api import deps

    monkeypatch.setattr(deps, "_reload_templates", lambda st: called.append(True))
    assert client.put("/api/steering/fresh", json={"body": "hi\n"}).status_code == 200
    assert called == []


def test_the_steering_validation_runs_off_the_event_loop(client, templates_dir, monkeypatch):
    """`_check_steering_change` copies every `*.md` in the steering directory
    into a scratch dir and runs two config loaders over the copy. That is
    directory-sized blocking I/O in an `async def`, and the directory's size is
    the operator's to grow.

    Asserted with `asyncio.get_running_loop()` rather than by comparing against
    `threading.main_thread()`: `TestClient` runs the event loop in an anyio
    portal *worker* thread, so "not the main thread" is true even when the code
    does run on the loop, and that assertion would pass without the fix. A
    thread that is not running the loop has no running loop, which is exact.
    """
    (_steering_dir(templates_dir) / "house-style.md").write_text("prefer stdlib\n")
    on_loop, off_loop = [], []
    from kraft.api.routes import settings as settings_mod

    real = settings_mod._check_steering_change

    def record(st, name, body):
        try:
            on_loop.append(asyncio.get_running_loop())
        except RuntimeError:
            off_loop.append(threading.current_thread().name)
        return real(st, name, body)

    monkeypatch.setattr(settings_mod, "_check_steering_change", record)

    assert (
        client.put("/api/steering/house-style", json={"body": "prefer native\n"}).status_code == 200
    )
    assert client.delete("/api/steering/house-style").status_code == 200

    assert on_loop == []
    assert len(off_loop) == 2


def test_deleting_steering_reloads_nothing(client, templates_dir, monkeypatch):
    """The DELETE-side mirror of `test_saving_steering_reloads_nothing`, and a
    deliberate change-detector on an implementation detail.

    That is the point: no `app.state` holds steering bodies -- dispatch reads
    them straight off disk, and `resolve_invocation` re-checks the assembled
    budget at every launch -- so a `_reload_templates` call added here would be
    dead code whose only effect is to tell the next reader that state caches
    them. The reason is non-obvious enough that someone adds it back.
    """
    (_steering_dir(templates_dir) / "orphan.md").write_text("unused\n")
    called = []
    from kraft.api import deps

    monkeypatch.setattr(deps, "_reload_templates", lambda st: called.append(True))

    assert client.delete("/api/steering/orphan").status_code == 200
    assert called == []


def test_a_stray_unreadable_entry_does_not_break_an_unrelated_save(client, templates_dir):
    """`$KRAFT_HOME/templates/steering/` is hand-editable, so it can hold things
    that are not readable files. Copying the directory to validate against must
    not turn one of those into a 500 on every save of every other file — the old
    write-then-rollback never touched entries nothing referenced.
    """
    steering = _steering_dir(templates_dir)
    (steering / "dangling.md").symlink_to(steering / "nothing-here.md")
    (steering / "adirectory.md").mkdir()

    assert (
        client.put("/api/steering/house-style", json={"body": "prefer stdlib\n"}).status_code == 200
    )
    assert (steering / "house-style.md").read_text() == "prefer stdlib\n"


def test_a_delete_referenced_only_by_repos_yaml_is_refused(tmp_path, client, templates_dir):
    """The registry is one of two files that name steering; `repos.yaml` is the
    other, and only the registry leg was covered."""
    (_steering_dir(templates_dir) / "house-style.md").write_text("prefer stdlib\n")
    repo = make_repo(tmp_path)
    assert client.post("/api/repos", json={"path": str(repo)}).status_code == 201
    repos = yaml.safe_load((templates_dir / "repos.yaml").read_text())
    repos["repos"][0]["steering"] = ["house-style"]
    config.write_yaml(templates_dir / "repos.yaml", repos)

    assert client.delete("/api/steering/house-style").status_code == 422
    assert (templates_dir / "steering" / "house-style.md").is_file()

    # and once nothing names it, the same delete goes through
    repos["repos"][0].pop("steering")
    config.write_yaml(templates_dir / "repos.yaml", repos)
    assert client.delete("/api/steering/house-style").status_code == 200
    assert not (templates_dir / "steering" / "house-style.md").exists()
