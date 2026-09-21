"""Phase 4's exit criterion on the shipped default chain: a `KRAFT_FAIL` work
item whose merge request keeps failing CI walks the node's recovery, then its
fix loop, then escalation -- and ends with a human, having spent exactly the
attempts the seed allows. And the same item failing its local review walks
`verification`'s own fix loop, which re-runs the tests and the review and never
the implementer (Ruling 87). The agents are `fixtures/fake-claude.sh`, which
fails every launch whose context names `KRAFT_FAIL`; git is real, the forge is
fake."""

from pathlib import Path

import pytest
from support.harness import seed_v1_library, v1_named_chain

from kraft import executor
from kraft import policy as _policy
from kraft.adapters import forge

FAKE_CLAUDE = Path(__file__).resolve().parents[2] / "fixtures" / "fake-claude.sh"
ON_A_FORGE = {"setup_command": "", "forge": "github"}
RED = (forge.FailedJob("test", "failed", "script_failure"),)
NODE = "merge_request_feedback"
#: What the walk is about: the controls, the stop, and the escalation after it.
TRAIL = (
    "node_recovery_started",
    "fix_cycle_started",
    "judge_verdict",
    "findings_measured",
    "work_item_needs_human",
    "escalation_message",
)


async def _walk_from(node, item_on, tmp_path, monkeypatch):
    """One walk of a `KRAFT_FAIL` item from `node`: `(item, status, argv of
    every agent launch)`."""
    templates = seed_v1_library(tmp_path / "templates", agent_command=str(FAKE_CLAUDE))
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(templates))
    argv_log = tmp_path / "argv.log"
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE_ARGV_LOG", str(argv_log))
    monkeypatch.setattr(
        forge.run,
        "resolve",
        lambda name: forge.FakeForge(ci_states=["failed"], ci_failed_jobs=[RED]),
    )
    chain = v1_named_chain(templates, "default")
    it = await item_on(chain, node, title="KRAFT_FAIL the pipeline never goes green")
    start = [n.id for n in chain.nodes].index(node)

    status = await executor.run(
        it.database,
        it.run_dirs,
        work_item_id=it.id,
        start_index=start,
        policy=_policy.Policy(loops={}, default=_policy.Cap(9, 3600), auto_escalate_delay_s=0),
        launch=executor.LaunchContext(repo_entry=ON_A_FORGE, steering_dir=None),
    )
    records = argv_log.read_text().split("\x00\n") if argv_log.exists() else []
    return it, status, [r.splitlines() for r in records if r.strip()]


def _trail(it) -> list[tuple[str, dict]]:
    trail = [(e["type"], e["payload"]) for e in it.events() if e["type"] in TRAIL]
    for kind, payload in trail:  # the report's event trail: `just test ... -s`
        print(kind, {k: payload.get(k) for k in ("scope", "cycle", "verdict", "reason", "auto")})
    return trail


@pytest.fixture
async def failed_walk(item_on, tmp_path, monkeypatch):
    """The post-draft walk, once."""
    return await _walk_from(NODE, item_on, tmp_path, monkeypatch)


async def test_a_failing_item_walks_recovery_then_the_fix_loop_then_escalation(failed_walk):
    it, status, _launches = failed_walk

    trail = _trail(it)
    kinds = [k for k, _ in trail]

    assert status == "needs_human"
    [recovery] = [p for k, p in trail if k == "node_recovery_started"]
    assert recovery["scope"] == "node"
    assert recovery["tasks"] == [
        f"{NODE}.on_failure.repair.repair_feedback",
        f"{NODE}.on_failure.sync.sync_mr",
    ]
    cycles = [p["cycle"] for k, p in trail if k == "fix_cycle_started"]
    assert cycles and cycles == list(range(1, len(cycles) + 1))
    # The judge only ever speaks after the first attempt, and its failed
    # launches fall open rather than stopping the loop.
    assert kinds.index("judge_verdict") > kinds.index("fix_cycle_started")
    stop = kinds.index("work_item_needs_human")
    assert kinds.index("node_recovery_started") < kinds.index("fix_cycle_started") < stop
    assert kinds.index("escalation_message", stop) > stop
    [escalation] = [p for k, p in trail if k == "escalation_message"]
    assert escalation["auto"] is True
    assert it.status() == "needs_human"


def _option(argv: list[str], flag: str) -> str | None:
    return argv[argv.index(flag) + 1] if flag in argv else None


async def test_the_judge_launches_on_its_own_runtime_not_the_fixers(failed_walk):
    """`judge-runtime-is-independent-from-fixer-runtime`: the seeded judge
    (`strict_judge`, profile `claude_review`) and the repair it judges
    (`repair_mr_feedback`, profile `codex_default` with its own model) each
    launch on their own task's configuration."""
    _it, _status, launches = failed_walk

    def prompt(argv):
        return _option(argv, "-p") or ""

    judges = [a for a in launches if prompt(a).startswith("The fix loop on node")]
    repairs = [a for a in launches if prompt(a).startswith(f"The checks in node {NODE} failed")]
    assert judges and repairs
    assert {(_option(a, "--model"), _option(a, "--effort")) for a in judges} == {("sonnet", "high")}
    assert {_option(a, "--model") for a in repairs} == {"gpt-5.6-terra"}


async def test_a_failing_review_walks_verifications_own_fix_loop_never_the_implementer(
    item_on, tmp_path, monkeypatch
):
    """Ruling 87 on real launches. The tests pass (the seed's test builtin is
    neutered to `true` here) and the review fails, so `verification`'s fix loop
    repairs and re-measures from its first step -- the tests, then the review,
    each round -- until the stall stops it. The implementer never launches,
    and every review launch is handed the review package."""
    it, status, launches = await _walk_from("verification", item_on, tmp_path, monkeypatch)

    trail = _trail(it)
    assert status == "needs_human"
    assert {p["node_id"] for k, p in trail if k == "fix_cycle_started"} == {"verification"}
    paths = [s["hook_point"] for s in it.sessions("verification")]
    measured = [p for p in paths if p.startswith(("verification.tests", "verification.review"))]
    rounds = len(it.events("findings_measured"))
    assert rounds >= 2
    assert (
        measured
        == [
            "verification.tests.test_changed_scopes",
            "verification.review.code_review",
        ]
        * rounds
    )
    assert "verification.fix_loop.main.repair" in paths
    assert not it.sessions("implementation")
    reviews = [a for a in launches if (_option(a, "-p") or "").startswith("Review this work")]
    assert len(reviews) == rounds
    assert all("$KRAFT_REVIEW_PACKAGE" in " ".join(a) for a in reviews)
