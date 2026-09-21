"""The shipped default chain's shape, walked (Ruling 87): the implementer runs
once, and `verification` -- the tests, then the review, under their own fix
loop -- is what a failure re-runs; the pre-draft gate shows a work brief; and a
rebase in post-draft feedback re-tests and re-reviews the rebased head.

The chain is the packaged seed's own, resolved from `templates/`, so every task
keeps the kind it ships with -- the changed-test-scope task stays a `builtin`,
which a test library seeded for the fake agent turns into a subprocess.
`dispatch_node` is scripted (`script`), so nothing real launches."""

from pathlib import Path

import pytest

from kraft import executor, store
from kraft import policy as _policy
from kraft.executor import gates, walk
from kraft.templates.library import TemplateLibrary
from kraft.templates.models import BuiltinTask

SEEDED = Path(__file__).resolve().parents[2] / "templates"
NO_SETUP = {"setup_command": ""}
POLICY = _policy.Policy(loops={}, default=_policy.Cap(3, 3600))


@pytest.fixture
def default_chain():
    return TemplateLibrary.from_yaml_dir(SEEDED).resolve_chain("default")


def _walk(it, chain, node: str):
    return executor.run_once(
        it.database,
        it.run_dirs,
        work_item_id=it.id,
        registry=None,
        start_index=[n.id for n in chain.nodes].index(node),
        policy=POLICY,
        launch=executor.LaunchContext(repo_entry=NO_SETUP, steering_dir=None),
    )


def test_the_seeded_test_task_is_the_builtin_this_file_walks(default_chain):
    """What makes the walks below about the shipped chain: the task a failure
    comes from is the `builtin` Kraft ships, not a stand-in."""
    [tests] = next(n for n in default_chain.nodes if n.id == "verification").steps[0].tasks
    assert isinstance(tests.task, BuiltinTask)


@pytest.mark.parametrize(
    ("red", "calls"),
    [
        (
            "test_changed_scopes",
            ["implement", "test_changed_scopes", "repair", "test_changed_scopes", "code_review"],
        ),
        (
            "code_review",
            [
                "implement",
                "test_changed_scopes",
                "code_review",
                "repair",
                "test_changed_scopes",
                "code_review",
            ],
        ),
    ],
    ids=["tests-red", "review-red"],
)
async def test_a_verification_failure_reruns_the_tests_and_review_not_the_implementer(
    item_on, script, default_chain, red, calls
):
    """A red test or a red review is repaired inside `verification`, whose
    fix loop re-measures from its first step: the tests again, then the review.
    The implementer is dispatched once. And the review never runs on red tests:
    in `tests-red` it runs once, after the repair turned them green."""
    it = await item_on(default_chain, "implementation")
    script.plan = {red: ["failed", "done"]}

    assert await _walk(it, default_chain, "implementation") == "awaiting_gate"
    assert script.calls == [*calls, "author"]
    assert gates.pending_gate(it.database, it.id) == "local_review"
    [cycle] = it.events("fix_cycle_started")
    assert cycle["payload"]["node_id"] == "verification"


async def test_the_pre_draft_gate_shows_the_work_brief_the_node_before_it_wrote(
    item_on, script, fake_agent, run_dirs
):
    """`local_review` declares `artifact: work_brief`, and the `work_brief`
    node's author is contracted to write exactly that document -- run for real
    here, on the fake agent, so the file is the one the contract names."""
    chain = TemplateLibrary.from_yaml_dir(fake_agent.templates).resolve_chain("default")
    it = await item_on(chain, "work_brief")
    script.real = {"author"}

    assert await _walk(it, chain, "work_brief") == "awaiting_gate"
    assert gates.pending_gate(it.database, it.id) == "local_review"
    brief = f".engineering/work_briefs/{it.id}.md"
    assert gates.gate_artifact(run_dirs, it.row(), "local_review") == brief


async def _approved(it, gate: str) -> None:
    await it.database.write(lambda c: store.request_gate(c, it.id, gate, gate))
    await it.database.write(lambda c: store.approve_gate(c, it.id, gate))


def _moves_base_once(it):
    moved = []

    async def effect(_row):
        if not moved:
            moved.append(True)
            await it.database.write(lambda c: store.set_base_ref(c, it.id, "1" * 40))

    return effect


@pytest.mark.parametrize(
    ("reopens", "tail", "stops_at"),
    [
        (False, ["open", "await_ci", "await_review", "author"], "chain_review"),
        (True, [], "local_review"),
    ],
    ids=["approved-gate-passes", "approved-gate-reopens"],
)
async def test_a_rebase_in_post_draft_feedback_retests_and_rereviews_the_rebased_head(
    item_on, script, default_chain, monkeypatch, reopens, tail, stops_at
):
    """Kraft-bjw6a. A CI conflict rebased away moves the base, and
    `merge_request_feedback` declares `on_base_changed: {restart_from:
    verification}`, so the rebased head is tested and reviewed again before
    anything reads it as green.

    Both sides of `walk.RESTART_REOPENS_APPROVED_GATES`, the one decision point
    for a gate inside the restart span: an approved `local_review` is passed
    through (the default), or reopened."""
    monkeypatch.setattr(walk, "RESTART_REOPENS_APPROVED_GATES", reopens)
    it = await item_on(default_chain, "merge_request_feedback")
    await _approved(it, "local_review")
    script.effects = {"await_ci": _moves_base_once(it)}

    assert await _walk(it, default_chain, "merge_request_feedback") == "awaiting_gate"
    assert script.calls == [
        "await_ci",
        "await_review",
        "test_changed_scopes",
        "code_review",
        "author",
        *tail,
    ]
    [restart] = it.events("base_change_restart")
    assert restart["payload"]["restart_from"] == "verification"
    assert gates.pending_gate(it.database, it.id) == stops_at
    requested = [e["payload"]["gate"] for e in it.events("gate_requested")]
    assert requested == ["local_review", stops_at]
