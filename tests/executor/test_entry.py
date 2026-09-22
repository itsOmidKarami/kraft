"""`executor.intake`: the row and bead a work item is filed as, the chain
snapshot it runs, and the attachments it keeps."""

import dataclasses
import json
from pathlib import Path

import pytest
from support.harness import isolated_bd, v1_resolved

from kraft import executor

_IMPLEMENT = """
- id: implementation
  kind: exec
  tasks: [{id: implement, kind: agent, harness: fake, prompt: do it}]
"""

#: plan -> plan_approval -> implementation: the shape an attached plan trims.
_PLANNED = (
    """
- id: plan
  kind: exec
  tasks: [{id: author, kind: agent, harness: fake, prompt: plan, produces: plan}]
- {id: plan_approval, kind: gate, message: "ok?", artifact: plan}
"""
    + _IMPLEMENT
)


def _quick_task():
    """The smallest V1 chain intake can file: one exec node, one agent task.
    `intake` takes a `ResolvedChain` and writes its `materialized_chain`
    column, which is the executor's only input."""
    return v1_resolved(_IMPLEMENT, chain_id="quick-task")


def _row(database, wid):
    return database.read(
        lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (wid,)).fetchone()
    )


def _intake(database, run_dirs, tmp_path, chain=None, **kwargs):
    kwargs.setdefault("title", "t")
    kwargs.setdefault("repo", str(tmp_path))
    if "bd_cwd" not in kwargs:
        kwargs["bd_cwd"] = str(isolated_bd(tmp_path))
    return executor.intake(database, run_dirs, chain=chain or _quick_task(), **kwargs)


async def test_intake_creates_bead_and_row(bd, database, run_dirs, tmp_path):
    tracker = isolated_bd(tmp_path)

    wid = await _intake(
        database,
        run_dirs,
        tmp_path,
        title="make the failing test pass",
        repo="/some/repo",
        bd_cwd=str(tracker),
    )

    row = _row(database, wid)
    assert row["status"] == "active"
    assert row["bead_id"]
    assert row["chain_template"] == "quick-task"
    # `materialized_chain`, not `chain_definition`: intake writes the V1
    # snapshot and leaves the legacy column at "{}".
    assert row["chain_definition"] == "{}"
    stored = json.loads(row["materialized_chain"])
    assert [n["id"] for n in stored["chain"]["nodes"]] == ["implementation"]
    assert row["current_node_id"] is None
    assert bd.status(row["bead_id"], cwd=tracker) == "open"


@pytest.mark.parametrize(
    ("description", "implements_beads", "stored"),
    [
        # The trap this removes: `_extract_beads` scraped every id out of the
        # description into `implements_beads`, which is closed on completion --
        # so a sentence saying a bead was NOT in scope closed it anyway. Four
        # items closed a bead they never implemented that way, two of them P1s.
        ("Kraft-abc12 is context. Kraft-def34 is not in scope.", None, None),
        ("no ids here", ["Kraft-abc12"], ["Kraft-abc12"]),
    ],
    ids=["an-id-in-the-description-is-not-a-promise", "taken-from-the-argument"],
)
async def test_implements_beads_is_taken_from_the_argument_never_the_description(
    database, run_dirs, tmp_path, description, implements_beads, stored
):
    wid = await _intake(
        database,
        run_dirs,
        tmp_path,
        description=description,
        implements_beads=implements_beads,
    )

    raw = _row(database, wid)["implements_beads"]
    assert (json.loads(raw) if raw else None) == stored


async def test_intake_bead_failure_still_writes_a_row(database, run_dirs, tmp_path):
    """Kraft-7gy: a bd failure degrades intake, it does not fail it — the row is
    written with bead_id NULL rather than raising. See tests/test_bd_workspace.py
    for the full degrade coverage (the event, the API's bead_warning, doctor)."""
    bare = tmp_path / "bare"
    bare.mkdir()

    wid = await _intake(database, run_dirs, tmp_path, bd_cwd=str(bare))

    assert _row(database, wid)["bead_id"] is None


async def test_intake_adopts_a_given_bead_instead_of_filing_a_new_one(
    database, run_dirs, tmp_path, monkeypatch
):
    """Auto-intake starts a bead that already exists. Filing a duplicate of it on
    every pickup is the failure this parameter exists to prevent."""

    async def boom(*a, **kw):
        raise AssertionError("bd create must not run when a bead_id is given")

    monkeypatch.setattr("kraft.executor.beads.intake", boom)

    wid = await _intake(database, run_dirs, tmp_path, bd_cwd=None, bead_id="TEST-abc")

    assert _row(database, wid)["bead_id"] == "TEST-abc"


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


async def test_intake_with_a_plan_attachment_drops_the_plan_node(database, run_dirs, tmp_path):
    spec = """
- id: spec
  kind: exec
  tasks: [{id: author, kind: agent, harness: fake, prompt: spec, produces: spec}]
- {id: spec_approval, kind: gate, message: "ok?", artifact: spec}
"""
    plan_doc = tmp_path / ".engineering" / "plans" / "p.md"
    plan_doc.parent.mkdir(parents=True, exist_ok=True)
    plan_doc.write_text("# p\n")

    wid = await _intake(
        database,
        run_dirs,
        tmp_path,
        v1_resolved(spec + _PLANNED, chain_id="default"),
        attachments=[{"kind": "plan", "path": ".engineering/plans/p.md"}],
    )

    row = _row(database, wid)
    # The plan node *and* the gate whose `artifact` names the plan -- in V1
    # they are two nodes, and both are redundant once the document arrives
    # already written.
    stored = json.loads(row["materialized_chain"])
    assert [n["id"] for n in stored["chain"]["nodes"]] == [
        "spec",
        "spec_approval",
        "implementation",
    ]
    assert json.loads(row["attachments"])[0]["kind"] == "plan"


def _external_plan(tmp_path):
    """A plan written outside the repo: what intake is handed as `source`."""
    external = tmp_path / "elsewhere" / "p.md"
    external.parent.mkdir(parents=True)
    external.write_text("# the plan\n")
    return external


async def _intake_with_plan(database, run_dirs, tmp_path, source):
    return await _intake(
        database,
        run_dirs,
        tmp_path,
        v1_resolved(_PLANNED, chain_id="default"),
        attachments=[{"kind": "plan", "path": ".engineering/plans/p.md", "source": str(source)}],
    )


def _stored_attachment(database, wid):
    return json.loads(_row(database, wid)["attachments"])[0]


async def test_intake_copies_an_attachment_into_kraft_storage(database, run_dirs, tmp_path):
    """Kraft-eqgn: the trim is irreversible, so the document that justifies it
    has to be Kraft's own from that moment — not a path into someone else's
    working tree that can be deleted an hour later."""
    external = _external_plan(tmp_path)

    wid = await _intake_with_plan(database, run_dirs, tmp_path, external)

    entry = _stored_attachment(database, wid)
    # path is untouched: it is where the document lands in the worktree
    assert entry["path"] == ".engineering/plans/p.md"
    # source now points at Kraft's own copy, not the external file
    assert entry["source"] != str(external)
    assert Path(entry["source"]).read_text() == "# the plan\n"
    assert Path(entry["source"]).is_relative_to(run_dirs.attachments)


async def test_the_copy_survives_the_original_being_deleted(database, run_dirs, tmp_path):
    """The whole point, stated as the scenario that produced the bug: a document
    written in a throwaway worktree that is cleaned up before the item runs."""
    external = _external_plan(tmp_path)
    wid = await _intake_with_plan(database, run_dirs, tmp_path, external)

    external.unlink()

    assert Path(_stored_attachment(database, wid)["source"]).read_text() == "# the plan\n"


async def test_intake_refuses_an_attachment_it_cannot_copy(database, run_dirs, tmp_path):
    """The behaviour change that closes the bug: fail at the one moment the
    caller can still fix the path, instead of creating an item that looks fine
    and misbehaves half an hour later with two gates missing."""
    with pytest.raises(ValueError, match="plan attachment"):
        await _intake_with_plan(database, run_dirs, tmp_path, tmp_path / "no-such-file.md")

    # and no half-built row was left behind
    assert database.read(lambda c: c.execute("SELECT id FROM work_items").fetchall()) == []


@pytest.mark.parametrize(
    ("messages", "beads"),
    [
        (
            ["feat: x\n\nFixes Kraft-abc12.", "chore: y", "fix: z\n\nCloses: Kraft-def34"],
            ["Kraft-abc12", "Kraft-def34"],
        ),
        # Same discipline as the description: mentioning an id promises nothing.
        (["fix: touches Kraft-abc12 in passing"], []),
        # A trailer is a line of its own. The word mid-sentence -- a body
        # retelling the brief -- is prose, not a promise (Kraft-iaou3).
        (["fix: y\n\nThe old path closes Kraft-abc12 too early."], []),
    ],
    ids=["fixes-and-closes-trailers", "a-bare-id-in-a-body", "the-word-mid-sentence"],
)
def test_only_a_fixes_or_closes_trailer_names_a_bead_to_close(messages, beads):
    assert executor.entry._trailer_beads(messages) == beads


async def test_a_chain_policy_over_the_ceiling_is_refused_before_any_side_effect(
    database, run_dirs, tmp_path, monkeypatch
):
    """Kraft-ib2af: the refusal is a `ValueError` every intake door already
    turns into a legible answer, and it comes before the bead is filed and the
    attachments are copied -- a trigger filing an orphaned bead every due
    minute was the failure. The chain's own `policy:` widens `allowed_tools`
    past a ceiling of `[git]`."""
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
    filed = []

    async def record(*a, **kw):
        filed.append(a)
        return "TEST-orphan"

    monkeypatch.setattr("kraft.executor.beads.intake", record)
    doc = tmp_path / "plan.md"
    doc.write_text("# plan\n")

    with pytest.raises(ValueError, match="cannot widen the inherited safety ceiling"):
        await _intake(
            database,
            run_dirs,
            tmp_path,
            chain,
            bd_cwd=None,
            effective_policy=ceiling,
            attachments=[{"kind": "plan", "path": "p.md", "source": str(doc)}],
        )

    assert database.read(lambda c: c.execute("SELECT id FROM work_items").fetchall()) == []
    assert not any(run_dirs.attachments.iterdir())
    assert filed == []
