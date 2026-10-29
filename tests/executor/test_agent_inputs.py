"""`AgentTask.inputs`: what Kraft hands an agent task beyond its prompt, only
when the task declares it -- the review package (Ruling 47), whose first
consumer is the seeded fix-loop judge and the in-loop code review the second,
and a reviewer's continuity: its last round's findings and its own last
session."""

import json

import pytest
from support.harness import commit_all, entry_of, v1_named_chain, v1_node, v1_task, write

from kraft import events, executor
from kraft.executor import dispatch, prompts
from kraft.templates.environment import WorkItemTarget
from kraft.templates.models import AgentInput

NO_SETUP = entry_of({"setup_command": ""})


def _node(**agent_fields):
    """A chain of one node, `review`, whose one task is the agent `judge`."""
    judge = v1_task("judge", kind="agent", prompt="Judge it.", **agent_fields)
    return [v1_node("review", tasks=[judge])]


@pytest.mark.parametrize("declared", [True, False], ids=["declared", "undeclared"])
async def test_the_review_package_reaches_only_a_task_that_declares_it(
    item_on, fake_agent, run_dirs, declared
):
    """`review-package-is-delivered-to-a-task-that-declares-it`: the package
    is written for the session and named in the launch's context, and a task
    that does not ask for it gets neither."""
    it = await item_on(_node(**({"inputs": ["review_package"]} if declared else {})))

    assert (
        await executor.run_once(
            it.database,
            it.run_dirs,
            work_item_id=it.id,
            launch=executor.LaunchContext(repo_entry=NO_SETUP),
        )
        == "completed"
    )
    [argv] = fake_agent.argv()
    assert ("$KRAFT_REVIEW_PACKAGE" in " ".join(argv)) is declared
    [session] = it.sessions("review")
    assert it.files(session["id"]).review.is_file() is declared


def test_the_seeded_fix_loop_judge_declares_the_package_and_its_method(fake_agent):
    """Ruling 47 and Ruling 73: the seeded `strict_judge` is the package's
    first consumer, and states its three-decision contract through
    `kraft:fix-loop-judge` rather than a one-line prompt."""
    chain = v1_named_chain(fake_agent.templates, "default")
    nodes = {n.id: n for n in chain.nodes}
    for node_id in ("verification", "merge_request_feedback"):
        judge = nodes[node_id].judge.task
        assert judge.inputs == [AgentInput.REVIEW_PACKAGE]
        assert judge.skill == "kraft:fix-loop-judge"


def test_the_seeded_code_review_reads_the_review_package_through_its_method(fake_agent):
    """Ruling 87: the in-loop review reads the change through the same package
    as the judge -- the whole branch first, then only what changed since its
    last completed session (Kraft-s7c04.1) -- and reviews by `kraft:code-review`.
    It is the seeded reviewing task, so it also carries its last round's
    findings and its own last session forward."""
    chain = v1_named_chain(fake_agent.templates, "default")
    [review] = next(n for n in chain.nodes if n.id == "verification").steps[1].tasks
    assert review.path == "verification.review.code_review"
    assert review.task.inputs == [
        AgentInput.REVIEW_PACKAGE,
        AgentInput.CARRIED_FINDINGS,
        AgentInput.PREVIOUS_REVIEW,
    ]
    assert review.task.skill == "kraft:code-review"


def test_no_seeded_repair_is_told_to_ignore_the_failure_it_was_sent_for(fake_agent):
    """Kraft-gl2bf. `kraft:mr-metadata-repair` says to do nothing on a code
    failure, so it rides only on the task dispatched for merge-request metadata,
    whose own prompt says so; every repair that fixes code carries no method
    that tells it to stand down. And a fix loop's repair is worded for its own
    node: verification's for tests and review findings, not for CI."""
    chain = v1_named_chain(fake_agent.templates, "default")
    nodes = {n.id: n for n in chain.nodes}
    [metadata] = nodes["merge_request_feedback"].on_failure[0].tasks
    assert metadata.task.skill == "kraft:mr-metadata-repair"
    assert "label" in metadata.task.prompt
    for node_id in ("verification", "merge_request_feedback"):
        agents = [
            t.task for s in nodes[node_id].fix_loop for t in s.tasks if t.task.kind == "agent"
        ]
        assert agents and all(a.skill is None for a in agents)
    [verification_repair] = nodes["verification"].fix_loop[0].tasks
    assert "review findings" in verification_repair.task.prompt
    assert "CI" not in verification_repair.task.prompt


async def _measured(it, node_id: str, finding) -> None:
    """A `findings_measured` for `node_id`: its last measurement found `finding`."""
    from dataclasses import asdict

    from kraft import events

    await it.database.write(
        lambda c: events.append(
            c,
            it.id,
            "findings_measured",
            {"node_id": node_id, "cycle": 0, "head_sha": None, "findings": [asdict(finding)]},
        )
    )


async def _run(it) -> None:
    launch = executor.LaunchContext(repo_entry=NO_SETUP)
    assert await executor.run_once(it.database, it.run_dirs, work_item_id=it.id, launch=launch) == (
        "completed"
    )


@pytest.mark.parametrize("declared", [True, False], ids=["declared", "undeclared"])
async def test_carried_findings_reach_only_a_task_that_declares_them(item_on, fake_agent, declared):
    """`carried-findings-are-delivered-to-a-reviewing-task`: the node's last
    measurement, each finding under its stable tag -- the tag a reworded repeat
    reports as `same_as` -- and nothing for a task that did not ask."""
    from kraft.findings import Finding

    it = await item_on(_node(**({"inputs": ["carried_findings"]} if declared else {})))
    seen = Finding("important", "the cache is never invalidated", "a.py", 3, "judge")
    await _measured(it, "review", seen)

    await _run(it)

    [prompt] = fake_agent.prompts()
    assert (f"[{seen.fingerprint}]" in prompt) is declared
    assert ("the cache is never invalidated" in prompt) is declared


@pytest.mark.parametrize("declared", [True, False], ids=["declared", "undeclared"])
async def test_a_resumed_reviewer_is_pointed_at_its_own_last_session(item_on, fake_agent, declared):
    """`continuity-note-is-delivered-to-a-resumed-reviewer`: on its second
    session a declaring task is told where its own last completed review wrote
    its result and summary."""
    it = await item_on(_node(**({"inputs": ["previous_review"]} if declared else {})))
    _, _, result = await it.session("before", "review.main.judge", "done", head_sha="abc123")
    await it.database.write(
        lambda c: c.execute(
            "UPDATE worker_sessions SET session_summary_ref = 'summaries/before.md' WHERE id = ?",
            ("before",),
        )
    )

    await _run(it)

    prompt = fake_agent.prompts()[-1]
    assert (result in prompt) is declared
    assert ("summaries/before.md" in prompt) is declared


async def test_a_first_review_is_handed_no_history(item_on, fake_agent):
    """Declared, but with no measurement and no earlier session there is
    nothing to carry: the first and most important review reads as it would
    without the inputs."""
    it = await item_on(_node(inputs=["carried_findings", "previous_review"]))

    await _run(it)

    [prompt] = fake_agent.prompts()
    assert "The last review of this node reported" not in prompt
    assert "Your own last review of this node" not in prompt


async def test_a_blind_agent_failure_is_not_described_by_its_own_prompt(item_on):
    """An agent session's recorded command is its whole argv, prompt included.
    Folded into the blind-failure finding, a prompt carrying last round's
    findings grows that finding every round and changes its fingerprint, so
    the stuck detector never sees the repeat. A subprocess keeps its command:
    that one is worth re-running."""
    from kraft.executor import dispatch

    it = await item_on(_node())
    await it.session("s1", "review.main.judge", "failed", command="claude -p 'the whole prompt'")
    node = next(n for n in it.chain.chain.nodes if n.id == "review")

    [finding], _ = dispatch.collect_findings(it.database, it.id, node, 0)

    assert "the whole prompt" not in finding.message
    assert "Confirm the fix with" not in finding.message


async def test_a_huge_carried_findings_list_cannot_break_the_launch(item_on, fake_agent, run_dirs):
    """Kraft-rmz4g. No agent input may grow a launch past the OS argv limit
    (one argument is capped at 128 KiB on Linux; the whole argv at 1 MiB on
    macOS). A reviewer carried two thousand verbose findings still launches:
    its prompt is bounded, and it is told where the whole instruction is."""
    from kraft.adapters import agent as agent_mod
    from kraft.findings import Finding

    it = await item_on(_node(inputs=["carried_findings"]))
    last = Finding("important", "the last finding in the list", "z.py", 9, "judge")
    many = [Finding("minor", f"finding {i}: " + "x" * 400, "a.py", i, "judge") for i in range(2000)]
    await _measured_many(it, "review", [*many, last])

    await _run(it)

    [argv] = fake_agent.argv()
    assert max(len(a.encode()) for a in argv) < 128 * 1024
    [prompt] = fake_agent.prompts()
    assert len(prompt.encode()) <= agent_mod.INSTRUCTION_MAX_BYTES + 1024
    [session] = it.sessions("review")
    full = it.files(session["id"]).instruction
    assert str(full) in prompt
    assert "the last finding in the list" in full.read_text()


async def _measured_many(it, node_id: str, findings) -> None:
    from dataclasses import asdict

    from kraft import events

    await it.database.write(
        lambda c: events.append(
            c,
            it.id,
            "findings_measured",
            {
                "node_id": node_id,
                "cycle": 0,
                "head_sha": None,
                "findings": [asdict(f) for f in findings],
            },
        )
    )


def _bot_node(**agent_fields):
    """A node whose review wait is followed by the agent `judge`."""
    wait = {"id": "wait", "kind": "forge", "target": "mr.automated_review"}
    judge = v1_task("judge", kind="agent", prompt="Decide.", **agent_fields)
    steps = [{"id": "settle", "tasks": [wait]}, {"id": "decide", "tasks": [judge]}]
    return [{"id": "review", "kind": "exec", "steps": steps}]


def _collection(said: bool) -> dict:
    """What a review wait left: one bot with a thread open, or with nothing to say."""
    thread = {"id": "t1", "file": "calc.py", "line": 3, "body": "unused x"}
    entry = {"reviewer": "bot:x", "finished": "activity", "check": None}
    entry |= {"summaries": [], "threads": [thread] if said else []}
    return {"head": "abc123", "round": 0, "reviewers": [entry]}


async def _waited(it, collection: dict, *, round: int = 0) -> None:
    """The review wait's session, finished, with `collection` in its folder."""
    await it.session("w0", "review.settle.wait", "done", round=round, head_sha="abc123")
    written = it.files("w0").bot_feedback
    written.parent.mkdir(parents=True, exist_ok=True)
    written.write_text(json.dumps(collection))


@pytest.mark.parametrize(
    "said, inputs, ran, delivered",
    [
        (True, ["bot_feedback"], True, True),
        (True, [], True, False),
        (False, ["bot_feedback"], False, False),
    ],
    ids=["declared", "undeclared", "empty-collection-skips"],
)
async def test_bot_feedback_is_delivered_only_when_declared(
    item_on, fake_agent, tmp_path, monkeypatch, said, inputs, ran, delivered
):
    """`bot-feedback-is-delivered-to-a-task-that-declares-it`: the collection the
    review wait left is copied for the declaring session and named in its
    launch; a collection with nothing in it starts no agent at all."""
    seen = tmp_path / "seen-env.txt"
    monkeypatch.setenv("KRAFT_FAKE_AGENT_SHELL", f'printf %s "$KRAFT_BOT_FEEDBACK" > {seen}')
    # In a person's own checkout, where a task that runs first takes the checkout.
    in_place = WorkItemTarget.for_repository("sample", in_place=True)
    it = await item_on(_bot_node(inputs=inputs), "review", target=in_place)
    await _waited(it, _collection(said))

    launch = executor.LaunchContext(repo_entry=NO_SETUP)
    assert (
        await executor.run_once(
            it.database, it.run_dirs, work_item_id=it.id, start_index=0, start_step=1, launch=launch
        )
        == "completed"
    )

    [session] = [s for s in it.sessions("review") if s["hook_point"] == "review.decide.judge"]
    assert session["status"] == "done"
    assert [("$KRAFT_BOT_FEEDBACK" in " ".join(a)) for a in fake_agent.argv()] == (
        [delivered] if ran else []
    )
    own = it.files(session["id"]).bot_feedback
    assert own.is_file() is delivered
    assert (seen.read_text() == (str(own) if delivered else "")) if ran else not seen.exists()
    if delivered:
        assert json.loads(own.read_text()) == _collection(True)
    assert len(it.events("task_marked")) == (1 if ran else 0), "nothing is taken for a skip"
    if not ran:
        assert session["head_sha"] is None, "a session that read nothing is no review of a head"


async def test_a_skipped_triage_is_not_the_previous_review_of_the_next_round(item_on, fake_agent):
    """The next round's triage is handed its last real verdict: a skip row in
    between must not stand in for it (`last_review_session` reads only a
    session at a head)."""
    it = await item_on(_bot_node(inputs=["bot_feedback", "previous_review"]), "review")
    await it.session("t0", "review.decide.judge", "done", head_sha="abc123")
    await _waited(it, _collection(False))

    await executor.run_once(
        it.database,
        it.run_dirs,
        work_item_id=it.id,
        start_index=0,
        start_step=1,
        launch=executor.LaunchContext(repo_entry=NO_SETUP),
    )

    skipped = [s for s in it.sessions("review") if s["hook_point"] == "review.decide.judge"]
    assert [s["head_sha"] for s in skipped] == ["abc123", None]
    last = prompts.last_review_session(it.database, it.id, "review.decide.judge")
    assert last["result_path"] == skipped[0]["result_path"]


@pytest.mark.parametrize(
    "ran_at_round, asked_at_round, restarted_after, found",
    [
        (0, 0, False, True),
        (0, 1, False, False),
        (0, 0, True, False),
    ],
    ids=["this-round", "stale-round-collection-ignored", "left-before-a-restart"],
)
async def test_the_collection_read_is_this_nodes_this_rounds_and_this_runs(
    item_on, ran_at_round, asked_at_round, restarted_after, found
):
    it = await item_on(_bot_node(inputs=["bot_feedback"]), "review")
    await _waited(it, _collection(True), round=ran_at_round)
    if restarted_after:
        await it.database.write(lambda c: events.append(c, it.id, "work_item_retried", {}))
    node = it.chain.chain.nodes[0]

    got = prompts.bot_feedback_source(it.database, it.run_dirs, it.id, node, asked_at_round)

    assert (got is not None) is found


@pytest.mark.parametrize("declared", [True, False], ids=["declared", "undeclared"])
async def test_the_triage_verdict_reaches_only_a_task_that_declares_it(
    item_on, fake_agent, declared
):
    """`triage-verdict-is-delivered-to-the-review-brief`: the task that writes the
    review brief is pointed at the triage's latest verdict, and any other is not."""
    brief = v1_task("write", kind="agent", prompt="Write.")
    if declared:
        brief["inputs"] = ["triage_verdict"]
    it = await item_on(
        [*_bot_node(inputs=["bot_feedback"]), v1_node("brief", tasks=[brief])], "brief"
    )
    _, _, result = await it.session("t0", "review.decide.judge", "done", head_sha="abc123")
    await it.database.write(
        lambda c: c.execute(
            "UPDATE worker_sessions SET session_summary_ref = 'summaries/t0.md' WHERE id = 't0'"
        )
    )

    await _run(it)

    [prompt] = fake_agent.prompts()
    assert (result in prompt) is declared
    assert ("summaries/t0.md" in prompt) is declared


@pytest.mark.parametrize(
    ("declared", "override"),
    [(True, None), (True, "Fix the failing check."), (False, None)],
    ids=["declared", "fix-loop-override", "undeclared"],
)
async def test_pr_rules_reach_only_a_task_that_declares_them(
    item_on, fake_agent, repo, declared, override
):
    """`pr-rules-are-pointed-at-by-path`: a task that declares `pr_rules` is told
    where the repository's committed steward skill is, a fix loop's repair too,
    and a task that does not declare it is not. The file's text never rides."""
    write(repo, ".claude/skills/steward/SKILL.md", "Never squash.\n")
    commit_all(repo, "steward")
    it = await item_on(_node(**({"inputs": ["pr_rules"]} if declared else {})))

    if override is None:
        await _run(it)
    else:
        [node] = it.chain.chain.nodes
        await dispatch.dispatch_node(
            it.database,
            it.run_dirs,
            node.steps[0].tasks[0],
            node,
            it.row(),
            it.repo,
            launch=executor.LaunchContext(repo_entry=NO_SETUP),
            instruction_override=override,
        )

    [prompt] = fake_agent.prompts()
    assert ("`.claude/skills/steward/SKILL.md`" in prompt) is declared
    assert "Never squash." not in prompt
