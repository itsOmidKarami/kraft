"""`sync_mr` re-running the failed jobs after it changes the description."""

from __future__ import annotations

import pytest

from kraft.adapters import forge

RED = (forge.FailedJob("test", "failed", "script_failure"),)


class _Marked(forge.FakeForge):
    """A forge that marks its open as an edit, as `GlabCli.open_marker` does."""

    open_marker = forge.mr.OPEN_MARKER


async def _opened_then_repaired(run_forge, fake, worktree) -> None:
    """A description Kraft wrote, and a repair that changed what it would write."""
    await run_forge(fake, "open_mr", "x8", repo=worktree)
    meta = worktree / ".engineering" / "mr_metas" / "w1.md"
    meta.parent.mkdir(parents=True)
    meta.write_text("---\nwork_item_ids: [w1]\n---\nThe repaired description.\n")


@pytest.mark.parametrize(
    "state, live, retried",
    [
        # A repair edited the description under a red check: the jobs that read
        # the old one run again (editing a body triggers no workflow).
        ("failed", None, 1),
        # A sync after new commits sees a pending pipeline, never a red one.
        ("pending", None, 0),
        ("success", None, 0),
        # Kraft kept a person's description, so it changed nothing to re-check.
        ("failed", "person", 0),
        # No repair: only a GitLab open's marker goes, which no check could read.
        ("failed", "marked", 0),
    ],
    ids=[
        "red-check-body-changed",
        "pending-body-changed",
        "green-body-changed",
        "red-check-body-kept",
        "red-check-only-the-open-marker",
    ],
)
async def test_sync_mr_reruns_failed_jobs_only_when_the_description_changed(
    run_forge, tmp_path, state, live, retried
):
    fake = _Marked(ci_states=[state], ci_failed_jobs=[RED])
    if live == "marked":
        await run_forge(fake, "open_mr", "x8", repo=tmp_path)
        fake.bodies["kraft/w1"] = fake.opened_bodies[1] + forge.mr.OPEN_MARKER
    else:
        await _opened_then_repaired(run_forge, fake, tmp_path)
    if live == "person":
        fake.bodies["kraft/w1"] = "A person's description."

    await run_forge(fake, "sync_mr", "x9")
    assert len(fake.retried) == retried, "first sync: the live body differs from the built one"

    fake.retried.clear()
    await run_forge(fake, "sync_mr", "x10")
    assert fake.retried == [], "second sync: the body is already what the sync wrote"


async def test_sync_mr_leaves_a_red_check_of_another_commit_alone(run_forge, tmp_path):
    fake = forge.FakeForge(ci_states=["failed"], ci_failed_jobs=[RED], ci_shas=["not-the-head"])
    await fake.open_mr(repo=tmp_path, branch="kraft/w1", base="main", title="t", body="old")

    await run_forge(fake, "sync_mr", "x9")

    assert fake.retried == []


async def test_sync_mr_writes_nothing_when_the_live_body_cannot_be_read(run_forge, tmp_path):
    """It cannot tell a person's edit from its own text, so it does not overwrite."""

    class _Unreadable(forge.FakeForge):
        async def mr_body(self, *, repo, branch):
            raise forge.ForgeError("cannot read the description")

    fake = _Unreadable(ci_states=["failed"], ci_failed_jobs=[RED])
    await fake.open_mr(repo=tmp_path, branch="kraft/w1", base="main", title="t", body="old")

    assert await run_forge(fake, "sync_mr", "x9") == ("done", "done")

    assert "kraft/w1" not in fake.bodies
    assert fake.retried == []


@pytest.mark.parametrize(
    "new_pipeline, pinned",
    [(True, ""), (False, "555")],
    ids=["new-pipeline-unpins-the-old-one", "same-pipeline-stays-pinned"],
)
async def test_sync_mr_drops_the_pinned_pipeline_only_when_it_made_a_new_one(
    run_forge, repo, new_pipeline, pinned
):
    fake = forge.FakeForge(
        ci_states=["pending", "failed"],
        ci_failed_jobs=[RED],
        ci_pipeline_refs=["555"],
        edit_makes_new_pipeline=new_pipeline,
    )
    await _opened_then_repaired(run_forge, fake, repo)
    await run_forge(fake, "ci_poll", "s1", repo=repo)  # pins head:555

    await run_forge(fake, "sync_mr", "s2", repo=repo)

    ref = run_forge.database.read(
        lambda c: c.execute("SELECT ci_pipeline_ref FROM work_items WHERE id = 'w1'").fetchone()
    )["ci_pipeline_ref"]
    assert ref.split(":")[-1] == pinned
    assert len(fake.retried) == 1
