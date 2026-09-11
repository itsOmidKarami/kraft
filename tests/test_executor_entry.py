import asyncio
import json
import subprocess
from pathlib import Path

import pytest
from support.harness import isolated_bd

from kraft import db, executor
from kraft.paths import RunDirs
from kraft.templates import Template, load_registry, load_templates

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _quick_task() -> Template:
    reg = load_registry(_REPO_ROOT / "templates" / "registry.yaml")
    return load_templates(_REPO_ROOT / "templates", reg).valid["quick-task"]


def _bd_status(repo, bead_id):
    out = subprocess.run(
        ["bd", "show", bead_id, "--json"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return json.loads(out)[0]["status"]


def test_intake_creates_bead_and_row(tmp_path):
    tracker = isolated_bd(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = await executor.intake(
                database,
                rd,
                title="make the failing test pass",
                repo="/some/repo",
                template=_quick_task(),
                bd_cwd=str(tracker),
            )
            row = database.read(
                lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (wid,)).fetchone()
            )
            assert row["status"] == "active"
            assert row["bead_id"]
            assert row["chain_template"] == "quick-task"
            chain = json.loads(row["chain_definition"])
            assert [n["id"] for n in chain["nodes"]] == [
                "env_setup",
                "implementation",
                "verify",
            ]
            assert row["current_node_id"] is None
            assert _bd_status(tracker, row["bead_id"]) in ("open", "in_progress")
        finally:
            await database.close()

    asyncio.run(scenario())


def test_intake_bead_failure_still_writes_a_row(tmp_path):
    """Kraft-7gy: a bd failure degrades intake, it does not fail it — the row is
    written with bead_id NULL rather than raising. See tests/test_bd_workspace.py
    for the full degrade coverage (the event, the API's bead_warning, doctor)."""
    bare = tmp_path / "bare"
    bare.mkdir()

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = await executor.intake(
                database,
                rd,
                title="x",
                repo="/r",
                template=_quick_task(),
                bd_cwd=str(bare),
            )
            row = database.read(
                lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (wid,)).fetchone()
            )
            assert row["bead_id"] is None
        finally:
            await database.close()

    asyncio.run(scenario())


def test_intake_adopts_a_given_bead_instead_of_filing_a_new_one(tmp_path, monkeypatch):
    """Auto-intake starts a bead that already exists. Filing a duplicate of it on
    every pickup is the failure this parameter exists to prevent."""
    called = False

    async def boom(*a, **kw):
        nonlocal called
        called = True
        raise AssertionError("bd create must not run when a bead_id is given")

    monkeypatch.setattr("kraft.executor.beads.intake", boom)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = await executor.intake(
                database,
                rd,
                title="adopted work",
                repo="/some/repo",
                template=_quick_task(),
                bead_id="TEST-abc",
            )
            row = database.read(
                lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (wid,)).fetchone()
            )
            assert row["bead_id"] == "TEST-abc"
        finally:
            await database.close()

    asyncio.run(scenario())
    assert called is False


def test_attachments_reads_a_row_without_the_column():
    # Rows built by older fixtures have no 'attachments' key; that must not raise.
    class Row(dict):
        def keys(self):
            return super().keys()

    assert executor.attachments_of(Row(title="t")) == []
    assert executor.attachments_of(Row(attachments=None)) == []
    assert executor.attachments_of(Row(attachments='[{"kind": "plan", "path": "p.md"}]')) == [
        {"kind": "plan", "path": "p.md"}
    ]


def test_intake_with_a_plan_attachment_drops_the_plan_node(tmp_path):
    template = Template(
        id="default",
        nodes=[
            {"id": "spec", "tasks": ["on.spec.requested"], "gate_after": "spec_approval"},
            {"id": "plan", "tasks": ["on.plan.requested"], "gate_after": "plan_approval"},
            {"id": "implementation", "tasks": ["on.implementation.start"], "gate_after": None},
        ],
    )

    plan_doc = tmp_path / ".engineering" / "plans" / "p.md"
    plan_doc.parent.mkdir(parents=True, exist_ok=True)
    plan_doc.write_text("# p\n")

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = await executor.intake(
                database,
                rd,
                title="t",
                repo=str(tmp_path),
                template=template,
                bd_cwd=str(isolated_bd(tmp_path)),
                attachments=[{"kind": "plan", "path": ".engineering/plans/p.md"}],
            )
            row = database.read(
                lambda c: c.execute(
                    "SELECT chain_definition, attachments FROM work_items WHERE id = ?", (wid,)
                ).fetchone()
            )
            chain = json.loads(row["chain_definition"])
            assert [n["id"] for n in chain["nodes"]] == ["spec", "implementation"]
            assert json.loads(row["attachments"])[0]["kind"] == "plan"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_intake_copies_an_attachment_into_kraft_storage(tmp_path):
    """Kraft-eqgn: the trim is irreversible, so the document that justifies it
    has to be Kraft's own from that moment — not a path into someone else's
    working tree that can be deleted an hour later."""
    template = Template(
        id="default",
        nodes=[
            {"id": "plan", "tasks": ["on.plan.requested"], "gate_after": "plan_approval"},
            {"id": "implementation", "tasks": ["on.implementation.start"], "gate_after": None},
        ],
    )
    external = tmp_path / "elsewhere" / "p.md"
    external.parent.mkdir(parents=True)
    external.write_text("# the plan\n")

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = await executor.intake(
                database,
                rd,
                title="t",
                repo=str(tmp_path),
                template=template,
                bd_cwd=str(isolated_bd(tmp_path)),
                attachments=[
                    {
                        "kind": "plan",
                        "path": ".engineering/plans/p.md",
                        "source": str(external),
                    }
                ],
            )
            stored = database.read(
                lambda c: c.execute(
                    "SELECT attachments FROM work_items WHERE id = ?", (wid,)
                ).fetchone()
            )
            entry = json.loads(stored["attachments"])[0]
            # path is untouched: it is where the document lands in the worktree
            assert entry["path"] == ".engineering/plans/p.md"
            # source now points at Kraft's own copy, not the external file
            assert entry["source"] != str(external)
            assert Path(entry["source"]).is_file()
            assert Path(entry["source"]).read_text() == "# the plan\n"
            assert Path(entry["source"]).is_relative_to(rd.attachments)
        finally:
            await database.close()

    asyncio.run(scenario())


def test_the_copy_survives_the_original_being_deleted(tmp_path):
    """The whole point, stated as the scenario that produced the bug: a document
    written in a throwaway worktree that is cleaned up before the item runs."""
    template = Template(
        id="default",
        nodes=[
            {"id": "plan", "tasks": ["on.plan.requested"], "gate_after": "plan_approval"},
            {"id": "implementation", "tasks": ["on.implementation.start"], "gate_after": None},
        ],
    )
    external = tmp_path / "elsewhere" / "p.md"
    external.parent.mkdir(parents=True)
    external.write_text("# the plan\n")

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = await executor.intake(
                database,
                rd,
                title="t",
                repo=str(tmp_path),
                template=template,
                bd_cwd=str(isolated_bd(tmp_path)),
                attachments=[
                    {"kind": "plan", "path": ".engineering/plans/p.md", "source": str(external)}
                ],
            )
            external.unlink()
            entry = json.loads(
                database.read(
                    lambda c: c.execute(
                        "SELECT attachments FROM work_items WHERE id = ?", (wid,)
                    ).fetchone()
                )["attachments"]
            )[0]
            assert Path(entry["source"]).read_text() == "# the plan\n"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_intake_refuses_an_attachment_it_cannot_copy(tmp_path):
    """The behaviour change that closes the bug: fail at the one moment the
    caller can still fix the path, instead of creating an item that looks fine
    and misbehaves half an hour later with two gates missing."""
    template = Template(
        id="default",
        nodes=[
            {"id": "plan", "tasks": ["on.plan.requested"], "gate_after": "plan_approval"},
            {"id": "implementation", "tasks": ["on.implementation.start"], "gate_after": None},
        ],
    )

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            with pytest.raises(ValueError, match="plan attachment"):
                await executor.intake(
                    database,
                    rd,
                    title="t",
                    repo=str(tmp_path),
                    template=template,
                    bd_cwd=str(isolated_bd(tmp_path)),
                    attachments=[
                        {
                            "kind": "plan",
                            "path": ".engineering/plans/p.md",
                            "source": str(tmp_path / "no-such-file.md"),
                        }
                    ],
                )
            # and no half-built row was left behind
            rows = database.read(lambda c: c.execute("SELECT id FROM work_items").fetchall())
            assert rows == []
        finally:
            await database.close()

    asyncio.run(scenario())
