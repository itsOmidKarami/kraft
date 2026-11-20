"""An `mr.automated_review` task that sets `watch_ci` also pushes and reads the
pipeline at every check, and ends on a red one as its node's CI wait's finding.
Walked through the executor and `forge.run_task` against a `FakeForge`."""

# ruff: noqa: F811 -- `rig` is an imported fixture, taken by name.

from __future__ import annotations

from pathlib import Path

from kraft.adapters import forge
from kraft.executor.round_prompts import CI_LOG_NOTE

from .test_run_chain import RED
from .test_run_review import _review_node, rig  # noqa: F401 -- the fixture

_WAIT, _CI = "feedback.settle.review", "feedback.ci.await_ci"


async def test_a_red_pipeline_the_review_wait_reads_is_the_ci_wait_s_finding(rig):
    """One owner for CI: the same red, read first by the watching wait and then
    by the CI wait, is one finding: same source, one fingerprint, the CI-log
    marking in the fix prompt, and the stuck check sees it unchanged."""
    node = _review_node(10, reads=False, reviewers=(), loop=True, watch_ci=True)
    del node["on_failure"]
    node["fix_loop"]["max_attempts"] = 3
    # Reads: the review wait's red; after the fix, the review wait's pending,
    # then the CI wait's red.
    fake = forge.FakeForge(
        ci_states=["failed", "pending", "failed"], ci_failed_jobs=[*RED, (), *RED]
    )
    await rig(node, fake)

    await rig.walk()

    measured = [e["payload"] for e in rig.item.events("findings_measured")]
    assert [m["fingerprints"] for m in measured] == [measured[0]["fingerprints"]] * 2
    assert measured[0]["fingerprints"]
    assert {f["source_plugin"] for m in measured for f in m["findings"]} == {_CI}
    failed = [s["hook_point"] for s in rig.item.sessions("feedback") if s["status"] == "failed"]
    assert failed == [_WAIT, _CI], "two readers, one finding"
    assert CI_LOG_NOTE in rig.instructions[0] and f"({_CI})" in rig.instructions[0]
    assert rig.item.events("work_item_needs_human")[-1]["payload"]["reason"].startswith("stuck:")
    wait_log = Path(rig.item.sessions("feedback")[0]["log_path"]).read_text()
    assert any(line.startswith(forge.run.CI_RED_LINE) for line in wait_log.splitlines())


async def test_a_red_pipeline_the_review_wait_reads_goes_to_on_failure_first(rig):
    await rig(
        _review_node(10, reads=False, reviewers=(), loop=True, watch_ci=True),
        forge.FakeForge(ci_states=["failed"], ci_failed_jobs=RED),
    )
    await rig.walk()
    assert rig.triaged[0] == "feedback.on_failure.checks.repair_checks"
