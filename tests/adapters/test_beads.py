import asyncio
import json
import subprocess

import pytest
from support.harness import isolated_bd

from kraft.adapters import beads


@pytest.mark.e2e("bd")
def test_intake_then_complete_roundtrip(tmp_path):
    repo = isolated_bd(tmp_path)

    def _status(bead_id: str) -> str:
        out = subprocess.run(
            ["bd", "show", bead_id, "--json"], cwd=repo, capture_output=True, text=True, check=True
        ).stdout
        return json.loads(out)[0]["status"]

    async def scenario():
        bead_id = await beads.intake("wire the thing", cwd=str(repo))
        assert bead_id
        assert _status(bead_id) in ("open", "in_progress")
        await beads.complete(bead_id, cwd=str(repo))
        assert _status(bead_id) == "closed"

    asyncio.run(scenario())


@pytest.mark.e2e("bd")
def test_intake_writes_the_description_onto_the_bead(tmp_path):
    repo = isolated_bd(tmp_path)

    def _description(bead_id: str) -> str:
        out = subprocess.run(
            ["bd", "show", bead_id, "--json"], cwd=repo, capture_output=True, text=True, check=True
        ).stdout
        return json.loads(out)[0]["description"]

    async def scenario():
        bead_id = await beads.intake(
            "wire the thing", description="the brief, at length", cwd=str(repo)
        )
        assert _description(bead_id) == "the brief, at length"

        plain = await beads.intake("wire the other thing", cwd=str(repo))
        assert _description(plain) == "Created by the Kraft orchestrator."

        empty = await beads.intake("wire a third thing", description="", cwd=str(repo))
        assert _description(empty) == "Created by the Kraft orchestrator."

    asyncio.run(scenario())


@pytest.mark.e2e("bd")
def test_ready_projects_the_description(tmp_path):
    """Auto-intake reads a bead the human already wrote. Dropping its description
    is dropping the brief in the one case where nobody is present to notice."""
    repo = isolated_bd(tmp_path)

    async def scenario():
        await beads.intake("caulk the transom", description="it leaks at the seam", cwd=str(repo))
        rows = await beads.ready(cwd=str(repo))
        assert rows
        row = next(r for r in rows if r["title"] == "caulk the transom")
        assert row["description"] == "it leaks at the seam"

    asyncio.run(scenario())


@pytest.fixture
def bd_answers(monkeypatch):
    """`bd_answers(stdout, returncode=0, stderr="")` makes every `bd` the
    adapter runs answer that, and returns the `(argv, cwd)` of each call, so a
    test pins the command as well as how its output is read.

        calls = bd_answers('[{"id": "X-1", "title": "t"}]')
        await beads.ready(cwd="/r")
        assert calls == [(["bd", "ready", "--json"], "/r")]
    """

    def answer(stdout: str, returncode: int = 0, stderr: str = "") -> list:
        calls: list = []

        def run(argv, *_a, cwd=None, check=False, **_k):
            calls.append((list(argv), cwd))
            if check and returncode:
                raise subprocess.CalledProcessError(returncode, argv, stdout, stderr)
            return subprocess.CompletedProcess(argv, returncode, stdout, stderr)

        monkeypatch.setattr(beads.subprocess, "run", run)
        return calls

    return answer


@pytest.mark.beads_adapter
async def test_ready_tolerates_a_bead_with_no_description(bd_answers):
    """`ready` is best-effort by contract; a row without the key must not raise."""
    calls = bd_answers('[{"id": "X-1", "title": "t", "priority": 1}]')
    rows = await beads.ready(cwd="/tmp")
    assert rows == [
        {"id": "X-1", "title": "t", "priority": 1, "issue_type": None, "description": None}
    ]
    assert calls == [(["bd", "ready", "--json"], "/tmp")]


@pytest.mark.beads_adapter
async def test_ready_drops_a_row_without_an_id_or_a_title(bd_answers):
    """The poller files a work item per row: one with no id cannot be tracked,
    and one with no title has nothing to file it under."""
    rows = [
        {"id": "X-1", "title": "kept"},
        {"title": "no id"},
        {"id": "X-2"},
        {"id": "X-3", "title": ""},
    ]
    bd_answers(json.dumps([*rows, "not a row"]))
    assert [r["id"] for r in await beads.ready(cwd="/tmp")] == ["X-1"]


@pytest.mark.beads_adapter
async def test_intake_reads_the_id_past_an_advisory_line(bd_answers):
    """bd may print a notice (an upgrade, a migration) before the issue
    object; the id is still the object's."""
    calls = bd_answers('Note: a newer bd is available\n{"id": "X-9", "title": "t"}\n')
    assert await beads.intake("wire it", cwd="/r") == "X-9"
    argv = [
        "bd",
        "create",
        "--json",
        "--title",
        "wire it",
        "-d",
        "Created by the Kraft orchestrator.",
    ]
    assert calls == [([*argv, "--type", "task"], "/r")]


@pytest.mark.beads_adapter
async def test_complete_raises_when_bd_cannot_close_the_bead(bd_answers):
    """Unlike the best-effort reads, a close that bd refused is an error the
    caller hears: the item's bead would otherwise stay open unnoticed."""
    calls = bd_answers("", returncode=1, stderr="Error: no issue X-1")
    with pytest.raises(subprocess.CalledProcessError):
        await beads.complete("X-1", cwd="/r")
    assert calls == [(["bd", "close", "X-1"], "/r")]


@pytest.mark.beads_adapter
async def test_intake_refuses_output_with_no_issue_object(bd_answers):
    """A bd that exits 0 but prints no object has filed nothing Kraft can
    track: a failed intake that says what bd printed, not a bead id."""
    bd_answers("created\n", stderr="warning: flushed")
    with pytest.raises(RuntimeError) as excinfo:
        await beads.intake("wire it", cwd="/r")
    assert str(excinfo.value) == "bd create emitted no JSON: 'created\\n' stderr='warning: flushed'"


@pytest.mark.beads_adapter
def test_intake_error_carries_bd_stderr(tmp_path, monkeypatch):
    """Kraft-ibwj: `CalledProcessError` names the argv and the exit status and
    not the one line that explains them. The raised message must carry bd's own
    words, because that message is what reaches the user."""
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    bd = stub_dir / "bd"
    bd.write_text('#!/bin/sh\necho "Error: no beads database found" >&2\nexit 1\n')
    bd.chmod(0o755)
    monkeypatch.setenv("PATH", str(stub_dir))
    with pytest.raises(RuntimeError) as excinfo:
        asyncio.run(beads.intake("x", cwd=str(tmp_path)))
    assert "no beads database found" in str(excinfo.value)


@pytest.mark.e2e("bd")
def test_search_finds_a_closed_bead(tmp_path, monkeypatch):
    """Kraft-evm: a completed work item closes its bead, and that is exactly the
    work the search strip has to be able to find.

    Kraft-uqpan: bd 1.3.0 searches closed beads by default, so the result alone
    no longer pins the `--status all` Kraft passes for the bd versions that do
    not (CI runs 1.2.2). The argv of the real call is pinned as well."""
    repo = isolated_bd(tmp_path)
    real_run = subprocess.run
    argvs: list[list[str]] = []

    def recording_run(argv, *args, **kwargs):
        argvs.append(list(argv))
        return real_run(argv, *args, **kwargs)

    monkeypatch.setattr(beads.subprocess, "run", recording_run)

    async def scenario():
        bead_id = await beads.intake("caulk the transom", cwd=str(repo))
        await beads.complete(bead_id, cwd=str(repo))
        hits = await beads.search("caulk the transom", cwd=str(repo))
        assert [(h["id"], h["status"]) for h in hits] == [(bead_id, "closed")]
        return bead_id

    bead_id = asyncio.run(scenario())
    (search,) = [a for a in argvs if a[:2] == ["bd", "search"]]
    assert " --status all" in " ".join(search), (
        f"beads.search ran {search!r}: without `--status all`, a bd that searches open "
        f"beads by default leaves the closed bead {bead_id} out of the strip (Kraft-evm)"
    )


@pytest.mark.beads_adapter
def test_search_degrades_when_bd_is_missing(tmp_path, monkeypatch):
    """Kraft-9m4: best-effort by contract — no `bd` on PATH is an empty strip,
    not a 500 out of GET /beads/search."""
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert asyncio.run(beads.search("anything")) == []


@pytest.mark.e2e("bd")
def test_ready_returns_open_beads_with_int_priorities(tmp_path):
    repo = isolated_bd(tmp_path)
    bid = asyncio.run(beads.intake("pick me up", cwd=str(repo)))
    rows = asyncio.run(beads.ready(cwd=str(repo)))
    row = next(r for r in rows if r["id"] == bid)
    assert isinstance(row["priority"], int)
    assert row["title"] == "pick me up"


@pytest.mark.e2e("bd")
def test_ready_is_best_effort_on_a_directory_with_no_beads(tmp_path):
    """A poller that raises kills its own task; an empty list is the honest answer."""
    plain = tmp_path / "plain"
    plain.mkdir()
    assert asyncio.run(beads.ready(cwd=str(plain))) == []


@pytest.mark.beads_adapter
def test_ready_is_best_effort_when_bd_emits_undecodable_bytes(tmp_path, monkeypatch):
    """`subprocess.run(text=True)` decodes stdout as UTF-8 and raises
    `UnicodeDecodeError` on bytes that are not; the poller's caller must still
    get a list."""
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    bd = stub_dir / "bd"
    bd.write_text('#!/bin/sh\nprintf "[\\377\\376]"\n')
    bd.chmod(0o755)
    monkeypatch.setenv("PATH", str(stub_dir))
    assert asyncio.run(beads.ready(cwd=str(tmp_path))) == []


@pytest.mark.e2e("bd")
def test_blocked_by_returns_the_row_s_blockers(tmp_path):
    repo = isolated_bd(tmp_path)

    async def scenario():
        a = await beads.intake("A", cwd=str(repo))
        b = await beads.intake("B", cwd=str(repo))
        subprocess.run(
            ["bd", "dep", "add", b, a, "--type", "blocks"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        )
        assert await beads.blocked_by([b], cwd=str(repo)) == [a]
        assert await beads.blocked_by([a], cwd=str(repo)) == []

    asyncio.run(scenario())


@pytest.mark.e2e("bd")
def test_blocked_by_unions_blockers_across_every_id_passed(tmp_path):
    """The motivating case (plan-review finding 1): a work item's own
    tracking bead has no edges, but a sub-bead named in `implements_beads`
    does -- one call must answer for both ids."""
    repo = isolated_bd(tmp_path)

    async def scenario():
        tracking = await beads.intake("tracking bead", cwd=str(repo))
        sub = await beads.intake("sub bead", cwd=str(repo))
        blocker = await beads.intake("blocker", cwd=str(repo))
        subprocess.run(
            ["bd", "dep", "add", sub, blocker, "--type", "blocks"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        )
        # tracking has no edges of its own; sub is blocked. Passing both in
        # one call must still surface the blocker.
        return await beads.blocked_by([tracking, sub], cwd=str(repo)), blocker

    result, blocker = asyncio.run(scenario())
    assert result == [blocker]


@pytest.mark.beads_adapter
def test_blocked_by_is_best_effort_when_bd_is_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert asyncio.run(beads.blocked_by(["X-1"], cwd=str(tmp_path))) == []


@pytest.mark.beads_adapter
@pytest.mark.parametrize(
    "stdout", ["not json", "[not json", 'Note: x\n[{"id": "X-1",'], ids=["no-list", "bad", "cut"]
)
async def test_blocked_by_tolerates_unparsable_output(bd_answers, stdout):
    """No list at all, and a list that does not parse, both answer `[]`."""
    calls = bd_answers(stdout)
    assert await beads.blocked_by(["X-1"], cwd="/tmp") == []
    assert calls == [(["bd", "blocked", "--json"], "/tmp")]


@pytest.mark.beads_adapter
def test_blocked_by_of_no_ids_is_a_no_op(monkeypatch):
    """No bead, no implements_beads: nothing to ask bd about, and no
    subprocess launched to ask it with."""

    def _boom(*a, **k):
        raise AssertionError("bd should not be invoked with an empty id list")

    monkeypatch.setattr(beads.subprocess, "run", _boom)
    assert asyncio.run(beads.blocked_by([], cwd="/tmp")) == []
