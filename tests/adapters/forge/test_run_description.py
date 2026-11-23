"""A re-describe of a merge request: what Kraft wrote is replaced, what a person
wrote is not."""

from __future__ import annotations

import pytest

from kraft.adapters import forge


def _authored(worktree, text: str, front: str = "") -> str:
    """What a `describe_merge_request` leaves; returns the description."""
    meta = worktree / ".engineering" / "mr_metas" / "w1.md"
    meta.parent.mkdir(parents=True, exist_ok=True)
    body = f"## What this introduces\n\n{text}"
    meta.write_text(f"---\nwork_item_ids: [w1]\n{front}---\n{body}\n")
    return body


@pytest.mark.parametrize("handler", ["open_mr", "sync_mr"])
@pytest.mark.parametrize(
    "edit, kept",
    [
        (lambda body: body, False),
        (lambda body: body.replace("\n", "\r\n") + "\n", False),
        (lambda body: body + "\n\n## Changelog\n- A person's line.", True),
        (lambda body: body.replace("A door", "A person's door"), True),
        (lambda body: "", True),
    ],
    ids=["untouched", "forge-round-trip", "changelog-edit", "other-section-edit", "emptied"],
)
async def test_a_re_describe_replaces_only_a_description_kraft_wrote(
    run_forge, tmp_path, handler, edit, kept
):
    """Whole or not at all, so nothing can come out half-merged, and a label a
    person changed survives either way: no path here writes one."""
    _authored(tmp_path, "A door.", "labels: [release::none]\n")
    fake = forge.FakeForge()
    await run_forge(fake, "open_mr", "x1")
    live = edit(fake.opened_bodies[1])
    fake.bodies["kraft/w1"] = live
    fake.labeled.append("release::patch")
    new = _authored(tmp_path, "A window.")

    await run_forge(fake, handler, "x2")

    assert fake.labeled == ["release::patch"]
    if kept:
        assert fake.bodies["kraft/w1"] == live
        assert [e["repo"] for e in run_forge.events("mr_description_kept")] == [str(tmp_path)]
        assert "not the one Kraft last wrote" in run_forge.log("x2")
    else:
        assert fake.bodies["kraft/w1"].startswith(new)
        assert run_forge.events("mr_description_kept") == []


@pytest.mark.parametrize("handler", ["open_mr", "sync_mr"])
@pytest.mark.parametrize("live", ["Written before Kraft kept a record.", ""], ids=["text", "empty"])
async def test_a_merge_request_with_no_record_keeps_its_description(
    run_forge, tmp_path, handler, live
):
    fake = forge.FakeForge()
    await fake.open_mr(repo=tmp_path, branch="kraft/w1", base="main", title="t", body=live)

    await run_forge(fake, handler, "x2")

    assert await fake.mr_body(repo=tmp_path, branch="kraft/w1") == live
    assert len(run_forge.events("mr_description_kept")) == 1


async def test_a_description_write_whose_answer_was_lost_is_not_read_as_an_edit(
    run_forge, tmp_path
):
    class _LostAnswer(forge.FakeForge):
        async def update_mr(self, *, repo, branch, body):
            first = not self.bodies
            await super().update_mr(repo=repo, branch=branch, body=body)
            if first:
                raise forge.ForgeError("connection reset after the write")

    _authored(tmp_path, "A door.")
    fake = _LostAnswer()
    await run_forge(fake, "open_mr", "x1")
    _authored(tmp_path, "A window.")
    await run_forge(fake, "sync_mr", "x2")
    third = _authored(tmp_path, "A third body.")

    await run_forge(fake, "sync_mr", "x3")

    assert fake.bodies["kraft/w1"].startswith(third)
    assert run_forge.events("mr_description_kept") == []
