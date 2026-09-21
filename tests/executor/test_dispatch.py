import ast
import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from support.harness import (
    _git,
    fake_docker_bin,
    fake_harness_home,
    isolated_bd,
    make_repo,
    seed_v1_library,
    v1_chain,
    v1_item,
    v1_named_chain,
    v1_resolved,
    v1_walk,
    write_harness_profiles,
)
from support.store_fixtures import mk_item, open_db

from kraft import db, events, executor, store
from kraft.config import git_read
from kraft.executor import dispatch
from kraft.findings import JobRef
from kraft.paths import RunDirs

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FAKE_AGENT = Path(__file__).resolve().parents[1] / "support" / "fake_agent.py"


_FAKE = f"{sys.executable} {_FAKE_AGENT}"


def _quick_task(tmp_path):
    """The shipped gateless `quick-task`, its agent task on the fake agent."""
    return v1_named_chain(tmp_path / "templates", agent_command=_FAKE)


def _implementer_prompt(tmp_path) -> str:
    """The shipped implementer's own `prompt:`, which a V1 agent instruction
    leads with -- read from the chain rather than restated here."""
    return _quick_task(tmp_path).nodes[0].steps[0].tasks[0].task.prompt


def _chain(tmp_path, nodes, *, chain_id="t"):
    """`nodes` as a chain to file, with the `fake` harness an agent task names
    overlaid onto this test's `KRAFT_HOME`."""
    seed_v1_library(tmp_path / "templates", agent_command=_FAKE)
    return v1_resolved(nodes, chain_id=chain_id)


def _agent(task_id="implement", **fields):
    return {"id": task_id, "kind": "agent", "harness": "fake", "prompt": "Do it.", **fields}


def _builtin(task_id="t"):
    return {"id": task_id, "kind": "builtin", "ref": "kraft.verify_changed_test_scopes"}


def _node_of(raw_node: dict):
    """One authored V1 node, resolved -- what `measure_node` takes."""
    return v1_resolved([raw_node]).nodes[0]


def _task_of(node, task_id: str):
    return next(t for step in node.steps for t in step.tasks if t.task.id == task_id)


def _argv_lines(path: Path) -> list[list[str]]:
    return [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]


def test_attachment_note_lists_kinds_and_paths():
    note = executor.attachment_note(
        [
            {"kind": "spec", "path": ".engineering/specs/a.md"},
            {"kind": "plan", "path": ".engineering/plans/a.md"},
        ]
    )
    assert "Spec: .engineering/specs/a.md" in note
    assert "Plan: .engineering/plans/a.md" in note
    assert "do not re-plan" in note.lower()


def test_attachment_note_is_empty_without_attachments():
    assert executor.attachment_note([]) == ""


def test_dispatch_puts_the_attachment_note_after_the_title(tmp_path, monkeypatch):
    """Unit tests on attachment_note/attachments_of alone don't prove dispatch_node
    composes them correctly (wrong order, or dropping the note entirely, would
    still pass those). This drives a real agent launch and reads back the exact
    prompt sent, the way test_fix_loop asserts steer-note ordering.

    V1: an agent task's instruction leads with the task's own `prompt:`, and
    the brief (title first) follows it."""
    monkeypatch.delenv("KRAFT_FAKE_AGENT", raising=False)
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    spec_doc = repo / ".engineering" / "specs" / "a.md"
    spec_doc.parent.mkdir(parents=True, exist_ok=True)
    spec_doc.write_text("# a\n")
    prompts = tmp_path / "prompts.txt"
    monkeypatch.setenv("KRAFT_FAKE_AGENT_PROMPT_LOG", str(prompts))
    title = "make the failing test pass"

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = await executor.intake(
                database,
                rd,
                title=title,
                repo=str(repo),
                chain=_quick_task(tmp_path),
                bd_cwd=str(tracker),
                attachments=[{"kind": "spec", "path": ".engineering/specs/a.md"}],
            )
            result = await executor.run(
                database, rd, work_item_id=wid, registry=None, bd_cwd=str(tracker)
            )
            assert result == "completed"
        finally:
            await database.close()

    asyncio.run(scenario())

    sent = [p for p in prompts.read_text().split("\n\x00\n") if p.strip()]
    # quick-task's only agent dispatch is the implementation node.
    assert len(sent) == 1
    prompt = sent[0]
    assert prompt.startswith(f"{_implementer_prompt(tmp_path)}\n\n{title}")
    assert prompt.index("Spec: .engineering/specs/a.md") > prompt.index(title)
    assert "Do not re-plan." in prompt
    # The bead note (Kraft-a03) is appended after everything else, including
    # the attachment note.
    assert prompt.index("Do not re-plan.") < prompt.index("Do not run `bd close`")
    assert prompt.rstrip().endswith(executor.BEAD_NOTE.strip())


def test_dispatch_puts_the_description_after_the_title(tmp_path, monkeypatch):
    """The description is the brief; the title is a label. Both reach the agent,
    in that order. This is the assertion the whole feature exists for."""
    monkeypatch.delenv("KRAFT_FAKE_AGENT", raising=False)
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    prompts = tmp_path / "prompts.txt"
    monkeypatch.setenv("KRAFT_FAKE_AGENT_PROMPT_LOG", str(prompts))
    title = "make the failing test pass"
    description = "test_widget_totals asserts a float; the code returns Decimal."

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = await executor.intake(
                database,
                rd,
                title=title,
                description=description,
                repo=str(repo),
                chain=_quick_task(tmp_path),
                bd_cwd=str(tracker),
            )
            result = await executor.run(
                database, rd, work_item_id=wid, registry=None, bd_cwd=str(tracker)
            )
            assert result == "completed"
        finally:
            await database.close()

    asyncio.run(scenario())

    sent = [p for p in prompts.read_text().split("\n\x00\n") if p.strip()]
    assert len(sent) == 1
    assert title in sent[0]
    assert description in sent[0]
    assert sent[0].index(title) < sent[0].index(description)


def test_dispatch_without_a_description_sends_the_title_alone(tmp_path, monkeypatch):
    """No description must reproduce today's instruction exactly — no stray blank
    lines, no 'None' rendered into the prompt. V1: after the task's own
    `prompt:`, which leads every agent instruction."""
    monkeypatch.delenv("KRAFT_FAKE_AGENT", raising=False)
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    prompts = tmp_path / "prompts.txt"
    monkeypatch.setenv("KRAFT_FAKE_AGENT_PROMPT_LOG", str(prompts))
    title = "make the failing test pass"

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = await executor.intake(
                database,
                rd,
                title=title,
                repo=str(repo),
                chain=_quick_task(tmp_path),
                bd_cwd=str(tracker),
            )
            await executor.run(database, rd, work_item_id=wid, registry=None, bd_cwd=str(tracker))
        finally:
            await database.close()

    asyncio.run(scenario())

    sent = [p for p in prompts.read_text().split("\n\x00\n") if p.strip()]
    assert len(sent) == 1
    assert sent[0].strip() == f"{_implementer_prompt(tmp_path)}\n\n{title}{executor.BEAD_NOTE}"


def test_agent_instruction_tells_the_worker_not_to_close_beads(tmp_path, monkeypatch):
    """A worker at implementation time has verify, review and merge still
    ahead of it; closing the work item's own tracking bead there says the
    work is done before it is (Kraft-a03). Kraft closes it itself at chain
    completion (`beads.complete` in `kraft.executor.walk.run_once`) -- the
    instruction has to tell the worker to leave every bead, including its
    own, alone."""
    monkeypatch.delenv("KRAFT_FAKE_AGENT", raising=False)
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    prompts = tmp_path / "prompts.txt"
    monkeypatch.setenv("KRAFT_FAKE_AGENT_PROMPT_LOG", str(prompts))

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = await executor.intake(
                database,
                rd,
                title="t",
                repo=str(repo),
                chain=_quick_task(tmp_path),
                bd_cwd=str(tracker),
            )
            await executor.run(database, rd, work_item_id=wid, registry=None, bd_cwd=str(tracker))
        finally:
            await database.close()

    asyncio.run(scenario())

    sent = [p for p in prompts.read_text().split("\n\x00\n") if p.strip()]
    assert "bd close" in sent[0]
    assert "Kraft closes it automatically" in sent[0]


def test_repo_default_model_reaches_the_agent_launch(tmp_path, monkeypatch):
    """`executor.run` -> `walk.walk_node` -> `dispatch.measure_node` ->
    `dispatch.dispatch_node` must carry the launch context all the way to
    `run_agent_task`, or a repo's configured default_model silently never
    reaches the agent.

    An agent task that sets no `model:` of its own, the way the legacy fake
    binding set none: the shipped implementer names one, and a task's own
    model rightly beats the repo default."""
    monkeypatch.delenv("KRAFT_FAKE_AGENT", raising=False)
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    argv_log = tmp_path / "argv.jsonl"
    monkeypatch.setenv("KRAFT_FAKE_AGENT_ARGV_LOG", str(argv_log))
    chain = _chain(tmp_path, [{"id": "implementation", "kind": "exec", "tasks": [_agent()]}])

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = await executor.intake(
                database,
                rd,
                title="make the failing test pass",
                repo=str(repo),
                chain=chain,
                bd_cwd=str(tracker),
            )
            launch = executor.LaunchContext(
                repo_entry={"default_model": "haiku", "setup_command": ""}, steering_dir=None
            )
            result = await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=None,
                bd_cwd=str(tracker),
                launch=launch,
            )
            assert result == "completed"
        finally:
            await database.close()

    asyncio.run(scenario())

    argvs = _argv_lines(argv_log)
    assert len(argvs) == 1  # the chain's only agent dispatch is implementation
    assert argvs[0][argvs[0].index("--model") + 1] == "haiku"


def test_node_override_model_beats_item_override_beats_binding(tmp_path, monkeypatch):
    """Precedence (Kraft-df4tc design point 2): node_overrides > item-wide
    agent_overrides > the task's own model. Exercises all three tiers in one
    item so a bug that makes any two collapse into one shows up as a wrong
    --model on the wire, not a passing test."""
    monkeypatch.delenv("KRAFT_FAKE_AGENT", raising=False)
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    argv_log = tmp_path / "argv.jsonl"
    monkeypatch.setenv("KRAFT_FAKE_AGENT_ARGV_LOG", str(argv_log))

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = await executor.intake(
                database,
                rd,
                title="t",
                repo=str(repo),
                chain=_quick_task(tmp_path),
                bd_cwd=str(tracker),
            )
            await database.write(
                lambda c: store.set_agent_overrides(c, wid, json.dumps({"model": "item-model"}))
            )
            await database.write(
                lambda c: store.set_node_overrides(
                    c, wid, {"implementation": {"model": "node-model"}}
                )
            )
            result = await executor.run(
                database, rd, work_item_id=wid, registry=None, bd_cwd=str(tracker)
            )
            assert result == "completed"
        finally:
            await database.close()

    asyncio.run(scenario())

    argvs = _argv_lines(argv_log)
    assert argvs[0][argvs[0].index("--model") + 1] == "node-model"


def test_a_hook_with_its_own_skill_is_not_told_to_implement(tmp_path, monkeypatch):
    """A reviewer task carries a skill. It must not be handed the implementer's
    "follow the plan, do not re-plan" -- that framing is why the
    MR-description node ran the full test suite (Kraft-s7c04.52). V1 keys the
    two framings on the task having a `skill:`, not on a hook name."""
    monkeypatch.delenv("KRAFT_FAKE_AGENT", raising=False)
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    spec_doc = repo / ".engineering" / "specs" / "a.md"
    spec_doc.parent.mkdir(parents=True, exist_ok=True)
    spec_doc.write_text("# a\n")
    plan_doc = repo / ".engineering" / "plans" / "a.md"
    plan_doc.parent.mkdir(parents=True, exist_ok=True)
    plan_doc.write_text("# p\n")
    prompt_log = tmp_path / "prompts.txt"
    monkeypatch.setenv("KRAFT_FAKE_AGENT_PROMPT_LOG", str(prompt_log))
    chain = _chain(
        tmp_path,
        [
            {"id": "implementation", "kind": "exec", "tasks": [_agent()]},
            {
                "id": "verify",
                "kind": "exec",
                "tasks": [_agent("review", skill="kraft:code-review")],
            },
        ],
    )

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = await executor.intake(
                database,
                rd,
                title="t",
                repo=str(repo),
                chain=chain,
                bd_cwd=str(tracker),
                attachments=[
                    {"kind": "spec", "path": ".engineering/specs/a.md"},
                    {"kind": "plan", "path": ".engineering/plans/a.md"},
                ],
            )
            row = database.read(
                lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (wid,)).fetchone()
            )
            launch = executor.LaunchContext(repo_entry=None, steering_dir=None)
            impl_node, verify_node = chain.nodes
            await dispatch.dispatch_node(
                database,
                rd,
                impl_node.steps[0].tasks[0],
                impl_node,
                row,
                repo,
                launch=launch,
            )
            await dispatch.dispatch_node(
                database,
                rd,
                verify_node.steps[0].tasks[0],
                verify_node,
                row,
                repo,
                launch=launch,
            )
        finally:
            await database.close()

    asyncio.run(scenario())

    sent = [p for p in prompt_log.read_text().split("\n\x00\n") if p.strip()]
    assert len(sent) == 2
    impl_prompt, review_prompt = sent

    assert "Do not re-plan." in impl_prompt
    assert "Implementing this work item is a different node's job" not in impl_prompt

    assert "Do not re-plan" not in review_prompt
    assert "Follow the documents above" not in review_prompt
    assert "Implementing this work item is a different node's job" in review_prompt
    assert "Judge the change against them." in review_prompt


def test_run_gathers_multi_task_node(tmp_path):
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    chain = _chain(
        tmp_path,
        [
            {
                "id": "work",
                "kind": "exec",
                "tasks": [
                    {"id": "a", "kind": "subprocess", "command": "true"},
                    {"id": "b", "kind": "subprocess", "command": "true"},
                ],
            }
        ],
    )

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = await executor.intake(
                database, rd, title="t", repo=str(repo), chain=chain, bd_cwd=str(tracker)
            )
            result = await executor.run(
                database, rd, work_item_id=wid, registry=None, bd_cwd=str(tracker)
            )
            assert result == "completed"
            sessions = database.read(
                lambda c: c.execute(
                    "SELECT node_id FROM worker_sessions WHERE work_item_id = ?", (wid,)
                ).fetchall()
            )
            work_sessions = [s for s in sessions if s["node_id"] == "work"]
            assert len(work_sessions) == 2
            types = [e["type"] for e in database.read(lambda c: events.read_after(c, 0, wid))]
            # One node: V1 has no `env_setup` node beside it.
            assert types.count("node_completed") == 1
        finally:
            await database.close()

    asyncio.run(scenario())


def test_the_repos_declared_env_reaches_a_test_scopes_run_task(tmp_path, monkeypatch):
    """dispatch.py calls `_subprocess.run_task` directly for each matched test
    scope, bypassing `resolve_invocation`. Left unwired, that call hands
    `run_task` a `repo_entry` it never reads, and the repo's declared `env`
    never reaches the one path whose job is to decide whether the MR is safe
    to merge (Kraft-69atv Step 4b). The call-site override
    (`PYTHONDONTWRITEBYTECODE=1`) must still win alongside it."""
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    dumped = tmp_path / "child-env.txt"
    # The real builtin -- the seeded fixture neuters it to `true`.
    chain = _chain(tmp_path, [{"id": "verify", "kind": "exec", "tasks": [_builtin()]}])

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = await executor.intake(
                database, rd, title="t", repo=str(repo), chain=chain, bd_cwd=str(tracker)
            )
            await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=None,
                bd_cwd=str(tracker),
                launch=executor.LaunchContext(
                    repo_entry={
                        "test_command": (
                            f'{sys.executable} -c "import os; '
                            f"open({str(dumped)!r}, 'w').write(repr(dict(os.environ)))\""
                        ),
                        "setup_command": "",
                        "env": {"MY_REPO": "1"},
                    },
                    steering_dir=None,
                ),
            )
        finally:
            await database.close()

    asyncio.run(scenario())
    child = ast.literal_eval(dumped.read_text())
    assert child["MY_REPO"] == "1"  # the repo's declared env reached the scope
    assert child["PYTHONDONTWRITEBYTECODE"] == "1"  # the call-site override still wins


def test_a_sandboxed_subprocess_hook_actually_runs_through_docker(tmp_path, monkeypatch):
    """A subprocess task on a repo whose entry turns sandboxing on wraps into
    `docker run` -- proven by pointing PATH at a fake `docker` that unwraps
    back to the real command. The marker file alone would not prove this: the
    real command writes it whether or not anything wrapped it, so this also
    checks the sentinel only the fake `docker` itself touches.

    V1: a task declares no sandbox of its own; the repo entry is the one
    source (`dispatch_node`'s subprocess branch, `sandbox.resolve({}, ...)`)."""
    monkeypatch.setenv("PATH", f"{fake_docker_bin(tmp_path)}:{os.environ['PATH']}")
    called = tmp_path / "docker-was-called"
    monkeypatch.setenv("FAKE_DOCKER_CALLED", str(called))
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    marker = tmp_path / "ran.txt"
    command = f"{sys.executable} -c \"open({str(marker)!r}, 'w').write('ran')\""
    chain = _chain(
        tmp_path,
        [
            {
                "id": "verify",
                "kind": "exec",
                "tasks": [{"id": "suite", "kind": "subprocess", "command": command}],
            }
        ],
    )

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = await executor.intake(
                database, rd, title="t", repo=str(repo), chain=chain, bd_cwd=str(tracker)
            )
            await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=None,
                bd_cwd=str(tracker),
                launch=executor.LaunchContext(
                    repo_entry={
                        "setup_command": "",
                        "sandbox": {"kind": "docker", "image": "kraft-worker:py"},
                    },
                    steering_dir=None,
                ),
            )
        finally:
            await database.close()

    asyncio.run(scenario())
    assert marker.read_text() == "ran"
    assert called.exists()


def _implementation_prompt(tmp_path, monkeypatch, plan_text: str) -> str:
    """The one prompt quick-task sends, for an item whose attached plan is `plan_text`."""
    monkeypatch.delenv("KRAFT_FAKE_AGENT", raising=False)
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    plan_doc = repo / ".engineering" / "plans" / "p.md"
    plan_doc.parent.mkdir(parents=True, exist_ok=True)
    plan_doc.write_text(plan_text)
    prompts = tmp_path / "prompts.txt"
    monkeypatch.setenv("KRAFT_FAKE_AGENT_PROMPT_LOG", str(prompts))

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = await executor.intake(
                database,
                rd,
                title="make the failing test pass",
                repo=str(repo),
                chain=_quick_task(tmp_path),
                bd_cwd=str(tracker),
                attachments=[{"kind": "plan", "path": ".engineering/plans/p.md"}],
            )
            await executor.run(database, rd, work_item_id=wid, registry=None, bd_cwd=str(tracker))
        finally:
            await database.close()

    asyncio.run(scenario())
    sent = [p for p in prompts.read_text().split("\n\x00\n") if p.strip()]
    assert len(sent) == 1
    return sent[0]


def test_the_implementer_is_told_to_report_progress_through_a_tasked_plan(tmp_path, monkeypatch):
    prompt = _implementation_prompt(
        tmp_path, monkeypatch, "# p\n\n## Task 1 — parse\n\n## Task 2 — serve\n"
    )
    assert "This plan has 2 tasks." in prompt
    assert "`kraft item progress K`" in prompt
    assert "(task K)" in prompt
    # after the attachment note, before the bead note that always closes the prompt
    assert (
        prompt.index("Do not re-plan.")
        < prompt.index("This plan has 2 tasks.")
        < prompt.index("Do not run `bd close`")
    )


def test_a_plan_without_task_headings_gets_no_progress_note(tmp_path, monkeypatch):
    prompt = _implementation_prompt(tmp_path, monkeypatch, "# p\n\n## Step one\n")
    assert "kraft item progress" not in prompt


def test_the_implementer_is_told_which_commands_gate_its_paths(tmp_path, monkeypatch):
    """49c0cefd's agent could run `just e2e-ci` and was never told it existed
    (Kraft-s7c04.8). The implementation prompt now carries the repo's
    path->command mapping. An agent task with a skill of its own -- a
    different job, dispatched in the same run -- must not get it
    (Kraft-s7c04.45: keyed on the task, not the node id)."""
    monkeypatch.delenv("KRAFT_FAKE_AGENT", raising=False)
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    prompt_log = tmp_path / "prompts.txt"
    monkeypatch.setenv("KRAFT_FAKE_AGENT_PROMPT_LOG", str(prompt_log))
    launch = executor.LaunchContext(
        repo_entry={
            "test_scopes": [
                {"paths": ["frontend/**"], "command": "just test-ui"},
                {"paths": ["frontend/**"], "command": "just e2e-ci"},
                {"paths": ["src/**", "tests/**"], "command": "just ci-test"},
            ]
        },
        steering_dir=None,
    )
    chain = _chain(
        tmp_path,
        [
            {"id": "implementation", "kind": "exec", "tasks": [_agent()]},
            {
                "id": "open_mr",
                "kind": "exec",
                "tasks": [_agent("describe", skill="kraft:mr-description")],
            },
        ],
    )

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = await executor.intake(
                database, rd, title="t", repo=str(repo), chain=chain, bd_cwd=str(tracker)
            )
            row = database.read(
                lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (wid,)).fetchone()
            )
            for node in chain.nodes:
                await dispatch.dispatch_node(
                    database, rd, node.steps[0].tasks[0], node, row, repo, launch=launch
                )
        finally:
            await database.close()

    asyncio.run(scenario())
    sent = [p for p in prompt_log.read_text().split("\n\x00\n") if p.strip()]
    assert len(sent) == 2
    impl_prompt, other_prompt = sent
    for cmd in ("just test-ui", "just e2e-ci", "just ci-test"):
        assert cmd in impl_prompt
        assert cmd not in other_prompt


# ── per-scope result identity (Batch C·MR1 Task 1, Kraft-s7c04.9/.8/.14) ────


def test_collect_findings_reports_an_early_scope_failure_even_when_a_later_scope_passes(
    tmp_path,
):
    """The changed-test-scope builtin mints one session per scope under one
    task path (dispatch.py's identity problem, spec 2026-09-15-batch-c1-design
    §"The identity problem"). A last-wins read of the round's sessions would
    let scope 3's pass erase scope 1's real failure -- exactly the blind
    failure gap 65f3ed90 closed, and C2 regresses it without this fix."""

    async def scenario():
        database = await open_db(tmp_path)
        try:
            await mk_item(database)
            node = _node_of({"id": "verify", "kind": "exec", "tasks": [_builtin()]})
            path = node.steps[0].tasks[0].path
            failing_log = tmp_path / "scope1.log"
            failing_log.write_text("2 tests failed\n")
            rows = [
                ("s-scope-1", str(failing_log), "failed"),
                ("s-scope-2", "/dev/null", "done"),
                ("s-scope-3", "/dev/null", "done"),
            ]
            for sid, log_path, status in rows:
                await database.write(
                    lambda c, sid=sid, log_path=log_path: store.create_session(
                        c,
                        id=sid,
                        work_item_id="w1",
                        node_id="verify",
                        hook_point=path,
                        log_path=log_path,
                        result_path=str(tmp_path / f"{sid}.json"),
                        round=0,
                        head_sha="sha-a",
                        # A V1 scope session records the repo command it ran:
                        # the builtin task itself carries none.
                        command="just ci-test",
                    )
                )
                await database.write(
                    lambda c, sid=sid, status=status: store.session_exited(c, sid, status)
                )
            found, reported = dispatch.collect_findings(database, "w1", node, 0)
            return found, reported
        finally:
            await database.close()

    found, reported = asyncio.run(scenario())
    assert len(found) == 1, f"expected exactly scope 1's blind failure, got {found}"
    assert "2 tests failed" in found[0].message
    assert "just ci-test" in found[0].message
    assert reported == set()
    assert found[0].jobs == (
        JobRef(label="just ci-test", log_ref="kraft view logs w1 --session s-scope-1"),
    )


def test_collect_findings_aggregates_multiple_failing_scopes_into_one_finding(
    tmp_path,
):
    """G1 brainstorm: C2 runs every scope rather than stopping at the first
    failure, so two scopes under the changed-test-scope builtin can fail in the
    same round -- the fix agent needs one coherent notice naming both, not two
    identical-looking critical findings."""

    async def scenario():
        database = await open_db(tmp_path)
        try:
            await mk_item(database)
            node = _node_of({"id": "verify", "kind": "exec", "tasks": [_builtin()]})
            path = node.steps[0].tasks[0].path
            log_a = tmp_path / "a.log"
            log_a.write_text("test A failed\n")
            log_b = tmp_path / "b.log"
            log_b.write_text("test B failed\n")
            rows = [
                ("s-scope-1", str(log_a), "failed", "just test-ui"),
                ("s-scope-2", str(log_b), "failed", "just e2e-ci"),
            ]
            for sid, log_path, status, command in rows:
                await database.write(
                    lambda c, sid=sid, log_path=log_path, command=command: store.create_session(
                        c,
                        id=sid,
                        work_item_id="w1",
                        node_id="verify",
                        hook_point=path,
                        log_path=log_path,
                        result_path=str(tmp_path / f"{sid}.json"),
                        round=0,
                        head_sha="sha-a",
                        command=command,
                    )
                )
                await database.write(
                    lambda c, sid=sid, status=status: store.session_exited(c, sid, status)
                )
            found, reported = dispatch.collect_findings(database, "w1", node, 0)
            return found, reported
        finally:
            await database.close()

    found, reported = asyncio.run(scenario())
    assert len(found) == 1, f"expected one aggregated finding, got {found}"
    assert "just test-ui" in found[0].message
    assert "just e2e-ci" in found[0].message
    assert len(found[0].jobs) == 2
    assert reported == set()


def test_collect_findings_aggregated_fingerprint_is_stable_across_rounds(tmp_path):
    """This is the property `walk.py`'s stuck-detector actually depends on
    (`prints == previous_prints`): aggregating N failing scopes into one
    Finding shrinks how many fingerprints one round produces (2 -> 1 for the
    scenario above), which nothing at the `walk.py` level exercises directly
    in this bundle. Pinning it here instead: the same two scopes failing
    identically in two different rounds (different session ids, different
    round numbers -- the round-to-round reality) must still produce the same
    single fingerprint, or the stuck-detector's streak can never advance past
    1."""

    async def scenario():
        database = await open_db(tmp_path)
        try:
            await mk_item(database)
            node = _node_of({"id": "verify", "kind": "exec", "tasks": [_builtin()]})
            path = node.steps[0].tasks[0].path
            log_a = tmp_path / "a.log"
            log_a.write_text("test A failed\n")
            log_b = tmp_path / "b.log"
            log_b.write_text("test B failed\n")
            fingerprints = []
            for round_ in (0, 1):
                rows = [
                    (f"s-r{round_}-1", str(log_a), "failed", "just test-ui"),
                    (f"s-r{round_}-2", str(log_b), "failed", "just e2e-ci"),
                ]
                for sid, log_path, status, command in rows:
                    await database.write(
                        lambda c, sid=sid, log_path=log_path, command=command, round_=round_: (
                            store.create_session(
                                c,
                                id=sid,
                                work_item_id="w1",
                                node_id="verify",
                                hook_point=path,
                                log_path=log_path,
                                result_path=str(tmp_path / f"{sid}.json"),
                                round=round_,
                                head_sha=f"sha-{round_}",
                                command=command,
                            )
                        )
                    )
                    await database.write(
                        lambda c, sid=sid, status=status: store.session_exited(c, sid, status)
                    )
                found, _ = dispatch.collect_findings(database, "w1", node, round_)
                fingerprints.append(found[0].fingerprint)
            return fingerprints
        finally:
            await database.close()

    fp_round0, fp_round1 = asyncio.run(scenario())
    assert fp_round0 == fp_round1


def test_collect_findings_ignores_a_stale_needs_context_row_from_a_reviewer(tmp_path):
    """A reviewer that exits `needs_context` stops before `bump_counter`, so a
    resume re-enters at the same round and the same head_sha -- the stale
    `needs_context` row (no result file) now sits alongside the real row from
    the resumed pass. Unlike the changed-test-scope builtin's scope loop, an
    agent task only ever mints one *real* session per pass, so this must stay
    last-wins: reading every same-head row here would hit `_FAILING_STATUSES`
    on the stale row and mint a bogus `from_blind_failure` critical finding
    for a failure that never happened."""

    async def scenario():
        database = await open_db(tmp_path)
        try:
            await mk_item(database)
            node = _node_of({"id": "verify", "kind": "exec", "tasks": [_agent("review")]})
            path = node.steps[0].tasks[0].path
            rows = [
                ("s-stale", "/dev/null", "needs_context"),
                ("s-resumed", "/dev/null", "done"),
            ]
            for sid, log_path, status in rows:
                await database.write(
                    lambda c, sid=sid, log_path=log_path: store.create_session(
                        c,
                        id=sid,
                        work_item_id="w1",
                        node_id="verify",
                        hook_point=path,
                        log_path=log_path,
                        result_path=str(tmp_path / f"{sid}.json"),
                        round=0,
                        head_sha="sha-a",
                    )
                )
                await database.write(
                    lambda c, sid=sid, status=status: store.session_exited(c, sid, status)
                )
            found, reported = dispatch.collect_findings(database, "w1", node, 0)
            return found, reported
        finally:
            await database.close()

    found, reported = asyncio.run(scenario())
    assert found == []
    assert reported == set()


# ── test-scope selection (Kraft-9wzy) ───────────────────────────────────────


def test_matched_scopes_single_scope_covers_everything():
    scopes = [{"paths": ["**"], "command": "cmd"}]
    assert dispatch._matched_scopes(scopes, ["a.py", "b.py"]) == scopes


def test_matched_scopes_picks_only_the_scope_a_path_falls_under():
    backend = {"paths": ["src/**"], "command": "backend"}
    frontend = {"paths": ["frontend/**"], "command": "frontend"}
    assert dispatch._matched_scopes([backend, frontend], ["frontend/x.ts"]) == [frontend]


def test_matched_scopes_dedupes_and_preserves_declaration_order():
    backend = {"paths": ["src/**"], "command": "backend"}
    frontend = {"paths": ["frontend/**"], "command": "frontend"}
    changed = ["frontend/a.ts", "src/x.py", "frontend/b.ts"]
    assert dispatch._matched_scopes([backend, frontend], changed) == [backend, frontend]


def test_matched_scopes_a_path_matching_two_scopes_returns_both():
    everything = {"paths": ["**"], "command": "all"}
    frontend = {"paths": ["frontend/**"], "command": "frontend"}
    assert dispatch._matched_scopes([everything, frontend], ["frontend/x.ts"]) == [
        everything,
        frontend,
    ]


def test_matched_scopes_an_unmatched_path_fails_open_to_every_scope():
    """Under-testing is the bug this exists to close (Kraft-9wzy) — a path the
    config is silent about must never narrow what runs."""
    backend = {"paths": ["src/**"], "command": "backend"}
    frontend = {"paths": ["frontend/**"], "command": "frontend"}
    scopes = [backend, frontend]
    assert dispatch._matched_scopes(scopes, ["README.md"]) == scopes


def test_matched_scopes_an_empty_diff_fails_open_to_every_scope():
    scopes = [{"paths": ["src/**"], "command": "backend"}, {"paths": ["**"], "command": "all"}]
    assert dispatch._matched_scopes(scopes, []) == scopes


def test_matched_scopes_honours_the_exclusion_form():
    root = {"paths": ["*", "src/**", "docs/**", "!frontend/**"], "command": "root"}
    frontend = {"paths": ["frontend/**"], "command": "frontend"}
    assert dispatch._matched_scopes([root, frontend], ["frontend/x.ts"]) == [frontend]
    assert dispatch._matched_scopes([root, frontend], ["src/x.py"]) == [root]


# ── C7: incremental scope selection that cannot under-test (Kraft-s7c04.14) ─

_FRONTEND_SCOPE = {"paths": ["frontend/**"], "command": "frontend-cmd"}
_BACKEND_SCOPE = {"paths": ["backend/**"], "command": "backend-cmd"}


def _cmds(to_run: list[dict]) -> set[tuple[str, ...]]:
    return {tuple(s["cmd"]) for s in to_run}


def test_select_scopes_on_the_first_round_uses_the_whole_branch_diff(tmp_path):
    """No prior measurement for this hook -- the first round always selects
    from the full branch diff, same as before C7."""

    async def scenario():
        database = await open_db(tmp_path)
        try:
            await mk_item(database)
            repo = make_repo(tmp_path)
            base_sha = git_read(repo, "rev-parse", "HEAD")
            await database.write(lambda c: store.set_base_ref(c, "w1", base_sha))
            (repo / "frontend").mkdir()
            (repo / "frontend" / "x.txt").write_text("a")
            _git(repo, "add", "-A")
            _git(repo, "commit", "-m", "touch frontend")
            repo_entry = {"test_scopes": [_FRONTEND_SCOPE, _BACKEND_SCOPE]}
            return dispatch._select_scopes(
                database, "w1", repo, "verify", "verify.main.t", 0, repo_entry
            )
        finally:
            await database.close()

    to_run, _sandbox = asyncio.run(scenario())
    assert _cmds(to_run) == {("frontend-cmd",)}


def test_select_scopes_stays_incremental_after_a_clean_round(tmp_path):
    """Round 0 ran both scopes and both passed; round 1's fix touches only
    frontend. The efficiency half of C7: nothing red from last round, so
    only the scope the new diff actually touches runs (6c712ea8 ran a
    13-minute `just ci-test` for a frontend-only fix)."""

    async def scenario():
        database = await open_db(tmp_path)
        try:
            await mk_item(database)
            repo = make_repo(tmp_path)
            base_sha = git_read(repo, "rev-parse", "HEAD")
            await database.write(lambda c: store.set_base_ref(c, "w1", base_sha))

            (repo / "frontend").mkdir()
            (repo / "frontend" / "x.txt").write_text("a")
            (repo / "backend").mkdir()
            (repo / "backend" / "y.txt").write_text("a")
            _git(repo, "add", "-A")
            _git(repo, "commit", "-m", "round0 touches both")
            round0_head = git_read(repo, "rev-parse", "HEAD")

            for sid in ("r0-frontend", "r0-backend"):
                await database.write(
                    lambda c, sid=sid: store.create_session(
                        c,
                        id=sid,
                        work_item_id="w1",
                        node_id="verify",
                        hook_point="verify.main.t",
                        log_path="/l",
                        result_path="/r",
                        round=0,
                        head_sha=round0_head,
                    )
                )
                await database.write(lambda c, sid=sid: store.session_exited(c, sid, "done"))

            (repo / "frontend" / "x.txt").write_text("b")
            _git(repo, "add", "-A")
            _git(repo, "commit", "-m", "round1 fix, frontend only")
            repo_entry = {"test_scopes": [_FRONTEND_SCOPE, _BACKEND_SCOPE]}
            return dispatch._select_scopes(
                database, "w1", repo, "verify", "verify.main.t", 1, repo_entry
            )
        finally:
            await database.close()

    to_run, _sandbox = asyncio.run(scenario())
    assert _cmds(to_run) == {("frontend-cmd",)}


def test_select_scopes_reruns_everything_after_any_scope_failed_last_round(tmp_path):
    """The C2/C7 interaction the spec calls out by name: round 0 fails
    backend and passes frontend, with frontend's row created *last* -- the
    same per-scope identity problem Task 1 fixed in `collect_findings`/
    `reusable_session`, now showing up in `prompts._last_review_session`'s own
    "most recent row" query once C7 reuses it for a multi-session hook. Round
    1's fix touches only frontend. Naive incremental selection would let a
    passing last-created row mark round 0 as fully reviewed and pick only
    frontend for round 1, leaving the backend failure unverified and
    un-reported forever. The union rule: a round following any failure at
    this hook does not trust the incremental diff and runs the full scope
    set again."""

    async def scenario():
        database = await open_db(tmp_path)
        try:
            await mk_item(database)
            repo = make_repo(tmp_path)
            base_sha = git_read(repo, "rev-parse", "HEAD")
            await database.write(lambda c: store.set_base_ref(c, "w1", base_sha))

            (repo / "frontend").mkdir()
            (repo / "frontend" / "x.txt").write_text("a")
            (repo / "backend").mkdir()
            (repo / "backend" / "y.txt").write_text("a")
            _git(repo, "add", "-A")
            _git(repo, "commit", "-m", "round0 touches both")
            round0_head = git_read(repo, "rev-parse", "HEAD")

            # backend's row is created first and fails; frontend's is created
            # last and passes -- the mixed shape a bare "last row wins" read
            # gets wrong.
            for sid, status in (("r0-backend", "failed"), ("r0-frontend", "done")):
                await database.write(
                    lambda c, sid=sid: store.create_session(
                        c,
                        id=sid,
                        work_item_id="w1",
                        node_id="verify",
                        hook_point="verify.main.t",
                        log_path="/l",
                        result_path="/r",
                        round=0,
                        head_sha=round0_head,
                    )
                )
                await database.write(
                    lambda c, sid=sid, status=status: store.session_exited(c, sid, status)
                )

            (repo / "frontend" / "x.txt").write_text("b")
            _git(repo, "add", "-A")
            _git(repo, "commit", "-m", "round1 fix, frontend only")
            repo_entry = {"test_scopes": [_FRONTEND_SCOPE, _BACKEND_SCOPE]}
            return dispatch._select_scopes(
                database, "w1", repo, "verify", "verify.main.t", 1, repo_entry
            )
        finally:
            await database.close()

    to_run, _sandbox = asyncio.run(scenario())
    assert _cmds(to_run) == {("frontend-cmd",), ("backend-cmd",)}


def test_select_scopes_verify_round_0_ignores_a_same_head_c1_gate_dispatch(tmp_path):
    """C1 (`implementation`) and verify both dispatch `on.test.run` under the
    same hook_point. Before the review fix, verify's round 0 read the latest
    `done` row for that hook_point via `prompts._last_review_session` --
    node-blind -- and found C1's own clean gate dispatch at the same HEAD,
    so the diff since it was empty and `_matched_scopes` failed open to
    *every* scope. That is not the spec's first acceptance criterion ("the
    first round still selects from the full branch diff"): round 0 must
    ignore any other node's dispatch and always measure since `base_ref`,
    which here touches only frontend."""

    async def scenario():
        database = await open_db(tmp_path)
        try:
            await mk_item(database)
            repo = make_repo(tmp_path)
            base_sha = git_read(repo, "rev-parse", "HEAD")
            await database.write(lambda c: store.set_base_ref(c, "w1", base_sha))

            (repo / "frontend").mkdir()
            (repo / "frontend" / "x.txt").write_text("a")
            _git(repo, "add", "-A")
            _git(repo, "commit", "-m", "implementation touches frontend only")
            head = git_read(repo, "rev-parse", "HEAD")

            # C1's own gate dispatch at `implementation`, round 0, clean, at
            # the same head verify is about to measure from.
            await database.write(
                lambda c: store.create_session(
                    c,
                    id="c1-gate",
                    work_item_id="w1",
                    node_id="implementation",
                    hook_point="verify.main.t",
                    log_path="/l",
                    result_path="/r",
                    round=0,
                    head_sha=head,
                )
            )
            await database.write(lambda c: store.session_exited(c, "c1-gate", "done"))
            repo_entry = {"test_scopes": [_FRONTEND_SCOPE, _BACKEND_SCOPE]}
            return dispatch._select_scopes(
                database, "w1", repo, "verify", "verify.main.t", 0, repo_entry
            )
        finally:
            await database.close()

    to_run, _sandbox = asyncio.run(scenario())
    assert _cmds(to_run) == {("frontend-cmd",)}


def test_select_scopes_round_0_reentry_after_a_partial_failure_still_runs_the_failed_scope(
    tmp_path,
):
    """The `retry_after_cap` shape the review flagged: a prior dispatch at an
    older head failed backend and passed frontend, and the retried dispatch
    re-enters at round 0 with HEAD having since moved (a fix commit landed,
    or a human commit). Before the fix, round 0 read the latest `done` row
    for this hook_point -- frontend's, since the read never looks at its
    failed backend sibling -- and diffed only from there, so a fix touching
    only frontend's paths selected only frontend and would have gone green
    with backend still broken. Round 0 must measure the whole branch diff
    instead, which still covers backend's files."""

    async def scenario():
        database = await open_db(tmp_path)
        try:
            await mk_item(database)
            repo = make_repo(tmp_path)
            base_sha = git_read(repo, "rev-parse", "HEAD")
            await database.write(lambda c: store.set_base_ref(c, "w1", base_sha))

            (repo / "frontend").mkdir()
            (repo / "frontend" / "x.txt").write_text("a")
            (repo / "backend").mkdir()
            (repo / "backend" / "y.txt").write_text("a")
            _git(repo, "add", "-A")
            _git(repo, "commit", "-m", "touches both")
            h1 = git_read(repo, "rev-parse", "HEAD")

            for sid, status in (("backend-fail", "failed"), ("frontend-pass", "done")):
                await database.write(
                    lambda c, sid=sid: store.create_session(
                        c,
                        id=sid,
                        work_item_id="w1",
                        node_id="verify",
                        hook_point="verify.main.t",
                        log_path="/l",
                        result_path="/r",
                        round=0,
                        head_sha=h1,
                    )
                )
                await database.write(
                    lambda c, sid=sid, status=status: store.session_exited(c, sid, status)
                )

            # A fix commit lands touching only frontend before the cap's
            # counter reset re-enters at round 0.
            (repo / "frontend" / "x.txt").write_text("b")
            _git(repo, "add", "-A")
            _git(repo, "commit", "-m", "fix, frontend only")
            repo_entry = {"test_scopes": [_FRONTEND_SCOPE, _BACKEND_SCOPE]}
            return dispatch._select_scopes(
                database, "w1", repo, "verify", "verify.main.t", 0, repo_entry
            )
        finally:
            await database.close()

    to_run, _sandbox = asyncio.run(scenario())
    assert _cmds(to_run) == {("frontend-cmd",), ("backend-cmd",)}


async def _dispatch_scopes(tmp_path, repo, scopes: list[dict]):
    """The changed-test-scope builtin, dispatched once on a branch that touched
    `frontend/`, with `scopes` as the repo's own test scopes."""
    rd = RunDirs(tmp_path / "run").ensure()
    database = await db.Database.open(rd.db)
    try:
        chain = v1_chain([{"id": "verify", "kind": "exec", "tasks": [_builtin()]}], repo=repo)
        await v1_item(database, chain, repo=repo)
        base_sha = git_read(repo, "rev-parse", "HEAD")
        (repo / "frontend").mkdir()
        (repo / "frontend" / "x.txt").write_text("hi")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "touch frontend")
        await database.write(lambda c: store.set_base_ref(c, "w1", base_sha))
        row = database.read(lambda c: c.execute("SELECT * FROM work_items").fetchone())
        node = chain.chain.nodes[0]
        return await dispatch.dispatch_node(
            database,
            rd,
            node.steps[0].tasks[0],
            node,
            row,
            repo,
            launch=executor.LaunchContext(repo_entry={"test_scopes": scopes}, steering_dir=None),
        )
    finally:
        await database.close()


def test_dispatch_runs_every_matched_scope_even_after_an_earlier_failure(tmp_path, monkeypatch):
    """C2 (Kraft-s7c04.9): two overlapping scopes, the changed path matches
    both -- an earlier scope's failure must not stop a later one from
    running at all. Real subprocess dispatch, not a fake's own status."""
    repo = make_repo(tmp_path)
    marker1 = tmp_path / "frontend-ran.txt"
    marker2 = tmp_path / "root-ran.txt"

    fail_script = tmp_path / "fail.py"
    fail_script.write_text(
        f"import pathlib, sys\npathlib.Path({str(marker1)!r}).write_text('ran')\nsys.exit(1)\n"
    )
    succeed_script = tmp_path / "succeed.py"
    succeed_script.write_text(f"import pathlib\npathlib.Path({str(marker2)!r}).write_text('ran')\n")

    status = asyncio.run(
        _dispatch_scopes(
            tmp_path,
            repo,
            [
                {"paths": ["frontend/**"], "command": f"{sys.executable} {fail_script}"},
                {"paths": ["**"], "command": f"{sys.executable} {succeed_script}"},
            ],
        )
    )
    assert status == "failed"
    assert marker1.read_text() == "ran"
    assert marker2.read_text() == "ran", "a later scope must still run after an earlier failure"


def test_dispatch_aggregates_three_scopes_the_first_of_which_fails(tmp_path, monkeypatch):
    """C2 (Kraft-s7c04.9): three scopes, the first failing. All three must
    run -- the second and third are the defect this closes, "did not run"
    silently reading as a pass -- and the aggregate status still fails the
    node even though the last scope run was a pass."""
    repo = make_repo(tmp_path)
    ran = [tmp_path / f"scope{i}-ran.txt" for i in range(3)]

    def _script(path: Path, marker: Path, exit_code: int) -> None:
        path.write_text(
            f"import pathlib, sys\n"
            f"pathlib.Path({str(marker)!r}).write_text('ran')\n"
            f"sys.exit({exit_code})\n"
        )

    fail_script = tmp_path / "fail.py"
    _script(fail_script, ran[0], 1)
    ok_script_a = tmp_path / "ok_a.py"
    _script(ok_script_a, ran[1], 0)
    ok_script_b = tmp_path / "ok_b.py"
    _script(ok_script_b, ran[2], 0)

    status = asyncio.run(
        _dispatch_scopes(
            tmp_path,
            repo,
            [
                {"paths": ["**"], "command": f"{sys.executable} {fail_script}"},
                {"paths": ["**"], "command": f"{sys.executable} {ok_script_a}"},
                {"paths": ["**"], "command": f"{sys.executable} {ok_script_b}"},
            ],
        )
    )
    assert status == "failed", "the last scope's pass must not overwrite the aggregate"
    assert all(m.read_text() == "ran" for m in ran), "every scope must run, not just the first"


def test_a_steered_rerun_over_an_existing_artifact_is_framed_as_a_revision(tmp_path):
    """Kraft-bol: a rejected plan cost a full re-plan because the dispatch
    never mentioned the document the agent had already written."""
    (tmp_path / ".engineering" / "plans").mkdir(parents=True)
    (tmp_path / ".engineering" / "plans" / "w1.md").write_text("# the plan\n")

    prefix = executor.steer_prefix("plan", {"id": "w1"}, tmp_path, "task 4 has no test")

    assert ".engineering/plans/w1.md" in prefix
    assert "evise" in prefix  # "Revise that document in place"
    assert "task 4 has no test" in prefix
    assert not prefix.startswith("A human has steered this run:")


def test_a_steered_node_with_no_artifact_yet_keeps_the_plain_steer_prompt(tmp_path):
    """The branch fires on the document's existence, not on any gate name: a
    node whose artifact was never written has nothing to revise."""
    with_artifact = executor.steer_prefix("plan", {"id": "w1"}, tmp_path, "go left")
    without = executor.steer_prefix(None, {"id": "w1"}, tmp_path, "go left")

    assert with_artifact == without == "A human has steered this run: go left\n\n"


def test_agent_node_commits_what_the_worker_left_behind(tmp_path, monkeypatch):
    """Kraft-7fip. The fake agent edits calc.py and never commits it, which is
    exactly what a real worker did on work item 2506daf4: `verify` passed on the
    files on disk, then `on.mr.open` refused the dirty worktree two nodes later
    and a human had to `git commit` by hand. Kraft owns the worktree, so the
    edit must be in a commit by the time the node is done."""
    monkeypatch.delenv("KRAFT_FAKE_AGENT", raising=False)
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = await executor.intake(
                database,
                rd,
                title="make the failing test pass",
                repo=str(repo),
                chain=_quick_task(tmp_path),
                bd_cwd=str(tracker),
            )
            assert (
                await executor.run(
                    database,
                    rd,
                    work_item_id=wid,
                    registry=None,
                    bd_cwd=str(tracker),
                )
                == "completed"
            )
            return rd.worktrees / wid
        finally:
            await database.close()

    worktree = asyncio.run(scenario())

    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=worktree, capture_output=True, text=True, check=True
        ).stdout

    assert "a + b" in git("show", "HEAD:calc.py"), "the commit does not carry the edit"
    # Kraft's own session note is the one thing deliberately left behind, and it
    # is left behind in a repo that has not gitignored it too (Kraft-z8gj) --
    # anything else still uncommitted is work about to be destroyed.
    # -uall: porcelain collapses an untracked directory to its name, which would
    # hide a stranded source file sitting next to the session note.
    left = [line[3:] for line in git("status", "--porcelain", "-uall").splitlines() if line.strip()]
    assert all(p.startswith(".engineering/sessions/") for p in left), (
        f"the worker's work never reached a commit: {left}"
    )


def test_a_failed_straggler_sweep_does_not_fail_a_good_agent_run(tmp_path, monkeypatch):
    """The sweep is a courtesy, not the task. An index lock a co-task holds or
    an unset user.email would otherwise turn a successful agent run into a
    failed node — and losing the sweep only puts us back where Kraft-7fip
    found us, with the work on disk and `_assert_clean` naming it at open_mr.

    It must not vanish silently either (Kraft-hf12): a `sweep_failed` event
    is the only trail back to why that eventual `open_mr` refusal happened,
    since the exception itself only ever reached the server's own log.
    """
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)

    async def boom(*args, **kwargs):
        raise dispatch._forge.ForgeError("git commit failed: .git/index.lock exists")

    monkeypatch.setattr(dispatch._forge, "commit_stragglers", boom)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = await executor.intake(
                database,
                rd,
                title="make the failing test pass",
                repo=str(repo),
                chain=_quick_task(tmp_path),
                bd_cwd=str(tracker),
            )
            status = await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=None,
                bd_cwd=str(tracker),
            )
            evts = database.read(lambda c: events.read_after(c, 0, wid))
            return status, evts
        finally:
            await database.close()

    status, evts = asyncio.run(scenario())
    assert status == "completed"
    swept = [e for e in evts if e["type"] == "sweep_failed"]
    assert swept, "no sweep_failed event survived the swallowed ForgeError"
    assert "index.lock" in swept[0]["payload"]["error"]


def test_measure_node_reuses_a_done_session_at_the_current_head(tmp_path, monkeypatch):
    """Kraft-gl9d: a task whose session for this (node, round) already exited
    'done' against the worktree's current HEAD is not redispatched; a task
    with no matching 'done' session is."""

    async def scenario():
        database, rd, worktree, wid, row = await _setup_measure_node_scenario(tmp_path)
        try:
            head = git_read(worktree, "rev-parse", "HEAD")
            node = _node_of(
                {
                    "id": "verify",
                    "kind": "exec",
                    "tasks": [_builtin("suite"), _agent("review")],
                }
            )
            await database.write(
                lambda c: store.create_session(
                    c,
                    id="s-done",
                    work_item_id=wid,
                    node_id="verify",
                    hook_point=_task_of(node, "review").path,
                    log_path="/l",
                    result_path="/r",
                    round=0,
                    head_sha=head,
                )
            )
            await database.write(lambda c: store.session_exited(c, "s-done", "done"))

            dispatched = []

            async def fake_dispatch_node(db_, run_dirs_, task, *a, **kw):
                dispatched.append(task.task.id)
                return "failed"

            monkeypatch.setattr(dispatch, "dispatch_node", fake_dispatch_node)

            verdict, failed, excs = await dispatch.measure_node(
                database, rd, wid, node, row, worktree, round=0
            )
            # the reused task never dispatches; the other one does
            assert dispatched == ["suite"]
            assert verdict == "failed"
            assert [t.task.id for t in failed] == ["suite"]
        finally:
            await database.close()

    asyncio.run(scenario())


async def _setup_measure_node_scenario(tmp_path):
    """Shared plumbing for the task-repair tests below: a real db, a real
    worktree (dispatch_node is stubbed out, but `_head()` still reads it),
    and the row `measure_node` expects."""
    rd = RunDirs(tmp_path / "run").ensure()
    database = await db.Database.open(rd.db)
    worktree = make_repo(tmp_path)
    wid = "w1"
    await database.write(
        lambda c: store.create_work_item(
            c,
            id=wid,
            bead_id="B",
            title="t",
            repo=str(worktree),
            chain_template="x",
            chain_definition="{}",
        )
    )
    row = database.read(
        lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (wid,)).fetchone()
    )
    return database, rd, worktree, wid, row


def _two_step(*groups: list[str]) -> dict:
    """A node whose steps are `groups`, each a list of subprocess task ids."""
    return {
        "id": "n",
        "kind": "exec",
        "steps": [
            {
                "id": f"s{i}",
                "tasks": [{"id": t, "kind": "subprocess", "command": "true"} for t in group],
            }
            for i, group in enumerate(groups)
        ],
    }


def test_steps_run_in_order_and_a_failing_group_stops_the_node(tmp_path, monkeypatch):
    async def scenario():
        database, rd, worktree, wid, row = await _setup_measure_node_scenario(tmp_path)
        try:
            calls = []

            async def fake_dispatch_node(db_, run_dirs_, task, node, row_, wt, **kw):
                calls.append(task.task.id)
                return "failed" if task.task.id == "a" else "done"

            monkeypatch.setattr(dispatch, "dispatch_node", fake_dispatch_node)
            node = _node_of(_two_step(["a"], ["b"]))

            verdict, failed, excs = await dispatch.measure_node(
                database, rd, wid, node, row, worktree, round=0
            )

            assert verdict == "failed"
            assert [t.task.id for t in failed] == ["a"], "names the task that actually failed"
            assert "b" not in calls, "a later group must not run after an earlier one failed"
        finally:
            await database.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("status", ["infra", "unknown", "capped_out", "no-such-status"])
def test_an_unrecognized_task_status_fails_the_node_closed(tmp_path, monkeypatch, status):
    """Kraft-tfnjt: a status outside every known outcome stopped the step loop
    and then fell through the verdict ladder to "ok" -- a pass for a task that
    reported nothing Kraft understands, with the later step never run."""

    async def scenario():
        database, rd, worktree, wid, row = await _setup_measure_node_scenario(tmp_path)
        try:

            async def fake_dispatch_node(db_, run_dirs_, task, node, row_, wt, **kw):
                return status if task.task.id == "a" else "done"

            monkeypatch.setattr(dispatch, "dispatch_node", fake_dispatch_node)
            node = _node_of(_two_step(["a"], ["b"]))

            verdict, failed, _excs = await dispatch.measure_node(
                database, rd, wid, node, row, worktree, round=0
            )

            assert verdict == "failed", verdict
            assert [t.task.id for t in failed] == ["a"]
        finally:
            await database.close()

    asyncio.run(scenario())


def test_a_later_group_runs_only_after_the_earlier_one_finishes(tmp_path, monkeypatch):
    async def scenario():
        database, rd, worktree, wid, row = await _setup_measure_node_scenario(tmp_path)
        try:
            order = []

            async def fake_dispatch_node(db_, run_dirs_, task, node, row_, wt, **kw):
                order.append(f"start:{task.task.id}")
                await asyncio.sleep(0.01 if task.task.id == "a" else 0)
                order.append(f"end:{task.task.id}")
                return "done"

            monkeypatch.setattr(dispatch, "dispatch_node", fake_dispatch_node)
            node = _node_of(_two_step(["a"], ["b"]))

            await dispatch.measure_node(database, rd, wid, node, row, worktree, round=0)

            assert order.index("end:a") < order.index("start:b")
        finally:
            await database.close()

    asyncio.run(scenario())


def test_one_group_still_runs_concurrently(tmp_path, monkeypatch):
    """The no-regression case: the tasks in one step overlap."""

    async def scenario():
        database, rd, worktree, wid, row = await _setup_measure_node_scenario(tmp_path)
        try:
            order = []

            async def fake_dispatch_node(db_, run_dirs_, task, node, row_, wt, **kw):
                order.append(f"start:{task.task.id}")
                await asyncio.sleep(0.01 if task.task.id == "a" else 0)
                order.append(f"end:{task.task.id}")
                return "done"

            monkeypatch.setattr(dispatch, "dispatch_node", fake_dispatch_node)
            node = _node_of(_two_step(["a", "b"]))

            await dispatch.measure_node(database, rd, wid, node, row, worktree, round=0)

            assert order.index("start:b") < order.index("end:a"), "overlapped, not serialized"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_measure_node_reads_head_once_per_task_not_once_per_node(tmp_path, monkeypatch):
    """Kraft-37myi: the node-entry snapshot is wrong the moment anything
    dispatched inside the node moves HEAD -- a repair's commit or an ordered
    step that rebases."""

    async def scenario():
        database, rd, worktree, wid, row = await _setup_measure_node_scenario(tmp_path)
        try:
            node = _node_of(_two_step(["a", "b"]))

            reads = []
            real_git_read = dispatch._config.git_read

            def counting_git_read(path, *args, **kwargs):
                if args[:1] == ("rev-parse",):
                    reads.append(args)
                return real_git_read(path, *args, **kwargs)

            monkeypatch.setattr(dispatch._config, "git_read", counting_git_read)

            async def fake_dispatch_node(db_, run_dirs_, task, *a, **kw):
                return "done"

            monkeypatch.setattr(dispatch, "dispatch_node", fake_dispatch_node)

            await dispatch.measure_node(database, rd, wid, node, row, worktree, round=0)
            assert len(reads) == 2, f"expected one HEAD read per task, got {len(reads)}"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_unresolved_findings_steer_uses_the_latest_measurements_findings(tmp_path):
    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = "w1"
            await database.write(
                lambda c: store.create_work_item(
                    c,
                    id=wid,
                    bead_id="B",
                    title="t",
                    repo="/r",
                    chain_template="x",
                    chain_definition="{}",
                )
            )
            await database.write(
                lambda c: events.append(
                    c,
                    wid,
                    "findings_measured",
                    {
                        "node_id": "verify",
                        "cycle": 0,
                        "findings": [
                            {
                                "severity": "important",
                                "message": "missing null check",
                                "file": "a.py",
                                "line": 10,
                                "source_plugin": "reviewer",
                            }
                        ],
                        "fingerprints": ["x"],
                        "noop_hooks": [],
                    },
                )
            )
            note = dispatch.unresolved_findings_steer(database, wid, "verify", None)
            assert note is not None
            assert "missing null check" in note
            assert "a.py:10" in note
        finally:
            await database.close()

    asyncio.run(scenario())


def test_unresolved_findings_steer_is_none_with_no_measurement_yet(tmp_path):
    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = "w1"
            await database.write(
                lambda c: store.create_work_item(
                    c,
                    id=wid,
                    bead_id="B",
                    title="t",
                    repo="/r",
                    chain_template="x",
                    chain_definition="{}",
                )
            )
            assert dispatch.unresolved_findings_steer(database, wid, "verify", None) is None
        finally:
            await database.close()

    asyncio.run(scenario())


def test_unresolved_findings_steer_is_none_when_the_last_measurement_has_no_eligible_finding(
    tmp_path,
):
    """A blind task failure with nothing structured reported must fall through
    to the caller's own `last_rejection`, not seed an empty note."""

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = "w1"
            await database.write(
                lambda c: store.create_work_item(
                    c,
                    id=wid,
                    bead_id="B",
                    title="t",
                    repo="/r",
                    chain_template="x",
                    chain_definition="{}",
                )
            )
            await database.write(
                lambda c: events.append(
                    c,
                    wid,
                    "findings_measured",
                    {
                        "node_id": "verify",
                        "cycle": 0,
                        "findings": [],
                        "fingerprints": [],
                        "noop_hooks": [],
                    },
                )
            )
            assert dispatch.unresolved_findings_steer(database, wid, "verify", None) is None
        finally:
            await database.close()

    asyncio.run(scenario())


def test_unresolved_findings_steer_drops_a_finding_resolved_in_a_later_cycle(tmp_path):
    """Only the most recent measurement counts -- a finding present in an
    earlier cycle but gone from the latest one must not be re-seeded."""

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = "w1"
            await database.write(
                lambda c: store.create_work_item(
                    c,
                    id=wid,
                    bead_id="B",
                    title="t",
                    repo="/r",
                    chain_template="x",
                    chain_definition="{}",
                )
            )
            old = {
                "node_id": "verify",
                "cycle": 0,
                "findings": [
                    {
                        "severity": "important",
                        "message": "old bug",
                        "file": "a.py",
                        "line": 1,
                        "source_plugin": "reviewer",
                    }
                ],
                "fingerprints": ["old"],
                "noop_hooks": [],
            }
            new = {
                "node_id": "verify",
                "cycle": 1,
                "findings": [
                    {
                        "severity": "important",
                        "message": "new bug",
                        "file": "b.py",
                        "line": 2,
                        "source_plugin": "reviewer",
                    }
                ],
                "fingerprints": ["new"],
                "noop_hooks": [],
            }
            await database.write(lambda c: events.append(c, wid, "findings_measured", old))
            await database.write(lambda c: events.append(c, wid, "findings_measured", new))
            note = dispatch.unresolved_findings_steer(database, wid, "verify", None)
            assert "new bug" in note
            assert "old bug" not in note
        finally:
            await database.close()

    asyncio.run(scenario())


def test_a_seeded_note_is_not_attributed_to_a_human(tmp_path):
    """Kraft's own recap of the last review's unresolved findings (Kraft-7sec
    second half) travels as a `Steer` with `source="seeded"`. Both human templates
    name an author it does not have -- `_STEER_PROMPT` says "A human has
    steered this run", and over an existing artifact `_REVISE_PROMPT` says "A
    human read ... and sent it back with this note"."""
    (tmp_path / ".engineering" / "plans").mkdir(parents=True)
    (tmp_path / ".engineering" / "plans" / "w1.md").write_text("# the plan\n")
    binding = "plan"
    note = "Findings the last review of this node left unresolved:\n- [important] a.py:1 — x (cr)"

    seeded = executor.steer_prefix(binding, {"id": "w1"}, tmp_path, note, source="seeded")

    assert note in seeded
    assert "no human" in seeded
    assert "A human has steered" not in seeded
    assert "A human read" not in seeded
    # the default is unchanged: every existing caller still means a human.
    assert executor.steer_prefix(binding, {"id": "w1"}, tmp_path, note).startswith("A human read")


def test_dispatch_carries_the_notes_authorship_into_the_prompt(tmp_path, monkeypatch):
    """The wiring behind the test above: `dispatch_node` reads `Steer.source`
    off the note it takes, so a seeded steer reaches the agent framed as
    Kraft's. Without it the field exists but never reaches the prompt."""
    repo = make_repo(tmp_path)
    seen = {}

    async def fake_run_agent_task(db_, run_dirs_, **kw):
        seen["instruction"] = kw["task_instruction"]
        return "done"

    monkeypatch.setattr("kraft.executor.dispatch._agent.run_agent_task", fake_run_agent_task)
    seed_v1_library(tmp_path / "templates", agent_command=_FAKE)  # the `fake` harness

    async def scenario(source):
        from kraft.executor.context import LaunchContext

        rd = RunDirs(tmp_path / source / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            chain = v1_chain(
                [{"id": "verify", "kind": "exec", "tasks": [_agent("review")]}], repo=repo
            )
            await v1_item(database, chain, repo=repo)
            row = database.read(lambda c: c.execute("SELECT * FROM work_items").fetchone())
            node = chain.chain.nodes[0]
            await dispatch.dispatch_node(
                database,
                rd,
                node.steps[0].tasks[0],
                node,
                row,
                repo,
                steer=executor.Steer("findings left unresolved: x", source=source),
                launch=LaunchContext(repo_entry=None, steering_dir=None),
            )
        finally:
            await database.close()
        return seen["instruction"]

    seeded = asyncio.run(scenario("seeded"))
    assert "no human" in seeded
    assert "A human has steered" not in seeded

    typed = asyncio.run(scenario("human"))
    assert typed.startswith("A human has steered this run:")


def _dispatch_one_agent_node(tmp_path, monkeypatch, *, task_extra, item, node_ov, escalate):
    """One implementer dispatch with all three override tiers populated,
    returning the argv the agent was actually launched with.

    Direct `dispatch_node` rather than a whole `executor.run`: the merge under
    test lives at that one call site, and the three-tier fixture is the same
    for every field it merges."""
    monkeypatch.delenv("KRAFT_FAKE_AGENT", raising=False)
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    argv_log = tmp_path / "argv.jsonl"
    monkeypatch.setenv("KRAFT_FAKE_AGENT_ARGV_LOG", str(argv_log))
    chain = _chain(
        tmp_path, [{"id": "implementation", "kind": "exec", "tasks": [_agent(**task_extra)]}]
    )

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = await executor.intake(
                database, rd, title="t", repo=str(repo), chain=chain, bd_cwd=str(tracker)
            )
            await database.write(lambda c: store.set_agent_overrides(c, wid, json.dumps(item)))
            await database.write(
                lambda c: store.set_node_overrides(c, wid, {"implementation": node_ov})
            )
            row = database.read(
                lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (wid,)).fetchone()
            )
            node = chain.nodes[0]
            await dispatch.dispatch_node(
                database,
                rd,
                node.steps[0].tasks[0],
                node,
                row,
                repo,
                escalate=escalate,
            )
        finally:
            await database.close()

    asyncio.run(scenario())
    return _argv_lines(argv_log)[0]


def test_node_override_effort_beats_item_override_beats_binding(tmp_path, monkeypatch):
    """Spec section 6 asks for the precedence covered per field, not once for
    the whole dial: `effort` takes a different path out of
    `resolve_invocation` than `model` does (task-level only, no repo default),
    so `model` passing says nothing about this one."""
    argv = _dispatch_one_agent_node(
        tmp_path,
        monkeypatch,
        task_extra={"effort": "low"},
        item={"effort": "medium"},
        node_ov={"effort": "high"},
        escalate=False,
    )
    assert argv[argv.index("--effort") + 1] == "high"


def test_node_override_escalate_model_beats_item_override_beats_binding(tmp_path, monkeypatch):
    """The fix loop's capability bump is the third field of the dial, and the
    only one that reaches the wire as `--model` from a *different* branch of
    `resolve_invocation` (`escalate=True`). A V1 task has no `escalate_model`
    of its own, so the tiers are the item and the node."""
    argv = _dispatch_one_agent_node(
        tmp_path,
        monkeypatch,
        task_extra={"model": "task-model"},
        item={"model": "item-model", "escalate_model": "item-escalate"},
        node_ov={"model": "node-model", "escalate_model": "node-escalate"},
        escalate=True,
    )
    assert argv[argv.index("--model") + 1] == "node-escalate"


def _needs_context_node():
    """A verify node with a measuring task, a fix loop and a judge -- every
    path `needs_context_question` filters on."""
    return _node_of(
        {
            "id": "verify",
            "kind": "exec",
            "tasks": [_builtin("suite")],
            "fix_loop": {"tasks": [_agent("fix")], "judge": _agent("judge")},
        }
    )


def test_resolved_escalation_does_not_restop_the_node(tmp_path):
    """An escalation that exited needs_context must not stop the node again.

    Kraft-7itv follow-up: the judge is already excluded for this reason;
    escalation is a conversation with a human, not a measurement, and its
    question re-fires forever once answered.
    """

    async def scenario():
        database = await open_db(tmp_path)
        try:
            await mk_item(database, "wi")
            await database.write(
                lambda c: store.create_session(
                    c,
                    id="esc",
                    work_item_id="wi",
                    node_id="verify",
                    hook_point=dispatch.ESCALATION_HOOK,
                    log_path=str(tmp_path / "esc.log"),
                    result_path=str(tmp_path / "esc.json"),
                    round=0,
                )
            )
            (tmp_path / "esc.json").write_text(
                json.dumps({"status": "needs_context", "question": "which base image?"})
            )
            await database.write(lambda c: store.session_exited(c, "esc", "needs_context"))
            node = _needs_context_node()
            assert dispatch.needs_context_question(database, "wi", node, 0) is None
        finally:
            await database.close()

    asyncio.run(scenario())


def test_measurement_needs_context_still_stops_the_node(tmp_path):
    """The negative case: a real measurement question must still stop."""

    async def scenario():
        database = await open_db(tmp_path)
        try:
            await mk_item(database, "wi")
            node = _needs_context_node()
            await database.write(
                lambda c: store.create_session(
                    c,
                    id="meas",
                    work_item_id="wi",
                    node_id="verify",
                    hook_point=_task_of(node, "suite").path,
                    log_path=str(tmp_path / "meas.log"),
                    result_path=str(tmp_path / "meas.json"),
                    round=0,
                )
            )
            (tmp_path / "meas.json").write_text(
                json.dumps({"status": "needs_context", "question": "which python?"})
            )
            await database.write(lambda c: store.session_exited(c, "meas", "needs_context"))
            assert dispatch.needs_context_question(database, "wi", node, 0) == "which python?"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_reentry_is_not_stopped_by_the_previous_passs_fix_question(tmp_path):
    """A gate rejection or ci_wait poll re-entering a fix_loop node must not
    inherit the last pass's fix-task needs_context (review finding 5).

    walk_node now seeds `round` from the persisted counter, and the counter
    survives a non-resume re-entry -- so the previous pass's round-N fix row is
    still the latest for its path when the new pass takes its first
    measurement, and nothing this pass writes can displace it until it bumps to
    N+1. The unflagged call must still return the question: surfacing a fix
    task's own needs_context one iteration later is deliberate.
    """

    async def scenario():
        database = await open_db(tmp_path)
        try:
            await mk_item(database, "wi")
            node = _needs_context_node()
            await database.write(
                lambda c: store.create_session(
                    c,
                    id="fix2",
                    work_item_id="wi",
                    node_id="verify",
                    hook_point=dispatch.fix_task_paths(node)[0],
                    log_path=str(tmp_path / "fix2.log"),
                    result_path=str(tmp_path / "fix2.json"),
                    round=2,
                )
            )
            (tmp_path / "fix2.json").write_text(
                json.dumps({"status": "needs_context", "question": "which migration?"})
            )
            await database.write(lambda c: store.session_exited(c, "fix2", "needs_context"))
            assert (
                dispatch.needs_context_question(database, "wi", node, 2, first_iteration=True)
                is None
            )
            assert dispatch.needs_context_question(database, "wi", node, 2) == "which migration?"
        finally:
            await database.close()

    asyncio.run(scenario())


def _f(message, severity="critical"):
    from kraft.findings import Finding

    return Finding(severity, message, "a.py", 1, "p")


def _round(n, findings, *, fixed=True):
    return {"round": n, "findings": findings, "fix_result_path": f"/r/{n}.json" if fixed else None}


def test_regressed_fingerprints_finds_a_finding_that_came_back():
    """Kraft-s7c04.7, the dataset's one true X<->Y oscillation (e983d85c):
    f37b90d3 pinned core.hooksPath=/dev/null to close a container escape;
    verify flagged that it broke git-lfs pre-push; 7381755f unpinned it; the
    next round found the CRITICAL escape reopened. The loop had everything it
    needed to see that and could only ever look one round back."""
    history = [_round(0, [_f("escape")]), _round(1, [_f("lfs")]), _round(2, [_f("escape")])]
    assert dispatch.regressed_fingerprints(history) == [_f("escape").fingerprint]


def test_a_finding_that_merely_persisted_is_not_a_regression():
    """That is `stuck_fingerprint`'s job and it means something different:
    never fixed, rather than fixed and then broken again."""
    history = [_round(n, [_f("escape")]) for n in range(3)]
    assert dispatch.regressed_fingerprints(history) == []


def test_a_finding_that_is_gone_now_is_not_a_regression():
    """Only the latest round's findings can be one -- the question this answers
    is what the fixer is about to work on."""
    history = [_round(0, [_f("escape")]), _round(1, []), _round(2, [_f("other")])]
    assert dispatch.regressed_fingerprints(history) == []


def test_a_gap_with_no_fix_in_it_is_not_a_regression():
    """The finding vanished because a round crashed or a resume re-measured,
    not because anything fixed it -- so nothing was reverted. Same guard
    `stuck_fingerprint` applies, for the same reason."""
    history = [_round(0, [_f("escape")]), _round(1, [], fixed=False), _round(2, [_f("escape")])]
    assert dispatch.regressed_fingerprints(history) == []


def test_two_rounds_cannot_contain_a_regression():
    history = [_round(0, [_f("escape")]), _round(1, [_f("escape")])]
    assert dispatch.regressed_fingerprints(history) == []


def test_a_gate_reviewers_verdict_names_an_automated_review_as_its_author(tmp_path):
    """Kraft-s7c04.6: `review_gates` re-entered `run_once(steer=note)` with no
    source at all, so `steer_prefix` led the re-run with "A human has steered
    this run" over a note an agent wrote -- and on a `fixed` verdict, over
    commits the agent had just made in that worktree. The node re-running could
    not tell those commits from a human's, so the brief it produced told the
    human they had fixed it themselves."""
    binding = "plan"
    (tmp_path / ".engineering" / "plans").mkdir(parents=True)
    (tmp_path / ".engineering" / "plans" / "w1.md").write_text("# the plan\n")
    note = "tidied the swallowed OSError and committed it"

    out = executor.steer_prefix(binding, {"id": "w1"}, tmp_path, note, source="gate_review")

    assert note in out
    assert "No human wrote it" in out
    assert "may have committed changes in this worktree itself" in out
    assert "A human has steered" not in out
    assert "A human read" not in out


def test_the_implementer_has_no_skill_so_its_brief_stays_the_task(tmp_path):
    """Task 2 keys off `skill:`. If someone gives the shipped implementer a
    skill, every fix round silently becomes "implementing is another node's
    job" -- addressed to the node that implements."""
    implementer = v1_named_chain(tmp_path / "templates").nodes[0].steps[0].tasks[0].task
    assert implementer.skill is None


def test_a_chain_can_run_two_harnesses(tmp_path, monkeypatch):
    """The point of the whole agent-harnesses spec, exercised with no tokens:
    one node on claude, the next on codex, both reaching the same
    result-file contract."""
    monkeypatch.delenv("KRAFT_FAKE_AGENT", raising=False)
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    argv_log = tmp_path / "argv.jsonl"
    monkeypatch.setenv("KRAFT_FAKE_AGENT_ARGV_LOG", str(argv_log))
    chain = _chain(
        tmp_path,
        [
            {"id": "claude_node", "kind": "exec", "tasks": [_agent("a", harness="claude")]},
            {"id": "codex_node", "kind": "exec", "tasks": [_agent("b", harness="codex")]},
        ],
    )
    # `codex` overlaid like the fixture overlays `claude`: the bundled
    # declaration, launching the fake agent.
    bundled = (_REPO_ROOT / "src" / "kraft" / "harnesses" / "codex.yaml").read_text()
    overlay = Path(os.environ["KRAFT_HOME"]) / "templates" / "harnesses" / "codex.yaml"
    fake_codex = json.dumps([sys.executable, str(_FAKE_AGENT), "codex", "exec"])
    overlay.write_text(bundled.replace("command: [codex, exec]", f"command: {fake_codex}"))
    write_harness_profiles(overlay.parents[1], {"codex": {"provider": "codex"}})

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = await executor.intake(
                database,
                rd,
                title="make the failing test pass",
                repo=str(repo),
                chain=chain,
                bd_cwd=str(tracker),
            )
            result = await executor.run(
                database, rd, work_item_id=wid, registry=None, bd_cwd=str(tracker)
            )
            assert result == "completed"
        finally:
            await database.close()

    asyncio.run(scenario())

    # Two harnesses, two spellings, one contract.
    records = _argv_lines(argv_log)
    assert any("--append-system-prompt" in r for r in records)
    assert any(any(a.startswith("developer_instructions=") for a in r) for r in records)


def test_previous_fix_session_is_reachable_from_dispatch():
    """WI-1 needs it at review dispatch, and walk imports dispatch rather than
    the other way round -- so it cannot stay in walk."""
    assert callable(dispatch.previous_fix_session)


def test_a_non_test_subprocess_hook_runs_its_own_command():
    """Kraft-ouoqx: repo test scopes must not replace a reviewer's command."""
    from kraft import templates

    binding = {"kind": "subprocess", "command": ["my-reviewer"]}
    assert templates.with_inputs(binding, "on.review.local.run") == {}


def test_the_test_hook_still_takes_repo_scopes_without_an_inputs_key():
    from kraft import templates

    binding = {"kind": "subprocess", "command": ["uv", "run", "pytest", "-q"]}
    resolved = templates.with_inputs(binding, templates.TEST_HOOK)
    assert resolved == {"test_scopes": {"channel": "argv"}}


def test_an_explicit_inputs_table_is_authoritative():
    from kraft import templates

    binding = {"kind": "subprocess", "command": ["x"], "inputs": {}}
    assert templates.with_inputs(binding, templates.TEST_HOOK) == {}


def test_an_agent_hook_resolves_to_no_inputs():
    from kraft import templates

    binding = {"kind": "agent", "harness": "claude"}
    assert templates.with_inputs(binding, "on.review.local.run") == {}


def test_measure_node_stops_at_a_rebase_that_moved_the_base(tmp_path, monkeypatch):
    """The later groups must not run against a base the first group just moved."""
    from kraft.executor.context import BASE_MOVED

    async def scenario():
        database, rd, worktree, wid, row = await _setup_measure_node_scenario(tmp_path)
        try:
            calls = []

            async def fake_dispatch_node(db_, run_dirs_, task, node, row_, wt, **kw):
                calls.append(task.task.id)
                return BASE_MOVED if task.task.id == "a" else "done"

            monkeypatch.setattr(dispatch, "dispatch_node", fake_dispatch_node)
            node = _node_of(_two_step(["a"], ["b"]))
            verdict, failed, _ = await dispatch.measure_node(
                database, rd, wid, node, row, worktree, round=0
            )
            assert verdict == BASE_MOVED
            assert failed == []
            assert calls == ["a"]
        finally:
            await database.close()

    asyncio.run(scenario())


def test_every_status_declares_the_tier_that_handles_it():
    """One table says which tier handles each status, so a new status cannot be
    added without saying where it is handled."""
    from kraft.executor import context

    statuses = {
        v
        for k, v in vars(context).items()
        if k.isupper() and isinstance(v, str) and not k.startswith("_")
    }
    missing = sorted(statuses - set(context.SCOPE))
    assert not missing, f"statuses with no declared scope: {missing}"
    assert set(context.SCOPE.values()) <= {"advance", "task", "node", "chain", "stop"}


def test_measure_node_records_the_group_it_reached(tmp_path, monkeypatch):
    """current_node_id alone cannot say 'step 3 of 4', so every re-entry
    restarted the node."""

    async def scenario():
        database, rd, worktree, wid, row = await _setup_measure_node_scenario(tmp_path)
        try:
            seen = {}

            async def fake_dispatch_node(db_, run_dirs_, task, node, row_, wt, **kw):
                seen[task.task.id] = database.read(
                    lambda c: c.execute(
                        "SELECT current_step FROM work_items WHERE id = ?", (wid,)
                    ).fetchone()[0]
                )
                return "failed" if task.task.id == "c" else "done"

            monkeypatch.setattr(dispatch, "dispatch_node", fake_dispatch_node)
            node = _node_of(_two_step(["a"], ["b"], ["c"]))
            await dispatch.measure_node(database, rd, wid, node, row, worktree, round=0)
            assert seen == {"a": 0, "b": 1, "c": 2}
        finally:
            await database.close()

    asyncio.run(scenario())


# --- Template Schema V1: typed dispatch --------------------------------------


def _v1_task(node_id: str, step_id: str, raw: dict):
    """One authored task, resolved to `(ResolvedNode, ResolvedTask)` -- what
    `dispatch_node` takes, without a whole chain to drive."""
    from kraft.templates.models import Chain, ExecNode, ResolvedChain

    chain = Chain.model_validate(
        {
            "id": "t",
            "nodes": [{"id": node_id, "kind": "exec", "steps": [{"id": step_id, "tasks": [raw]}]}],
        }
    )
    assert isinstance(chain.nodes[0], ExecNode)
    node = ResolvedChain.from_chain(chain).nodes[0]
    return node, node.steps[0].tasks[0]


async def _dispatch_one(tmp_path, repo, raw: dict, *, repo_entry=None, wid="w1"):
    """Dispatch one typed task against a real work item row and return
    `(status, database, run_dirs, node, task)` with the database still open."""
    from kraft.executor.context import LaunchContext

    node, task = _v1_task("verify", "checks", raw)
    chain = v1_chain(
        [{"id": "verify", "kind": "exec", "steps": [{"id": "checks", "tasks": [raw]}]}], repo=repo
    )
    rd = RunDirs(tmp_path / "run").ensure()
    database = await db.Database.open(rd.db)
    await v1_item(database, chain, repo=repo, wid=wid)
    row = database.read(
        lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (wid,)).fetchone()
    )
    status = await dispatch.dispatch_node(
        database,
        rd,
        task,
        node,
        row,
        repo,
        launch=LaunchContext(repo_entry=repo_entry or {"setup_command": ""}, steering_dir=None),
    )
    return status, database, rd, node, task


def test_each_task_kind_reaches_its_own_adapter(tmp_path, monkeypatch):
    """Ruling 3: the task's *type* chooses the adapter, and the model's own
    fields -- not a looked-up binding -- are what the adapter is handed. The
    adapters are recorded rather than run: this is about which one is picked and
    with what, and each one's own behaviour has its own tests."""
    repo = make_repo(tmp_path)
    seen: dict[str, dict] = {}

    async def fake_subprocess(_db, _rd, **kw):
        seen.setdefault("subprocess", kw)
        return "done"

    async def fake_agent(_db, _rd, **kw):
        seen["agent"] = kw
        return "done"

    async def fake_forge(_db, _rd, **kw):
        seen["forge"] = kw
        return "done"

    monkeypatch.setattr(dispatch._subprocess, "run_task", fake_subprocess)
    monkeypatch.setattr(dispatch._agent, "run_agent_task", fake_agent)
    monkeypatch.setattr(dispatch._forge, "run_task", fake_forge)
    monkeypatch.setattr(dispatch._builtins, "restore_branch", lambda *a, **k: None)

    async def commit_stragglers(*_a, **_k):
        return None

    monkeypatch.setattr(dispatch._forge, "commit_stragglers", commit_stragglers)
    monkeypatch.setenv("KRAFT_HOME", str(fake_harness_home(tmp_path, [sys.executable, "-c", ""])))

    async def scenario():
        for raw, entry in (
            ({"id": "t", "kind": "subprocess", "command": "just ci-test"}, None),
            (
                {"id": "t", "kind": "builtin", "ref": "kraft.verify_changed_test_scopes"},
                {"setup_command": "", "test_command": "just test"},
            ),
            (
                {"id": "t", "kind": "agent", "harness": "fake", "prompt": "do the work"},
                None,
            ),
            ({"id": "t", "kind": "forge", "target": "mr.open_draft"}, None),
        ):
            status, database, *_ = await _dispatch_one(
                tmp_path / raw["kind"], repo, raw, repo_entry=entry
            )
            assert status == "done", raw
            await database.close()

    asyncio.run(scenario())

    assert set(seen) == {"subprocess", "agent", "forge"}
    # The subprocess task's own command, split by the adapter's boundary, not a
    # registry list. (The builtin ran through the same adapter with the repo's
    # command -- `test_changed_test_scopes_*` covers that side.)
    assert seen["subprocess"]["cmd"] == ["just", "ci-test"]
    assert seen["agent"]["harness"] == "fake"
    assert "do the work" in seen["agent"]["task_instruction"]
    # The chain writes the lifecycle point; the adapter owns which handler runs.
    assert seen["forge"]["handler"] == "open_mr"
    assert seen["forge"]["hook_point"] == "verify.checks.t"


def test_changed_test_scopes_run_all_scopes_when_nothing_matches(tmp_path):
    """`changed-test-scope-verification-selects-safely`: a changed path matching
    no configured scope, and an empty diff, both run every scope. Under-testing
    is the bug this exists to close."""
    repo = make_repo(tmp_path)
    scopes = [
        {"paths": ["src/**"], "command": "echo src"},
        {"paths": ["tests/**"], "command": "echo tests"},
    ]

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            chain = v1_chain(
                [
                    {
                        "id": "verify",
                        "kind": "exec",
                        "tasks": [
                            {
                                "id": "t",
                                "kind": "builtin",
                                "ref": "kraft.verify_changed_test_scopes",
                            }
                        ],
                    }
                ],
                repo=repo,
            )
            await v1_item(database, chain, repo=repo)
            base = git_read(repo, "rev-parse", "HEAD")
            await database.write(lambda c: store.set_base_ref(c, "w1", base))
            empty_diff, _sandbox = dispatch._select_scopes(
                database, "w1", repo, "verify", "verify.main.t", 0, {"test_scopes": scopes}
            )
            # A changed path no scope claims.
            (repo / "docs").mkdir(exist_ok=True)
            (repo / "docs" / "note.md").write_text("x\n")
            _git(repo, "add", "-A")
            _git(repo, "commit", "-m", "docs only")
            unmatched, _ = dispatch._select_scopes(
                database, "w1", repo, "verify", "verify.main.t", 0, {"test_scopes": scopes}
            )
            return empty_diff, unmatched
        finally:
            await database.close()

    empty_diff, unmatched = asyncio.run(scenario())

    assert [s["cmd"] for s in empty_diff] == [["echo", "src"], ["echo", "tests"]]
    assert [s["cmd"] for s in unmatched] == [["echo", "src"], ["echo", "tests"]]


def _scope_marker(log: Path, name: str) -> dict:
    return {
        "paths": ["**"],
        "command": f"sh -c 'echo {name}-start >> {log}; sleep 0.4; echo {name}-end >> {log}'",
    }


def test_changed_test_scopes_run_sequentially_unless_configured_parallel(tmp_path):
    """`changed-test-scope-verification-is-sequential-by-default`: the scopes
    share one worktree, so one finishes before the next starts unless the task
    asks for parallel."""
    repo = make_repo(tmp_path)

    async def scenario(execution: str, log: Path):
        entry = {
            "setup_command": "",
            "test_scopes": [_scope_marker(log, "one"), _scope_marker(log, "two")],
        }
        status, database, *_ = await _dispatch_one(
            tmp_path / execution,
            repo,
            {
                "id": "t",
                "kind": "builtin",
                "ref": "kraft.verify_changed_test_scopes",
                "execution": execution,
            },
            repo_entry=entry,
        )
        await database.close()
        return status, log.read_text().split()

    sequential_log = tmp_path / "sequential.txt"
    parallel_log = tmp_path / "parallel.txt"
    seq_status, sequential = asyncio.run(scenario("sequential", sequential_log))
    par_status, parallel = asyncio.run(scenario("parallel", parallel_log))

    assert seq_status == "done" and par_status == "done"
    assert sequential == ["one-start", "one-end", "two-start", "two-end"]
    assert set(parallel[:2]) == {"one-start", "two-start"}


def test_changed_test_scopes_report_one_aggregate_result(tmp_path):
    """`changed-test-scope-verification-aggregates-results`: every selected
    scope runs and the task reports one status -- a later scope's pass never
    hides an earlier scope's failure (C2, Kraft-s7c04.9)."""
    repo = make_repo(tmp_path)

    async def scenario():
        entry = {
            "setup_command": "",
            "test_scopes": [
                {"paths": ["**"], "command": "false"},
                {"paths": ["**"], "command": "true"},
            ],
        }
        status, database, _rd, _node, task = await _dispatch_one(
            tmp_path,
            repo,
            {"id": "t", "kind": "builtin", "ref": "kraft.verify_changed_test_scopes"},
            repo_entry=entry,
        )
        rows = database.read(
            lambda c: c.execute(
                "SELECT hook_point, status FROM worker_sessions ORDER BY created_at"
            ).fetchall()
        )
        await database.close()
        return status, [tuple(r) for r in rows], task.path

    status, rows, path = asyncio.run(scenario())

    assert status == "failed"
    # Both scopes ran, both under the task's own canonical path, and the task
    # reported once.
    assert rows == [(path, "failed"), (path, "done")]


def test_an_agent_task_contract_precedes_its_skill_and_steering(tmp_path, monkeypatch):
    """`agent-task-contract-precedes-skill-and-steering`: Kraft's own output and
    lifecycle contract is delivered first, then the selected method, then
    steering -- and a selected skill cannot displace the contract."""
    from kraft.executor.context import LaunchContext

    repo = make_repo(tmp_path)
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")
    argv_log = tmp_path / "argv.txt"
    monkeypatch.setenv("KRAFT_FAKE_AGENT_ARGV_LOG", str(argv_log))
    monkeypatch.setenv(
        "KRAFT_HOME", str(fake_harness_home(tmp_path, [sys.executable, str(_FAKE_AGENT)]))
    )
    skills = tmp_path / "skills"
    (skills / "house-method").mkdir(parents=True)
    (skills / "house-method" / "SKILL.md").write_text("THE-METHOD\n")

    async def scenario():
        node, task = _v1_task(
            "spec",
            "author",
            {
                "id": "write",
                "kind": "agent",
                "harness": "fake",
                "prompt": "Produce the specification.",
                "skill": "house-method",
                "steering": ["project-standards"],
            },
        )
        chain = v1_chain(
            [
                {
                    "id": "spec",
                    "kind": "exec",
                    "steps": [
                        {
                            "id": "author",
                            "tasks": [
                                dict(
                                    id="write",
                                    kind="agent",
                                    harness="fake",
                                    prompt="Produce the specification.",
                                    skill="house-method",
                                    steering=["project-standards"],
                                )
                            ],
                        }
                    ],
                }
            ],
            repo=repo,
            steering={"project-standards": "THE-STEERING\n"},
        )
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            await v1_item(database, chain, repo=repo)
            row = database.read(
                lambda c: c.execute("SELECT * FROM work_items WHERE id = 'w1'").fetchone()
            )
            return await dispatch.dispatch_node(
                database,
                rd,
                task,
                node,
                row,
                repo,
                launch=LaunchContext(
                    repo_entry={"setup_command": ""},
                    steering_dir=None,
                    skills_dir=skills,
                ),
            )
        finally:
            await database.close()

    status = asyncio.run(scenario())

    assert status == "done"
    argv = json.loads(argv_log.read_text().splitlines()[0])
    context = argv[argv.index("--append-system-prompt") + 1]
    assert context.index("write a short session summary") < context.index("THE-METHOD")
    assert context.index("THE-METHOD") < context.index("THE-STEERING")


def _stop_reason(evts) -> str:
    return [e for e in evts if e["type"] == "work_item_needs_human"][-1]["payload"]["reason"]


def _loop_policy(tmp_path):
    from kraft import policy

    path = tmp_path / "policy.yaml"
    path.write_text("default: { attempts: 9, wall_clock_s: 3600 }\n")
    return policy.load_policy(path)


def _unstartable_agent(task_id):
    return {
        "id": task_id,
        "kind": "agent",
        "harness": "fake",
        "prompt": "Repair it.",
        "skill": "no-such-method",
    }


def test_an_on_failure_repair_that_cannot_start_names_its_cause(tmp_path, monkeypatch):
    """Review finding 1: a loopless node's `on_failure` repair that never
    launched read "task failed ... repair [agent] (after on_failure)" -- no
    cause, and not even "could not start"."""
    repo = make_repo(tmp_path)
    monkeypatch.setenv("KRAFT_HOME", str(fake_harness_home(tmp_path, [sys.executable, "-c", ""])))
    chain = v1_chain(
        [
            {
                "id": "build",
                "kind": "exec",
                "tasks": [{"id": "check", "kind": "subprocess", "command": "false"}],
                "on_failure": {"tasks": [_unstartable_agent("repair")]},
            }
        ],
        repo=repo,
    )

    status, evts, _sessions, _row = asyncio.run(v1_walk(tmp_path, chain, repo=repo))

    assert status == "needs_human"
    reason = _stop_reason(evts)
    assert "could not start repair in node build" in reason
    assert "no-such-method" in reason


def test_a_fixer_that_cannot_start_names_its_cause_instead_of_stuck(tmp_path, monkeypatch):
    """Review finding 2: a fix-loop fixer that never launched spent a cycle and
    stopped as "stuck: 1 finding(s) unchanged" -- telling a human the fixer
    tried, when it never ran (Kraft-579: a config_error is terminal)."""
    repo = make_repo(tmp_path)
    monkeypatch.setenv("KRAFT_HOME", str(fake_harness_home(tmp_path, [sys.executable, "-c", ""])))
    chain = v1_chain(
        [
            {
                "id": "build",
                "kind": "exec",
                "tasks": [{"id": "check", "kind": "subprocess", "command": "false"}],
                "fix_loop": {"tasks": [_unstartable_agent("fixer")]},
            }
        ],
        repo=repo,
    )

    status, evts, sessions, _row = asyncio.run(
        v1_walk(tmp_path, chain, repo=repo, policy=_loop_policy(tmp_path))
    )

    assert status == "needs_human"
    reason = _stop_reason(evts)
    assert "could not start fixer in node build" in reason, reason
    assert "no-such-method" in reason
    assert [s["hook_point"].rsplit(".", 1)[-1] for s in sessions] == ["check", "fixer"]


@pytest.mark.parametrize("fix_loop", [False, True], ids=["loopless", "fix-loop"])
def test_a_forge_task_with_no_forge_names_the_remedy_on_the_card(tmp_path, fix_loop):
    """Review finding 4: an in-process task's log is Kraft's own account of
    why it failed, so the card carries it -- here the repos.yaml remedy for a
    repo with no forge recorded, which otherwise reached only the log."""
    repo = make_repo(tmp_path)
    node = {
        "id": "draft",
        "kind": "exec",
        "tasks": [{"id": "open", "kind": "forge", "target": "mr.open_draft"}],
    }
    if fix_loop:
        node["fix_loop"] = {"tasks": [{"id": "fix", "kind": "subprocess", "command": "true"}]}
    chain = v1_chain([node], repo=repo)

    status, evts, _sessions, _row = asyncio.run(
        v1_walk(tmp_path, chain, repo=repo, policy=_loop_policy(tmp_path))
    )

    assert status == "needs_human"
    reason = _stop_reason(evts)
    # A repo with no forge is a launch refused before it starts, not a failed
    # task: it stops as a config error and spends no fix cycle (Kraft-hr0xr).
    assert "could not start open in node draft" in reason, reason
    assert "no forge is recorded for this repo" in reason, reason
    assert not [e for e in evts if e["type"] == "fix_cycle_started"]


def _cycles(evts) -> int:
    return len([e for e in evts if e["type"] == "fix_cycle_started"])


def test_a_refused_agent_launch_in_a_fix_loop_node_stops_naming_its_cause(tmp_path, monkeypatch):
    """Kraft-hr0xr: `run_agent_task` refuses a launch whose merged options name a
    capability the harness does not declare -- here a repo's `deny_tools` on a
    provider without that capability. That refusal counted as a failed task,
    so the fix loop spent paid cycles relaunching into the same refusal and the
    card read "fix_loop exhausted", with the cause only in the server log."""
    repo = make_repo(tmp_path)
    monkeypatch.setenv("KRAFT_HOME", str(fake_harness_home(tmp_path, [sys.executable, "-c", ""])))
    agent = {"id": "work", "kind": "agent", "harness": "fake", "prompt": "Do it."}
    chain = v1_chain(
        [
            {
                "id": "impl",
                "kind": "exec",
                "tasks": [agent],
                "fix_loop": {"tasks": [{**agent, "id": "repair"}]},
            }
        ],
        repo=repo,
    )

    status, evts, sessions, _row = asyncio.run(
        v1_walk(
            tmp_path,
            chain,
            repo=repo,
            repo_entry={"setup_command": "", "deny_tools": ["Bash"]},
            policy=_loop_policy(tmp_path),
        )
    )

    assert status == "needs_human"
    reason = _stop_reason(evts)
    assert "could not start work in node impl" in reason, reason
    assert "deny_tools" in reason, reason
    assert _cycles(evts) == 0
    assert [s["hook_point"] for s in sessions] == ["impl.main.work"]
    assert sessions[0]["status"] == "config_error"


def test_a_subprocess_command_that_cannot_be_parsed_stops_naming_its_cause(tmp_path):
    """Kraft-hr0xr, the same invariant for a subprocess task: a command
    `shlex` cannot split never started, so it is a config error that names
    the command, not a failure a fix loop could repair."""
    repo = make_repo(tmp_path)
    chain = v1_chain(
        [
            {
                "id": "build",
                "kind": "exec",
                "tasks": [{"id": "check", "kind": "subprocess", "command": "echo 'unclosed"}],
                "fix_loop": {"tasks": [{"id": "fix", "kind": "subprocess", "command": "true"}]},
            }
        ],
        repo=repo,
    )

    status, evts, _sessions, _row = asyncio.run(
        v1_walk(tmp_path, chain, repo=repo, policy=_loop_policy(tmp_path))
    )

    assert status == "needs_human"
    reason = _stop_reason(evts)
    assert "could not start check in node build" in reason, reason
    assert "echo 'unclosed" in reason, reason
    assert _cycles(evts) == 0


def test_a_fix_loop_that_caps_out_on_a_raising_task_names_the_exception(tmp_path, monkeypatch):
    """Kraft-hr0xr: the fix loop threw the measuring pass's exceptions away, so
    a node whose task raised every cycle stopped as "exhausted after N fix
    cycle(s)" with nothing on the card about why. The loopless path already
    folds them into its reason; the loop's cap does too now."""
    from kraft import policy

    repo = make_repo(tmp_path)
    real_run_task = dispatch._subprocess.run_task

    async def boom(*a, cmd=None, **kw):
        if cmd == ["false"]:
            raise RuntimeError("the-real-cause")
        return await real_run_task(*a, cmd=cmd, **kw)

    monkeypatch.setattr(dispatch._subprocess, "run_task", boom)
    chain = v1_chain(
        [
            {
                "id": "build",
                "kind": "exec",
                "tasks": [{"id": "check", "kind": "subprocess", "command": "false"}],
                "fix_loop": {"tasks": [{"id": "fix", "kind": "subprocess", "command": "true"}]},
            }
        ],
        repo=repo,
    )
    path = tmp_path / "policy.yaml"
    path.write_text("default: { attempts: 1, wall_clock_s: 3600 }\n")

    status, evts, _sessions, _row = asyncio.run(
        v1_walk(tmp_path, chain, repo=repo, policy=policy.load_policy(path))
    )

    assert status == "needs_human"
    reason = _stop_reason(evts)
    assert "exhausted" in reason, reason
    assert "the-real-cause" in reason, reason


def test_a_config_error_stop_carries_a_bounded_cause(tmp_path, monkeypatch):
    """The card carries the cause, but never an unbounded log line: past the
    cap it is cut, and the full line stays in the session log."""
    repo = make_repo(tmp_path)
    monkeypatch.setenv("KRAFT_HOME", str(fake_harness_home(tmp_path, [sys.executable, "-c", ""])))
    skill = "no-such-method-" + "x" * 400
    chain = v1_chain(
        [
            {
                "id": "spec",
                "kind": "exec",
                "tasks": [
                    {
                        "id": "write",
                        "kind": "agent",
                        "harness": "fake",
                        "prompt": "Produce the specification.",
                        "skill": skill,
                    }
                ],
            }
        ],
        repo=repo,
    )

    _status, evts, sessions, _row = asyncio.run(v1_walk(tmp_path, chain, repo=repo))

    reason = next(e for e in evts if e["type"] == "work_item_needs_human")["payload"]["reason"]
    assert "no-such-method-" in reason and reason.endswith("…")
    assert len(reason) < 400
    assert skill in Path(sessions[0]["log_path"]).read_text()


def test_an_unloadable_selected_skill_stops_for_a_human(tmp_path, monkeypatch):
    """`selected-skill-must-be-available`: no substitute method, no launch."""
    repo = make_repo(tmp_path)
    monkeypatch.setenv("KRAFT_HOME", str(fake_harness_home(tmp_path, [sys.executable, "-c", ""])))
    chain = v1_chain(
        [
            {
                "id": "spec",
                "kind": "exec",
                "tasks": [
                    {
                        "id": "write",
                        "kind": "agent",
                        "harness": "fake",
                        "prompt": "Produce the specification.",
                        "skill": "no-such-method",
                    }
                ],
            }
        ],
        repo=repo,
    )

    status, evts, sessions, _row = asyncio.run(v1_walk(tmp_path, chain, repo=repo))

    assert status == "needs_human"
    assert [s["status"] for s in sessions] == ["config_error"]
    reason = next(e for e in evts if e["type"] == "work_item_needs_human")["payload"]["reason"]
    assert "could not start write in node spec" in reason
    assert "no-such-method" in Path(sessions[0]["log_path"]).read_text()
    # The card names the cause itself, not only the log (Round 3 of Task 6b).
    assert "no-such-method" in reason


@pytest.mark.parametrize(
    ("selected", "profiles", "why"),
    [
        # Every case selects an id that *is* an installed provider (`codex`,
        # `claude`), so a fallback onto the provider of the same name would
        # fire here -- and launch the real binary -- rather than go unnoticed.
        ("codex", {"claude": {"provider": "claude"}}, "defines no such profile"),
        ("claude", {"claude": {"provider": "claude", "enabled": False}}, "'claude' is disabled"),
        (
            "claude",
            {"claude": {"provider": "nonesuch"}},
            "provider 'nonesuch' is not an installed harness",
        ),
        ("claude", None, "cannot read/parse"),
        # A default Kraft would not pass on is refused, not dropped unread.
        (
            "claude",
            {"claude": {"provider": "claude", "defaults": {"autocompact": "50"}}},
            "does not apply",
        ),
    ],
    ids=["absent", "disabled", "unknown-provider", "no-file", "unapplied-default"],
)
def test_an_unavailable_selected_harness_stops_for_a_human(
    tmp_path, monkeypatch, selected, profiles, why
):
    """`unavailable-selected-harness-needs-human`: never silently another
    harness. Each case makes the selected profile unavailable a different
    way -- absent, disabled, on a provider this install lacks, no
    `harnesses.yaml` at all, or carrying a default Kraft cannot apply -- and
    each stops before anything launches."""
    repo = make_repo(tmp_path)
    monkeypatch.setenv("KRAFT_HOME", str(tmp_path / "empty-home"))
    templates = tmp_path / "templates"
    templates.mkdir()
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(templates))
    if profiles is not None:
        (templates / "harnesses.yaml").write_text(json.dumps({"harnesses": profiles}))
    chain = v1_chain(
        [
            {
                "id": "spec",
                "kind": "exec",
                "tasks": [
                    {
                        "id": "write",
                        "kind": "agent",
                        "harness": selected,
                        "prompt": "Produce the specification.",
                    }
                ],
            }
        ],
        repo=repo,
    )

    status, _evts, sessions, _row = asyncio.run(v1_walk(tmp_path, chain, repo=repo))

    assert status == "needs_human"
    assert [s["status"] for s in sessions] == ["config_error"]
    log = Path(sessions[0]["log_path"]).read_text()
    assert f"selects harness {selected!r}, which is not available" in log
    assert why in log, log


def test_a_typed_agent_task_reports_the_providers_own_normalized_result(tmp_path, monkeypatch):
    """`provider-owns-runtime-mechanics` (normalized task results): the status a
    typed agent task reports is the provider's own result, normalized by the
    adapter into Kraft's vocabulary and written onto the session row -- Kraft
    does not infer it from an exit code."""
    from kraft.executor.context import LaunchContext

    repo = make_repo(tmp_path)
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")
    monkeypatch.setenv("KRAFT_FAKE_AGENT_STATUS", "done_with_concerns")
    monkeypatch.setenv("KRAFT_FAKE_AGENT_CONCERNS", "the totals are still Decimal")
    monkeypatch.setenv(
        "KRAFT_HOME", str(fake_harness_home(tmp_path, [sys.executable, str(_FAKE_AGENT)]))
    )

    async def scenario():
        raw = {"id": "write", "kind": "agent", "harness": "fake", "prompt": "do the work"}
        node, task = _v1_task("spec", "author", raw)
        chain = v1_chain(
            [{"id": "spec", "kind": "exec", "steps": [{"id": "author", "tasks": [raw]}]}],
            repo=repo,
        )
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            await v1_item(database, chain, repo=repo)
            row = database.read(
                lambda c: c.execute("SELECT * FROM work_items WHERE id = 'w1'").fetchone()
            )
            status = await dispatch.dispatch_node(
                database,
                rd,
                task,
                node,
                row,
                repo,
                launch=LaunchContext(repo_entry={"setup_command": ""}, steering_dir=None),
            )
            session = database.read(
                lambda c: c.execute(
                    "SELECT hook_point, status, result_path FROM worker_sessions"
                ).fetchone()
            )
            return status, dict(session)
        finally:
            await database.close()

    status, session = asyncio.run(scenario())

    assert status == "done_with_concerns"
    assert (session["hook_point"], session["status"]) == ("spec.author.write", status)
    assert "the totals are still Decimal" in Path(session["result_path"]).read_text()


_FAKE_CLAUDE_SH = _REPO_ROOT / "fixtures" / "fake-claude.sh"

#: The shipped `project-standards` profile's instructions, as `library.yaml` has them.
_PROJECT_STANDARDS = "Keep changes focused. Run the relevant checks before finishing."


def _dispatch_seeded(tmp_path, monkeypatch, node_id, *, after_intake=None, snapshot=None):
    """Seed the shipped library, materialize its `default` chain, and run it
    from `node_id` with only each profile's `executable:` pointed at a fake.

    `templates/steering/` is removed after seeding: the product ships no
    steering file for a V1 profile, so a test that left one there would pass
    on a setup no operator has. `after_intake(templates)` runs between intake
    and dispatch; `snapshot(json_str)` rewrites the stored snapshot.

    Returns the worker sessions and the fake agent's argv log, one argument
    per line (so a multi-line prompt spans several)."""
    from kraft.executor.context import LaunchContext
    from kraft.policy import InstancePolicy, InstancePolicyInput
    from kraft.templates.environment import Repository, WorkItemTarget
    from kraft.templates.library import TemplateLibrary

    repo = make_repo(tmp_path)
    templates = seed_v1_library(tmp_path / "templates")
    shutil.rmtree(templates / "steering", ignore_errors=True)
    shipped = (_REPO_ROOT / "templates" / "harnesses.yaml").read_text()
    # The executable only: provider, enabled and defaults stay as shipped.
    (templates / "harnesses.yaml").write_text(
        shipped.replace("executable: codex", f"executable: {_FAKE_CLAUDE_SH}").replace(
            "executable: claude", f"executable: {_FAKE_CLAUDE_SH}"
        )
    )
    assert shipped.count("executable:") == 2
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(templates))
    argv_log = tmp_path / "argv.log"
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE_ARGV_LOG", str(argv_log))
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "noop")

    chain = (
        TemplateLibrary.from_yaml_dir(templates)
        .resolve_chain("default")
        .materialize(
            target=WorkItemTarget.for_repository(Repository(id="target", path=str(repo))),
            effective_policy=InstancePolicy.from_input(InstancePolicyInput.model_validate({})),
        )
    )
    start = [n.id for n in chain.chain.nodes].index(node_id)
    if snapshot is not None:
        stored = snapshot(chain.to_json())
        chain = SimpleNamespace(chain=chain.chain, to_json=lambda: stored)
    if after_intake is not None:
        after_intake(templates)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            await v1_item(database, chain, repo=repo)
            await executor.run_once(
                database,
                rd,
                work_item_id="w1",
                registry=None,
                start_index=start,
                launch=LaunchContext(
                    repo_entry={"setup_command": ""}, steering_dir=templates / "steering"
                ),
            )
            return [
                dict(r)
                for r in database.read(
                    lambda c: c.execute(
                        "SELECT * FROM worker_sessions ORDER BY created_at"
                    ).fetchall()
                )
            ]
        finally:
            await database.close()

    sessions = asyncio.run(scenario())
    return sessions, argv_log.read_text() if argv_log.exists() else ""


@pytest.mark.parametrize(
    ("node_id", "argv_marks"),
    [
        # `spec_author` sets no effort, so `codex_default`'s own `effort:
        # medium` is what reaches the provider's `model_reasoning_effort`.
        ("spec", ["exec", "--json", "model_reasoning_effort=medium"]),
        # `write_summary` sets no model, so `claude_review`'s `model: sonnet`.
        ("work_item_summary", ["-p", "--model", "sonnet"]),
    ],
    ids=["codex_default", "claude_review"],
)
def test_the_seeded_library_dispatches_through_its_real_harness_profiles(
    tmp_path, monkeypatch, node_id, argv_marks
):
    """The library an operator is seeded with names *profile* ids
    (`codex_default`, `claude_review`), which `templates/harnesses.yaml`
    defines. Dispatched unrewritten -- the task's `harness:` untouched, only the
    profile's `executable:` pointed at a fake -- each must launch its
    provider's argv with the profile's defaults, not stop at "not available".
    `seed_v1_library(agent_command=...)` rewrites every id to `fake`, which is
    why nothing caught that it never could."""
    sessions, argv = _dispatch_seeded(tmp_path, monkeypatch, node_id)

    first = sessions[0]
    assert first["status"] == "done", Path(first["log_path"]).read_text()
    for mark in argv_marks:
        assert mark in argv.split("\n"), argv


def test_the_seeded_library_steers_from_its_own_profiles_with_no_steering_file(
    tmp_path, monkeypatch
):
    """`spec.main.author` selects `steering: [project-standards]`, a profile
    `library.yaml` declares inline. With no `templates/steering/*.md` on disk
    -- which is what a fresh install has -- the profile's instructions still
    reach the agent, after the contract
    (`agent-task-contract-precedes-skill-and-steering`)."""
    sessions, argv = _dispatch_seeded(tmp_path, monkeypatch, "spec")

    first = sessions[0]
    assert (first["hook_point"], first["status"]) == ("spec.main.author", "done"), Path(
        first["log_path"]
    ).read_text()
    prompt = argv
    assert _PROJECT_STANDARDS in prompt
    assert prompt.index("Write your spec to") < prompt.index("## Project standards")


def test_editing_the_library_after_intake_does_not_change_a_running_items_steering(
    tmp_path, monkeypatch
):
    """`materialized-chain-is-immutable-work-item-input`: steering is chain
    content, frozen into the snapshot at intake. An edit to `library.yaml` --
    or a same-named file under `templates/steering/` -- after the item was
    filed reaches items filed afterwards, never this one."""

    def edit(templates):
        lib = templates / "library.yaml"
        lib.write_text(lib.read_text().replace(_PROJECT_STANDARDS, "EDITED AFTER INTAKE"))
        (templates / "steering").mkdir(exist_ok=True)
        (templates / "steering" / "project-standards.md").write_text("FILE AFTER INTAKE")

    sessions, argv = _dispatch_seeded(tmp_path, monkeypatch, "spec", after_intake=edit)

    assert sessions[0]["status"] == "done", Path(sessions[0]["log_path"]).read_text()
    prompt = argv
    assert _PROJECT_STANDARDS in prompt
    assert "EDITED AFTER INTAKE" not in prompt
    assert "FILE AFTER INTAKE" not in prompt


def test_a_snapshot_without_frozen_steering_stops_for_a_human(tmp_path, monkeypatch):
    """An item materialized before steering was frozen into its snapshot has
    names and no text. It must not run unsteered, and must not quietly take
    today's library text as if it were the intake's: it stops, saying why and
    what to do."""

    def unfrozen(raw):
        stored = json.loads(raw)
        del stored["steering"]
        return json.dumps(stored)

    sessions, argv = _dispatch_seeded(tmp_path, monkeypatch, "spec", snapshot=unfrozen)

    assert [s["status"] for s in sessions] == ["config_error"]
    assert argv == ""
    log = Path(sessions[0]["log_path"]).read_text()
    assert "project-standards" in log and "before steering was frozen" in log, log


def _capture_launches(monkeypatch) -> dict[str, str]:
    """Stand in for the process spawn only: every agent launch still goes
    through `run_agent_task`, `build_context` and `harness.build_argv`, and the
    argv it would have run is recorded by hook point, one argument per line."""
    import kraft.adapters.agent as agent_adapter

    launched: dict[str, str] = {}

    async def _spawn(_db, _rd, *, hook_point, cmd, **_kw):
        launched[hook_point] = "\n".join(cmd)
        return "done"

    monkeypatch.setattr(agent_adapter._subprocess, "run_task", _spawn)
    return launched


def _seeded_agent_launches(tmp_path, monkeypatch) -> tuple[set[str], dict[str, str]]:
    """Every agent task of every chain the shipped seed selects, materialized
    the way intake does and dispatched through `dispatch_node` on the shipped
    harness profiles. Returns the agent task paths found (as `chain:path`) and
    the argv each launch assembled."""
    from kraft.executor.context import LaunchContext
    from kraft.policy import InstancePolicy, InstancePolicyInput
    from kraft.templates.environment import Repository, WorkItemTarget
    from kraft.templates.library import TemplateLibrary
    from kraft.templates.models import AgentTask

    repo = make_repo(tmp_path)
    templates = seed_v1_library(tmp_path / "templates")
    shutil.rmtree(templates / "steering", ignore_errors=True)
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(templates))
    launched = _capture_launches(monkeypatch)
    library = TemplateLibrary.from_yaml_dir(templates)
    found: set[str] = set()
    argv: dict[str, str] = {}

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            for n, chain_id in enumerate(library.chain_ids):
                chain = library.resolve_chain(chain_id).materialize(
                    target=WorkItemTarget.for_repository(Repository(id="target", path=str(repo))),
                    effective_policy=InstancePolicy.from_input(
                        InstancePolicyInput.model_validate({})
                    ),
                )
                wid = f"w{n}"
                await v1_item(database, chain, repo=repo, wid=wid)
                row = database.read(
                    lambda c, wid=wid: c.execute(
                        "SELECT * FROM work_items WHERE id = ?", (wid,)
                    ).fetchone()
                )
                for node in chain.chain.nodes:
                    for task in node.tasks():
                        if not isinstance(task.task, AgentTask):
                            continue
                        found.add(f"{chain_id}:{task.path}")
                        launched.clear()
                        await dispatch.dispatch_node(
                            database,
                            rd,
                            task,
                            node,
                            row,
                            repo,
                            launch=LaunchContext(
                                repo_entry={"setup_command": ""},
                                steering_dir=templates / "steering",
                            ),
                        )
                        argv[f"{chain_id}:{task.path}"] = launched.get(task.path, "")
        finally:
            await database.close()

    asyncio.run(scenario())
    return found, argv


def test_every_seeded_agent_task_launches_with_the_never_signal_rule(tmp_path, monkeypatch):
    """`every-agent-launch-carries-kraft-safety-rules` (Kraft-5x93b): the legacy
    registry gave every agent the never-signal steering by default; V1 has no
    such default, so the rule is Kraft's own contract text instead. The task
    list is derived from the seed, so a new seeded agent task is covered the
    moment it exists."""
    import kraft.adapters.agent as agent_adapter

    found, argv = _seeded_agent_launches(tmp_path, monkeypatch)

    # Not vacuous: the seed was read, and its known agent tasks were launched.
    assert {
        "default:implementation.implementation.implement",
        "default:spec.main.author",
        "quick-task:implementation.main.implement",
    } <= found, found
    # The rule itself, not only its env-var hint: dropping the headline
    # sentence must go red too (Kraft-5x93b review, finding 1).
    for phrase in (
        "Never signal a process you did not start",
        "KRAFT_DAEMON_PID",
        "a question for a human",
    ):
        assert phrase in agent_adapter.SAFETY_RULES, phrase
    missing = sorted(p for p in found if agent_adapter.SAFETY_RULES not in argv[p])
    assert missing == [], missing


def test_an_operator_agent_task_with_no_skill_or_steering_gets_the_never_signal_rule(
    tmp_path, monkeypatch
):
    """The rule is not something a task opts into, so a task an operator
    writes without any steering or skill carries it all the same."""
    import kraft.adapters.agent as agent_adapter
    from kraft.executor.context import LaunchContext

    repo = make_repo(tmp_path)
    fake_harness_home(tmp_path, ["true"])
    launched = _capture_launches(monkeypatch)
    raw = {"id": "write", "kind": "agent", "harness": "fake", "prompt": "Do the thing."}
    node, task = _v1_task("work", "do", raw)
    chain = v1_chain(
        [{"id": "work", "kind": "exec", "steps": [{"id": "do", "tasks": [raw]}]}], repo=repo
    )

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            await v1_item(database, chain, repo=repo)
            row = database.read(
                lambda c: c.execute("SELECT * FROM work_items WHERE id = 'w1'").fetchone()
            )
            return await dispatch.dispatch_node(
                database,
                rd,
                task,
                node,
                row,
                repo,
                launch=LaunchContext(repo_entry={"setup_command": ""}, steering_dir=None),
            )
        finally:
            await database.close()

    assert asyncio.run(scenario()) == "done"
    assert agent_adapter.SAFETY_RULES in launched["work.do.write"]
