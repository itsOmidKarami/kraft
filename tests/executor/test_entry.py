import asyncio
import json
from pathlib import Path

import pytest
from support.harness import isolated_bd, v1_resolved

from kraft import db, executor
from kraft.paths import RunDirs

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _quick_task():
    """The smallest V1 chain intake can file: one exec node, one agent task.

    `intake` takes a `ResolvedChain` now, not a legacy `Template` -- it writes
    the `materialized_chain` column, which is the executor's only input.
    """
    return v1_resolved(
        [
            {
                "id": "implementation",
                "kind": "exec",
                "tasks": [
                    {"id": "implement", "kind": "agent", "harness": "fake", "prompt": "do it"}
                ],
            }
        ],
        chain_id="quick-task",
    )


def test_intake_creates_bead_and_row(bd, tmp_path):
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
                chain=_quick_task(),
                bd_cwd=str(tracker),
            )
            row = database.read(
                lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (wid,)).fetchone()
            )
            assert row["status"] == "active"
            assert row["bead_id"]
            assert row["chain_template"] == "quick-task"
            # `materialized_chain`, not `chain_definition`: intake writes the
            # V1 snapshot and leaves the legacy column at "{}".
            assert row["chain_definition"] == "{}"
            stored = json.loads(row["materialized_chain"])
            assert [n["id"] for n in stored["chain"]["nodes"]] == ["implementation"]
            assert row["current_node_id"] is None
            assert bd.status(row["bead_id"], cwd=tracker) == "open"
        finally:
            await database.close()

    asyncio.run(scenario())


def _intake_row(tmp_path, *, description=None, implements_beads=None):
    tracker = isolated_bd(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = await executor.intake(
                database,
                rd,
                title="t",
                repo="/some/repo",
                chain=_quick_task(),
                bd_cwd=str(tracker),
                description=description,
                implements_beads=implements_beads,
            )
            return database.read(
                lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (wid,)).fetchone()
            )
        finally:
            await database.close()

    return asyncio.run(scenario())


def test_a_bead_id_in_the_description_is_not_a_promise(tmp_path):
    """The trap this removes: `_extract_beads` scraped every id out of the
    description into `implements_beads`, which is closed on completion -- so a
    sentence saying a bead was NOT in scope closed it anyway. Four items closed
    a bead they never implemented that way, two of them P1s."""
    row = _intake_row(tmp_path, description="Kraft-abc12 is context. Kraft-def34 is not in scope.")
    assert row["implements_beads"] is None


def test_implements_beads_is_taken_from_the_argument(tmp_path):
    row = _intake_row(tmp_path, description="no ids here", implements_beads=["Kraft-abc12"])
    assert json.loads(row["implements_beads"]) == ["Kraft-abc12"]


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
                chain=_quick_task(),
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
                chain=_quick_task(),
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
    chain = v1_resolved(
        [
            {
                "id": "spec",
                "kind": "exec",
                "tasks": [
                    {
                        "id": "author",
                        "kind": "agent",
                        "harness": "fake",
                        "prompt": "spec",
                        "produces": "spec",
                    }
                ],
            },
            {"id": "spec_approval", "kind": "gate", "message": "ok?", "artifact": "spec"},
            {
                "id": "plan",
                "kind": "exec",
                "tasks": [
                    {
                        "id": "author",
                        "kind": "agent",
                        "harness": "fake",
                        "prompt": "plan",
                        "produces": "plan",
                    }
                ],
            },
            {"id": "plan_approval", "kind": "gate", "message": "ok?", "artifact": "plan"},
            {
                "id": "implementation",
                "kind": "exec",
                "tasks": [
                    {"id": "implement", "kind": "agent", "harness": "fake", "prompt": "do it"}
                ],
            },
        ],
        chain_id="default",
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
                chain=chain,
                bd_cwd=str(isolated_bd(tmp_path)),
                attachments=[{"kind": "plan", "path": ".engineering/plans/p.md"}],
            )
            row = database.read(
                lambda c: c.execute(
                    "SELECT materialized_chain, attachments FROM work_items WHERE id = ?", (wid,)
                ).fetchone()
            )
            # The plan node *and* the gate whose `artifact` names the plan --
            # in V1 they are two nodes, and both are redundant once the
            # document arrives already written.
            stored = json.loads(row["materialized_chain"])
            assert [n["id"] for n in stored["chain"]["nodes"]] == [
                "spec",
                "spec_approval",
                "implementation",
            ]
            assert json.loads(row["attachments"])[0]["kind"] == "plan"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_intake_copies_an_attachment_into_kraft_storage(tmp_path):
    """Kraft-eqgn: the trim is irreversible, so the document that justifies it
    has to be Kraft's own from that moment — not a path into someone else's
    working tree that can be deleted an hour later."""
    chain = v1_resolved(
        [
            {
                "id": "plan",
                "kind": "exec",
                "tasks": [
                    {
                        "id": "author",
                        "kind": "agent",
                        "harness": "fake",
                        "prompt": "plan",
                        "produces": "plan",
                    }
                ],
            },
            {"id": "plan_approval", "kind": "gate", "message": "ok?", "artifact": "plan"},
            {
                "id": "implementation",
                "kind": "exec",
                "tasks": [
                    {"id": "implement", "kind": "agent", "harness": "fake", "prompt": "do it"}
                ],
            },
        ],
        chain_id="default",
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
                chain=chain,
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
    chain = v1_resolved(
        [
            {
                "id": "plan",
                "kind": "exec",
                "tasks": [
                    {
                        "id": "author",
                        "kind": "agent",
                        "harness": "fake",
                        "prompt": "plan",
                        "produces": "plan",
                    }
                ],
            },
            {"id": "plan_approval", "kind": "gate", "message": "ok?", "artifact": "plan"},
            {
                "id": "implementation",
                "kind": "exec",
                "tasks": [
                    {"id": "implement", "kind": "agent", "harness": "fake", "prompt": "do it"}
                ],
            },
        ],
        chain_id="default",
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
                chain=chain,
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
    chain = v1_resolved(
        [
            {
                "id": "plan",
                "kind": "exec",
                "tasks": [
                    {
                        "id": "author",
                        "kind": "agent",
                        "harness": "fake",
                        "prompt": "plan",
                        "produces": "plan",
                    }
                ],
            },
            {"id": "plan_approval", "kind": "gate", "message": "ok?", "artifact": "plan"},
            {
                "id": "implementation",
                "kind": "exec",
                "tasks": [
                    {"id": "implement", "kind": "agent", "harness": "fake", "prompt": "do it"}
                ],
            },
        ],
        chain_id="default",
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
                    chain=chain,
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


def test_a_fixes_trailer_names_a_bead_to_close():
    assert executor.entry._trailer_beads(
        ["feat: x\n\nFixes Kraft-abc12.", "chore: y", "fix: z\n\nCloses: Kraft-def34"]
    ) == ["Kraft-abc12", "Kraft-def34"]


def test_a_bare_bead_id_in_a_commit_body_is_not_a_trailer():
    """Same discipline as the description: mentioning an id promises nothing.
    Only `Fixes`/`Closes` does."""
    assert executor.entry._trailer_beads(["fix: touches Kraft-abc12 in passing"]) == []


def _over_ceiling():
    """A chain whose own `policy:` widens `allowed_tools` past a ceiling of
    `[git]`, and that ceiling (Kraft-ib2af)."""
    import dataclasses

    from kraft.policy import InstancePolicy, InstancePolicyInput, TemplatePolicyOverride

    chain = _quick_task()
    chain = dataclasses.replace(
        chain,
        chain=chain.chain.model_copy(
            update={"policy": TemplatePolicyOverride(allowed_tools=["git", "rm_rf"])}
        ),
    )
    ceiling = InstancePolicy.from_input(
        InstancePolicyInput.model_validate({"maxima": {"allowed_tools": ["git"]}})
    )
    return chain, ceiling


def test_a_chain_policy_over_the_ceiling_is_refused_before_any_side_effect(tmp_path, monkeypatch):
    """Kraft-ib2af: the refusal is a `ValueError` every intake door already
    turns into a legible answer, and it comes before the bead is filed and the
    attachments are copied -- a trigger filing an orphaned bead every due
    minute was the failure."""
    chain, ceiling = _over_ceiling()
    filed = []

    async def record(*a, **kw):
        filed.append(a)
        return "TEST-orphan"

    monkeypatch.setattr("kraft.executor.beads.intake", record)
    doc = tmp_path / "plan.md"
    doc.write_text("# plan\n")

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            with pytest.raises(ValueError, match="cannot widen the inherited safety ceiling"):
                await executor.intake(
                    database,
                    rd,
                    title="t",
                    repo=str(tmp_path),
                    chain=chain,
                    effective_policy=ceiling,
                    attachments=[{"kind": "plan", "path": "p.md", "source": str(doc)}],
                )
            assert database.read(lambda c: c.execute("SELECT id FROM work_items").fetchall()) == []
            assert not any(rd.attachments.iterdir())
        finally:
            await database.close()

    asyncio.run(scenario())
    assert filed == []
