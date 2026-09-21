"""Phase 4's exit criterion on the shipped default chain: a `KRAFT_FAIL` work
item whose merge request keeps failing CI walks the node's recovery, then its
fix loop, then escalation -- and ends with a human, having spent exactly the
attempts the seed allows. The agents are `fixtures/fake-claude.sh`, which fails
every launch whose context names `KRAFT_FAIL`; git is real, the forge is fake."""

from pathlib import Path

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


async def test_a_failing_item_walks_recovery_then_the_fix_loop_then_escalation(
    item_on, tmp_path, monkeypatch
):
    templates = seed_v1_library(tmp_path / "templates", agent_command=str(FAKE_CLAUDE))
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(templates))
    monkeypatch.setattr(
        forge.run,
        "resolve",
        lambda name: forge.FakeForge(ci_states=["failed"], ci_failed_jobs=[RED]),
    )
    chain = v1_named_chain(templates, "default")
    it = await item_on(chain, NODE, title="KRAFT_FAIL the pipeline never goes green")
    start = [n.id for n in chain.nodes].index(NODE)

    status = await executor.run(
        it.database,
        it.run_dirs,
        work_item_id=it.id,
        registry=None,
        start_index=start,
        policy=_policy.Policy(loops={}, default=_policy.Cap(9, 3600), auto_escalate_delay_s=0),
        launch=executor.LaunchContext(repo_entry=ON_A_FORGE, steering_dir=None),
    )

    trail = [(e["type"], e["payload"]) for e in it.events() if e["type"] in TRAIL]
    for kind, payload in trail:  # the report's event trail: `just test ... -s`
        print(kind, {k: payload.get(k) for k in ("scope", "cycle", "verdict", "reason", "auto")})
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
