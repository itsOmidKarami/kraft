"""`walk.run`: one walk of the chain, then the two follow-ups every caller of
`run` relies on -- a pending gate's automated review, then a stuck item's
auto-escalation -- each handed the status the step before it left (Kraft-hvj13)."""

from kraft.executor import gates, walk


async def test_run_walks_then_reviews_gates_then_escalates_a_stop(monkeypatch):
    calls = []

    async def run_once(*args, **kwargs):
        calls.append(("walk", None))
        return "needs_human"

    async def review_gates(status, *args, **kwargs):
        calls.append(("review_gates", status))
        return "after-review"

    async def auto_escalate_stuck(status, *args, **kwargs):
        calls.append(("auto_escalate_stuck", status))
        return "after-escalation"

    monkeypatch.setattr(walk, "run_once", run_once)
    monkeypatch.setattr(gates, "review_gates", review_gates)
    monkeypatch.setattr(gates, "auto_escalate_stuck", auto_escalate_stuck)

    status = await walk.run(None, None, work_item_id="w1")

    assert status == "after-escalation"
    assert calls == [
        ("walk", None),
        ("review_gates", "needs_human"),
        ("auto_escalate_stuck", "after-review"),
    ]
