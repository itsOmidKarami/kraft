"""Which agent tasks a `/resume` steer reaches: the ones a pause stopped, and the
ones that asked a question. A sibling of test_pause_resume.py."""

from __future__ import annotations

import pytest
from test_pause_resume import _wait

REVIEW = "verification.review.code_review"


def _paused_in_verification(client, repo, task=REVIEW, ended="paused"):
    """An item paused in `verification` with `task`'s session paused -- by
    default its agent review's -- the way `/pause` leaves one; forced, so no
    agent runs. `ended="needs_context"` leaves it asking, the stop its question."""
    from kraft import store
    from kraft.vocab import StopKind as K

    wid = client.post(
        "/api/work-items",
        json={"title": "t", "repo": str(repo), "chain_template": "default", "autostart": False},
    ).json()["id"]
    db = client.app.state.db

    async def seed():
        await db.write(lambda c: store.load_chain(c, wid, "verification"))
        await db.write(
            lambda c: store.create_session(
                c,
                id=f"s-{wid}",
                work_item_id=wid,
                node_id="verification",
                hook_point=task,
                log_path="/l",
                result_path="/r",
            )
        )
        await db.write(lambda c: store.session_exited(c, f"s-{wid}", ended, work_item_id=wid))
        if ended == "needs_context":
            ask = "needs_context: which API?"
            await db.write(
                lambda c: store.mark_needs_human(c, wid, "verification", ask, kind=K.QUESTION)
            )

    client.portal.call(seed)
    return wid


@pytest.mark.parametrize(
    ("body", "targets", "ended"),
    [
        ({"steer": "keep the old API"}, {REVIEW: "keep the old API"}, "paused"),
        ({"steer": "x", "steers": {REVIEW: "only you"}}, {REVIEW: "only you"}, "paused"),
        ({"steer": "x", "steers": {REVIEW: "use v2"}}, {REVIEW: "use v2"}, "needs_context"),
    ],
    ids=["one-steer-for-every-paused-agent", "individual", "answers-the-task-that-asked"],
)
def test_resume_addresses_its_steer_to_the_paused_agent_tasks(
    monkeypatch, repo, client, body, targets, ended
):
    from kraft import executor

    seen = {}

    async def fake_run(*args, **kw):
        seen.update(kw)
        return "completed"

    monkeypatch.setattr(executor, "run", fake_run)
    wid = _paused_in_verification(client, repo, ended=ended)

    r = client.post(f"/api/work-items/{wid}/resume", json=body)

    assert r.status_code == 200, r.text
    _wait(lambda: seen, "the resumed walk")
    assert (seen["steer_to"], seen["steer"]) == (targets, None)


def test_a_steer_aimed_at_a_non_agent_task_is_refused_naming_it(repo, client):
    """`steer-can-address-paused-agent-tasks-individually`: a steer SHALL NOT
    target a non-agent task."""
    wid = _paused_in_verification(client, repo)
    path = "verification.tests.test_changed_scopes"

    r = client.post(f"/api/work-items/{wid}/resume", json={"steers": {path: "x"}})

    assert r.status_code == 422, r.text
    assert r.json()["detail"].startswith(f"steers.{path}: ")
    assert "a steer reaches only an agent task" in r.json()["detail"]
    assert client.get(f"/api/work-items/{wid}").json()["status"] == "paused"


def test_a_pause_that_stopped_no_agent_task_takes_no_steer(repo, client):
    """Ruling 183 (Kraft-5d3sy): a steer reaches agentic work the pause
    stopped. One that stopped a subprocess has nothing to take it, so both
    doors refuse the text rather than hand it to whichever agent runs next,
    and the detail screen offers no steer box."""
    wid = _paused_in_verification(client, repo, task="verification.tests.test_changed_scopes")
    refusal = (
        "no agent task is paused at node 'verification', and a steer reaches only "
        "an agent task a pause stopped; resume without one"
    )

    for verb, body in (("steer", {"text": "x"}), ("resume", {"steer": "x"})):
        r = client.post(f"/api/work-items/{wid}/{verb}", json=body)
        assert (r.status_code, r.json()["detail"]) == (409, refusal)
    item = client.get(f"/api/work-items/{wid}").json()
    assert (item["status"], item["steerable"], item["pending_steer_context"]) == (
        "paused",
        False,
        None,
    )
    agentic = _paused_in_verification(client, repo)
    assert client.get(f"/api/work-items/{agentic}").json()["steerable"] is True
