"""Intake refuses a plan whose `## Slices` section cannot be split."""


def test_intake_rejects_a_plan_whose_slices_share_a_task(client, repo):
    plan = repo / ".engineering" / "plans" / "p.md"
    plan.parent.mkdir(parents=True)
    plan.write_text(
        "## Slices\n\n- **Slice 1: A.** Tasks 1-2.\n- **Slice 2: B.** Tasks 2-3.\n\n"
        + "".join(f"### Task {n}: t\n\n" for n in (1, 2, 3))
    )
    r = client.post(
        "/api/work-items",
        json={
            "autostart": True,
            "title": "t",
            "repo": str(repo),
            "attachments": [{"kind": "plan", "path": ".engineering/plans/p.md"}],
        },
    )
    assert r.status_code == 422
    assert "two slices" in r.text
