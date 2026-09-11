import asyncio
import json
import shlex
import sys
from pathlib import Path

import pytest
from support.harness import make_repo

from kraft import db, skill, steering, store
from kraft.adapters import agent
from kraft.adapters.subprocess import read_concerns, read_question
from kraft.paths import RunDirs

_FAKE = Path(__file__).parent / "support" / "fake_agent.py"
_REPO_ROOT = Path(__file__).resolve().parents[1]
_FAKE_CLAUDE = _REPO_ROOT / "fixtures" / "fake-claude.sh"


async def _seed(database, repo):
    await database.write(
        lambda c: store.create_work_item(
            c,
            id="w1",
            bead_id="B",
            title="make the failing test pass",
            repo=str(repo),
            chain_template="quick-task",
            chain_definition="{}",
        )
    )


def test_agent_fix_mode_patches_repo_and_never_writes_claudemd(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    (repo / "CLAUDE.md").write_text("DO NOT EDIT\n")
    monkeypatch.delenv("KRAFT_FAKE_AGENT", raising=False)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            await _seed(database, repo)
            status = await agent.run_agent_task(
                database,
                rd,
                session_id="s1",
                work_item_id="w1",
                node_id="implementation",
                hook_point="on.implementation.start",
                command=f"{sys.executable} {_FAKE}",
                title="make the failing test pass",
                task_instruction="make the failing test pass",
                repo_path=str(repo),
                cwd=repo,
            )
            assert status == "done"
            assert "a + b" in (repo / "calc.py").read_text()
            assert (repo / "CLAUDE.md").read_text() == "DO NOT EDIT\n"  # untouched
        finally:
            await database.close()

    asyncio.run(scenario())


def test_agent_error_envelope_downgrades_to_failed(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "error")

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            await _seed(database, repo)
            status = await agent.run_agent_task(
                database,
                rd,
                session_id="s2",
                work_item_id="w1",
                node_id="implementation",
                hook_point="on.implementation.start",
                command=f"{sys.executable} {_FAKE}",
                title="t",
                task_instruction="t",
                repo_path=str(repo),
                cwd=repo,
            )
            assert status == "failed"
            row = database.read(
                lambda c: c.execute("SELECT status FROM worker_sessions WHERE id='s2'").fetchone()
            )
            assert row["status"] == "failed"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_agent_writes_session_summary_and_ref_lands_in_db(tmp_path, monkeypatch):
    """04 §6: the injected prompt carries the linkage fields, the worker writes
    .engineering/sessions/<session>.md, and its ref lands on worker_sessions."""
    repo = make_repo(tmp_path)
    monkeypatch.delenv("KRAFT_FAKE_AGENT", raising=False)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            await _seed(database, repo)
            status = await agent.run_agent_task(
                database,
                rd,
                session_id="s3",
                work_item_id="w1",
                node_id="implementation",
                hook_point="on.implementation.start",
                command=f"{sys.executable} {_FAKE}",
                title="t",
                task_instruction="t",
                repo_path=str(repo),
                cwd=repo,
            )
            assert status == "done"
            row = database.read(
                lambda c: c.execute(
                    "SELECT session_summary_ref FROM worker_sessions WHERE id='s3'"
                ).fetchone()
            )
            assert row["session_summary_ref"] == ".engineering/sessions/s3.md"
            summary = (repo / ".engineering" / "sessions" / "s3.md").read_text()
            assert "work_item_ids: [w1]" in summary
            assert "node_id: implementation" in summary
            assert "hook_point: on.implementation.start" in summary
            assert "worker_session_id: s3" in summary
        finally:
            await database.close()

    asyncio.run(scenario())


def test_agent_status_knob_reports_done_with_concerns(tmp_path, monkeypatch):
    """The fake's KRAFT_FAKE_AGENT_STATUS knob round-trips through the real
    result-file resolution path (kraft.adapters.subprocess._resolve_result_file),
    landing on worker_sessions.status -- not asserted against the raw JSON the
    fake wrote, which would pass even if nothing downstream read it."""
    repo = make_repo(tmp_path)
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")
    monkeypatch.setenv("KRAFT_FAKE_AGENT_STATUS", "done_with_concerns")
    monkeypatch.setenv("KRAFT_FAKE_AGENT_CONCERNS", "tests were flaky")

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            await _seed(database, repo)
            status = await agent.run_agent_task(
                database,
                rd,
                session_id="s4",
                work_item_id="w1",
                node_id="implementation",
                hook_point="on.implementation.start",
                command=f"{sys.executable} {_FAKE}",
                title="t",
                task_instruction="t",
                repo_path=str(repo),
                cwd=repo,
            )
            assert status == "done_with_concerns"
            row = database.read(
                lambda c: c.execute(
                    "SELECT status, result_path FROM worker_sessions WHERE id='s4'"
                ).fetchone()
            )
            assert row["status"] == "done_with_concerns"
            assert read_concerns(Path(row["result_path"])) == "tests were flaky"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_agent_status_knob_reports_needs_context(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")
    monkeypatch.setenv("KRAFT_FAKE_AGENT_STATUS", "needs_context")
    monkeypatch.setenv("KRAFT_FAKE_AGENT_QUESTION", "which repo?")

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            await _seed(database, repo)
            status = await agent.run_agent_task(
                database,
                rd,
                session_id="s5",
                work_item_id="w1",
                node_id="implementation",
                hook_point="on.implementation.start",
                command=f"{sys.executable} {_FAKE}",
                title="t",
                task_instruction="t",
                repo_path=str(repo),
                cwd=repo,
            )
            assert status == "needs_context"
            row = database.read(
                lambda c: c.execute(
                    "SELECT status, result_path FROM worker_sessions WHERE id='s5'"
                ).fetchone()
            )
            assert row["status"] == "needs_context"
            assert read_question(Path(row["result_path"])) == "which repo?"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_agent_plan_scripts_status_per_invocation(tmp_path, monkeypatch):
    """KRAFT_FAKE_AGENT_PLAN drives two launches to two different statuses from
    one process's worth of env -- the mechanism Sub-project F's fake reviewer
    established, reused rather than reinvented here."""
    repo = make_repo(tmp_path)
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")
    plan = tmp_path / "agent-plan.json"
    plan.write_text(
        json.dumps(
            [
                {"status": "done_with_concerns", "concerns": "first pass"},
                {"status": "needs_context", "question": "second pass"},
            ]
        )
    )
    monkeypatch.setenv("KRAFT_FAKE_AGENT_PLAN", str(plan))

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            await _seed(database, repo)
            first = await agent.run_agent_task(
                database,
                rd,
                session_id="s6",
                work_item_id="w1",
                node_id="implementation",
                hook_point="on.implementation.start",
                command=f"{sys.executable} {_FAKE}",
                title="t",
                task_instruction="t",
                repo_path=str(repo),
                cwd=repo,
            )
            second = await agent.run_agent_task(
                database,
                rd,
                session_id="s7",
                work_item_id="w1",
                node_id="implementation",
                hook_point="on.implementation.start",
                command=f"{sys.executable} {_FAKE}",
                title="t",
                task_instruction="t",
                repo_path=str(repo),
                cwd=repo,
            )
            return first, second
        finally:
            await database.close()

    first, second = asyncio.run(scenario())
    assert first == "done_with_concerns"
    assert second == "needs_context"


def test_fake_claude_status_knob_reports_done_with_concerns(tmp_path, monkeypatch):
    """Same proof as the Python fake, against fixtures/fake-claude.sh -- the
    dev-instance stand-in, a different shape (bash, exit 3 on failure)."""
    repo = make_repo(tmp_path)
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "noop")
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE_STATUS", "done_with_concerns")
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE_CONCERNS", "tests were flaky")

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            await _seed(database, repo)
            status = await agent.run_agent_task(
                database,
                rd,
                session_id="s10",
                work_item_id="w1",
                node_id="implementation",
                hook_point="on.implementation.start",
                command=str(_FAKE_CLAUDE),
                title="t",
                task_instruction="t",
                repo_path=str(repo),
                cwd=repo,
            )
            assert status == "done_with_concerns"
            row = database.read(
                lambda c: c.execute(
                    "SELECT status, result_path FROM worker_sessions WHERE id='s10'"
                ).fetchone()
            )
            assert row["status"] == "done_with_concerns"
            assert read_concerns(Path(row["result_path"])) == "tests were flaky"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_fake_claude_status_knob_reports_needs_context(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "noop")
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE_STATUS", "needs_context")
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE_QUESTION", "which repo?")

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            await _seed(database, repo)
            status = await agent.run_agent_task(
                database,
                rd,
                session_id="s11",
                work_item_id="w1",
                node_id="implementation",
                hook_point="on.implementation.start",
                command=str(_FAKE_CLAUDE),
                title="t",
                task_instruction="t",
                repo_path=str(repo),
                cwd=repo,
            )
            assert status == "needs_context"
            row = database.read(
                lambda c: c.execute(
                    "SELECT status, result_path FROM worker_sessions WHERE id='s11'"
                ).fetchone()
            )
            assert row["status"] == "needs_context"
            assert read_question(Path(row["result_path"])) == "which repo?"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_fake_claude_status_defaults_to_done(tmp_path, monkeypatch):
    """No knob set: today's callers (e.g. test_autostart.py) are unaffected."""
    repo = make_repo(tmp_path)
    monkeypatch.delenv("KRAFT_FAKE_CLAUDE_STATUS", raising=False)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            await _seed(database, repo)
            status = await agent.run_agent_task(
                database,
                rd,
                session_id="s12",
                work_item_id="w1",
                node_id="implementation",
                hook_point="on.implementation.start",
                command=str(_FAKE_CLAUDE),
                title="t",
                task_instruction="t",
                repo_path=str(repo),
                cwd=repo,
            )
            assert status == "done"
        finally:
            await database.close()

    asyncio.run(scenario())


def _capture_cmd(monkeypatch):
    """The command line `run_agent_task` would have run."""
    seen = {}

    async def fake_run_task(db, run_dirs, *, cmd, **kw):
        seen["cmd"] = cmd
        return "done"

    monkeypatch.setattr("kraft.adapters.agent._subprocess.run_task", fake_run_task)
    return seen


def _run(**overrides):
    """Call run_agent_task with dummy plumbing args, returning its status."""
    kwargs = dict(
        db=None,
        run_dirs=None,
        session_id="s1",
        work_item_id="w1",
        node_id="implementation",
        hook_point="on.implementation.start",
        command="claude",
        title="t",
        task_instruction="do the thing",
        repo_path="/repo",
        cwd="/repo",
    )
    kwargs.update(overrides)
    return asyncio.run(agent.run_agent_task(**kwargs))


def test_the_injected_context_names_the_statuses_and_fields():
    """Sub-project G §1: the injected context is the only channel that tells an
    agent the status vocabulary exists, so it has to name all four statuses and
    both optional result-file fields, not just the ones the session-summary
    contract already mentions (`needs_context` appeared only as
    `session_summary_ref` before this)."""
    ctx = agent._CTX
    for token in ("done", "done_with_concerns", "failed", "needs_context", "concerns", "question"):
        assert token in ctx


def test_the_context_asks_for_one_question_per_stop():
    """Spec §5: needs_context costs a full stop and relaunch, so an agent that
    needs three facts must ask for all three at once rather than stopping three
    times."""
    assert "ask for all of them in that one question" in agent._CTX


def test_the_context_says_backgrounding_does_not_work():
    """Kraft-avpe: a 231-turn implementation session started a background test
    job and ended its turn to wait for it. A headless session has no next turn
    to receive that notification -- nothing told the agent so."""
    assert "there is no notification" in agent._CTX
    assert "background" in agent._CTX


def test_every_agent_node_is_told_to_commit_its_work(monkeypatch):
    """Kraft-brq. The only commit sentence used to live in `_ARTIFACT`, which
    is appended only for a binding declaring `artifact:`. `on.implementation.start`
    declares none, so the node that writes the code was never told to commit it
    and the merge request carried the spec and the plan and nothing else.
    """
    seen = _capture_cmd(monkeypatch)
    _run(artifact=None)
    prompt = _system_prompt(seen["cmd"])
    assert "Commit everything you change before you exit" in prompt
    assert "destroyed with the worktree" in prompt


def test_workers_are_told_not_to_push_or_merge(monkeypatch):
    """The other half of the contract. A worker that pushes on its own defeats
    the dirty-tree precondition on the next node and merges a head CI has never
    seen; Kraft is the only pusher (spec §1, §4)."""
    seen = _capture_cmd(monkeypatch)
    _run(artifact=None)
    assert "Do not push and do not merge" in _system_prompt(seen["cmd"])


def test_the_artifact_contract_does_not_claim_the_gate_cannot_see_uncommitted_files(monkeypatch):
    """Kraft-i47n. `_ARTIFACT` justified its commit sentence with "an
    uncommitted file is invisible to them", which is false:
    `kraft.api.routes.board._gate_artifact` resolves the path and
    `GET /work-items/{wid}/artifact` reads the file off disk from the worktree,
    deliberately. With the contract in
    `_CTX` the artifact needs no commit rule of its own, and must not state one
    twice."""
    seen = _capture_cmd(monkeypatch)
    _run(artifact="spec")
    prompt = _system_prompt(seen["cmd"])
    assert "invisible to them" not in prompt
    assert prompt.count("Commit everything you change before you exit") == 1


def test_default_profile_reproduces_todays_command_line(monkeypatch):
    """The whole argv for a binding that sets none of the optional keys.

    Pinned in full rather than by flag, so a change to what every worker is
    launched with cannot land without being read.
    """
    seen = _capture_cmd(monkeypatch)
    _run(command="claude", task_instruction="do the thing")
    ctx = agent._CTX.format(
        title="t",
        task_instruction="do the thing",
        repo_path="/repo",
        work_item_id="w1",
        node_id="implementation",
        hook_point="on.implementation.start",
        session_id="s1",
    )
    assert seen["cmd"] == [
        *shlex.split("claude"),
        "-p",
        "do the thing",
        "--append-system-prompt",
        ctx,
        "--output-format",
        "stream-json",
        "--verbose",
        "--permission-mode",
        "auto",
        "--permission-prompt-tool",
        "mcp__kraft__permission_request",
    ]


def test_agent_command_streams_ndjson(monkeypatch):
    """`--output-format json` prints one object at exit and nothing before it:
    no log to tail, no tokens to count, no model to record until the node is
    already over (Kraft-77z, Kraft-54dk, Kraft-2r8s)."""
    seen = _capture_cmd(monkeypatch)
    _run()
    cmd = seen["cmd"]
    assert cmd[cmd.index("--output-format") + 1] == "stream-json"
    # measured: without --verbose the CLI exits 1 rather than streaming
    assert "--verbose" in cmd
    assert cmd.count("--output-format") == 1
    assert cmd.count("--verbose") == 1


def test_model_is_passed_through_as_a_flag(monkeypatch):
    seen = _capture_cmd(monkeypatch)
    _run(model="opus")
    assert seen["cmd"][-2:] == ["--model", "opus"]


def test_no_model_emits_no_model_flag(monkeypatch):
    """Regression guard: passes before this task too."""
    seen = _capture_cmd(monkeypatch)
    _run()
    assert "--model" not in seen["cmd"]


def test_deny_tools_become_one_comma_joined_flag(monkeypatch):
    seen = _capture_cmd(monkeypatch)
    _run(deny_tools=("WebFetch", "Bash"))
    assert seen["cmd"][-2:] == ["--disallowed-tools", "WebFetch,Bash"]


def test_empty_deny_tools_emits_no_flag(monkeypatch):
    """Regression guard: passes before this task too."""
    seen = _capture_cmd(monkeypatch)
    _run()
    assert "--disallowed-tools" not in seen["cmd"]


def test_allowed_tools_and_permission_mode_come_from_the_binding(monkeypatch):
    """A node that needs no shell should be able to say so (Kraft-3tw)."""
    seen = _capture_cmd(monkeypatch)
    _run(allowed_tools=("Read", "Grep"), permission_mode="acceptEdits")
    cmd = seen["cmd"]
    assert cmd[cmd.index("--allowedTools") + 1] == "Read,Grep"
    assert cmd[cmd.index("--permission-mode") + 1] == "acceptEdits"
    assert cmd.count("--permission-mode") == 1


def test_a_silent_binding_keeps_todays_grant(monkeypatch):
    """Regression guard: every shipped node still launches exactly as it did."""
    seen = _capture_cmd(monkeypatch)
    _run()
    cmd = seen["cmd"]
    assert cmd[cmd.index("--permission-mode") + 1] == "auto"
    assert "--allowedTools" not in cmd


def test_allowed_tools_and_permission_mode_are_read_off_the_hook_binding():
    inv = agent.resolve_invocation(
        {"command": "c", "allowed_tools": ["Read", "Grep"], "permission_mode": "plan"}, {}, None
    )
    assert inv.allowed_tools == ("Read", "Grep")
    assert inv.permission_mode == "plan"
    bare = agent.resolve_invocation({"command": "c"}, {}, None)
    assert bare.allowed_tools == () and bare.permission_mode is None


def test_allowed_tools_are_not_taken_from_the_repo_entry():
    """Unioning two allowlists widens the narrower one, which is the opposite
    of what an allowlist is for -- unlike deny_tools, which only ever narrows."""
    inv = agent.resolve_invocation(
        {"command": "c", "allowed_tools": ["Read"]},
        {"allowed_tools": ["Bash"], "deny_tools": ["WebFetch"]},
        None,
    )
    assert inv.allowed_tools == ("Read",)
    assert inv.deny_tools == ("WebFetch",)  # deny_tools still unions, repo first


def test_permission_prompt_tool_is_passed(monkeypatch):
    """Every agent task, not just ones with an allowlist: the decision and its
    event are the point, and a node with no allowlist still allows and still
    records (Kraft-oor)."""
    seen = _capture_cmd(monkeypatch)
    _run()
    cmd = seen["cmd"]
    assert cmd[cmd.index("--permission-prompt-tool") + 1] == "mcp__kraft__permission_request"


def test_unknown_profile_raises_naming_the_profile(monkeypatch):
    _capture_cmd(monkeypatch)
    with pytest.raises(ValueError, match="nope"):
        _run(profile="nope")


def _system_prompt(cmd):
    return cmd[cmd.index("--append-system-prompt") + 1]


def test_steering_is_appended_under_a_heading(monkeypatch):
    seen = _capture_cmd(monkeypatch)
    _run(steering_texts=("Use tabs.",))
    prompt = _system_prompt(seen["cmd"])
    assert prompt.endswith(steering.HEADING + "Use tabs.")


def test_steering_order_is_preserved_in_the_prompt(monkeypatch):
    """repo body before hook body — resolve_invocation already orders the
    tuple; this only checks run_agent_task doesn't reorder or drop bodies."""
    seen = _capture_cmd(monkeypatch)
    _run(steering_texts=("repo body", "hook body"))
    prompt = _system_prompt(seen["cmd"])
    assert prompt.index("repo body") < prompt.index("hook body")


def test_no_steering_leaves_the_system_prompt_byte_identical(monkeypatch):
    """Regression guard: this sub-project's central compatibility claim."""
    seen = _capture_cmd(monkeypatch)
    _run(steering_texts=())
    ctx = agent._CTX.format(
        title="t",
        task_instruction="do the thing",
        repo_path="/repo",
        work_item_id="w1",
        node_id="implementation",
        hook_point="on.implementation.start",
        session_id="s1",
    )
    assert _system_prompt(seen["cmd"]) == ctx


def test_a_real_launch_never_leaks_repo_file_content_into_the_prompt(tmp_path, monkeypatch):
    """Spec §6's boundary regression, for real, against a real (fake) agent
    process — not the string `"CLAUDE.md" not in prompt`, which holds for any
    implementation, including one that read the repo's CLAUDE.md and injected
    it under another name (and would invert on a legitimate steering file that
    happens to mention "CLAUDE.md" in prose). Put recognisable content in
    CLAUDE.md and AGENTS.md, run a real launch, and check neither file's
    *content* reached the prompt and neither file was touched."""
    repo = make_repo(tmp_path)
    claude_marker = "REPO-SECRET-CLAUDE-MD-CONTENT-MUST-NOT-BE-INJECTED"
    agents_marker = "REPO-SECRET-AGENTS-MD-CONTENT-MUST-NOT-BE-INJECTED"
    (repo / "CLAUDE.md").write_text(claude_marker + "\n")
    (repo / "AGENTS.md").write_text(agents_marker + "\n")
    monkeypatch.delenv("KRAFT_FAKE_AGENT", raising=False)
    argv_log = tmp_path / "argv.jsonl"
    monkeypatch.setenv("KRAFT_FAKE_AGENT_ARGV_LOG", str(argv_log))

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            await _seed(database, repo)
            status = await agent.run_agent_task(
                database,
                rd,
                session_id="s9",
                work_item_id="w1",
                node_id="implementation",
                hook_point="on.implementation.start",
                command=f"{sys.executable} {_FAKE}",
                title="make the failing test pass",
                task_instruction="make the failing test pass",
                repo_path=str(repo),
                cwd=repo,
            )
            assert status == "done"
        finally:
            await database.close()

    asyncio.run(scenario())

    argv = json.loads(argv_log.read_text().splitlines()[0])
    prompt = argv[argv.index("--append-system-prompt") + 1]
    assert claude_marker not in prompt
    assert agents_marker not in prompt
    # Untouched — the fake agent never opens either file, and neither does the
    # adapter building the prompt above it.
    assert (repo / "CLAUDE.md").read_text() == claude_marker + "\n"
    assert (repo / "AGENTS.md").read_text() == agents_marker + "\n"


# --- resolve_invocation ------------------------------------------------------


def test_hook_model_beats_repo_default():
    inv = agent.resolve_invocation(
        {"command": "c", "model": "opus"}, {"default_model": "haiku"}, None
    )
    assert inv.model == "opus"


def test_repo_default_model_applies_when_the_hook_is_silent():
    inv = agent.resolve_invocation({"command": "c"}, {"default_model": "haiku"}, None)
    assert inv.model == "haiku"


def test_no_model_anywhere_leaves_it_unset():
    inv = agent.resolve_invocation({"command": "c"}, {}, None)
    assert inv.model is None


def test_command_and_profile_vary_independently():
    """Spec §6: the profile says how a CLI is spoken to, the command says which
    binary is spoken to — one hook can change either without the other."""
    same_profile = agent.resolve_invocation({"command": "claude-next"}, None, None)
    assert (same_profile.command, same_profile.profile) == ("claude-next", "claude")

    same_command = agent.resolve_invocation({"command": "claude", "profile": "claude"}, None, None)
    assert (same_command.command, same_command.profile) == ("claude", "claude")


def test_deny_tools_union_repo_first_deduplicated():
    inv = agent.resolve_invocation(
        {"command": "c", "deny_tools": ["WebFetch", "Bash"]},
        {"deny_tools": ["Bash"]},
        None,
    )
    assert inv.deny_tools == ("Bash", "WebFetch")


def test_steering_is_repo_first_then_hook(tmp_path):
    (tmp_path / "repo-note.md").write_text("repo")
    (tmp_path / "hook-note.md").write_text("hook")
    inv = agent.resolve_invocation(
        {"command": "c", "steering": ["hook-note"]},
        {"steering": ["repo-note"]},
        tmp_path,
    )
    # assert against a TUPLE — Invocation fields are tuples, and
    # ("repo", "hook") == ["repo", "hook"] is False
    assert inv.steering_texts == ("repo", "hook")


def test_a_repo_entry_of_none_behaves_like_an_empty_one(tmp_path):
    assert agent.resolve_invocation({"command": "c"}, None, tmp_path) == agent.resolve_invocation(
        {"command": "c"}, {}, tmp_path
    )


def test_combined_repo_and_hook_steering_over_budget_raises(tmp_path):
    """`steering.validate` runs once over repos.yaml's names and separately over
    a hook's names at config-load time — each individually valid here. Nothing
    at load time measures the concatenation `resolve_invocation` actually
    builds, which is where a union of two individually-valid lists can still
    blow the shared 8 KB budget."""
    repo_body = "x" * (steering.MAX_BYTES // 2)
    hook_body = "y" * (steering.MAX_BYTES // 2)
    (tmp_path / "repo-note.md").write_text(repo_body)
    (tmp_path / "hook-note.md").write_text(hook_body)
    # Each list validates cleanly on its own — no post-validation edit needed.
    steering.validate(tmp_path, ["repo-note"], where="repos.yaml")
    steering.validate(tmp_path, ["hook-note"], where="registry.yaml")

    with pytest.raises(steering.SteeringError, match="8192"):
        agent.resolve_invocation(
            {"command": "c", "steering": ["hook-note"]},
            {"steering": ["repo-note"]},
            tmp_path,
        )


def test_escalate_picks_the_escalate_model():
    """Precedence lives in resolve_invocation and only there (spec §6): a fix
    cycle past `escalate_after` asks for the bump, it does not name a model."""
    binding = {"command": "c", "model": "sonnet", "escalate_model": "opus"}
    assert agent.resolve_invocation(binding, {}, None).model == "sonnet"
    assert agent.resolve_invocation(binding, {}, None, escalate=True).model == "opus"


def test_escalate_falls_back_to_the_ordinary_model_when_none_is_configured():
    binding = {"command": "c", "model": "sonnet"}
    assert agent.resolve_invocation(binding, {}, None, escalate=True).model == "sonnet"


def test_escalate_falls_back_to_the_repo_default_when_the_hook_names_neither():
    assert (
        agent.resolve_invocation(
            {"command": "c"}, {"default_model": "haiku"}, None, escalate=True
        ).model
        == "haiku"
    )


def test_item_override_model_beats_binding_model():
    binding = {"command": "c", "model": "sonnet"}
    inv = agent.resolve_invocation(binding, {}, None, item_override={"model": "opus"})
    assert inv.model == "opus"


def test_item_override_escalate_model_beats_binding_escalate_model_while_escalating():
    binding = {"command": "c", "escalate_model": "opus"}
    inv = agent.resolve_invocation(
        binding, {}, None, escalate=True, item_override={"escalate_model": "opus-max"}
    )
    assert inv.model == "opus-max"


def test_binding_escalate_model_beats_item_plain_model_override_while_escalating():
    """The bead's own worked case: a cheap item override must not suppress the
    escalation valve. A plain `model` override is not `escalate_model`, so it
    does not compete with the binding's `escalate_model` while escalating."""
    binding = {"command": "c", "model": "sonnet", "escalate_model": "opus"}
    inv = agent.resolve_invocation(
        binding, {}, None, escalate=True, item_override={"model": "haiku"}
    )
    assert inv.model == "opus"


def test_item_override_effort_beats_binding_effort():
    binding = {"command": "c", "effort": "low"}
    inv = agent.resolve_invocation(binding, {}, None, item_override={"effort": "max"})
    assert inv.effort == "max"


def test_no_item_override_falls_through_to_binding_and_repo_default():
    binding = {"command": "c", "model": "sonnet", "effort": "low"}
    inv = agent.resolve_invocation(binding, {}, None, item_override=None)
    assert inv.model == "sonnet"
    assert inv.effort == "low"


def test_an_artifact_binding_names_the_exact_path_in_the_system_prompt(monkeypatch):
    seen = _capture_cmd(monkeypatch)
    _run(artifact="spec")
    prompt = _system_prompt(seen["cmd"])
    assert ".engineering/specs/w1.md" in prompt
    assert "commit" in prompt


def test_no_artifact_and_no_method_leave_the_system_prompt_byte_identical(monkeypatch):
    seen = _capture_cmd(monkeypatch)
    _run()
    without = _system_prompt(seen["cmd"])
    seen2 = _capture_cmd(monkeypatch)
    _run(artifact=None, method_text=None)
    assert _system_prompt(seen2["cmd"]) == without


def test_method_text_is_injected_under_the_method_heading(monkeypatch):
    seen = _capture_cmd(monkeypatch)
    _run(method_text="Write it in one page.")
    prompt = _system_prompt(seen["cmd"])
    assert skill.HEADING + "Write it in one page." in prompt
    assert prompt.endswith(skill.UNAVAILABLE)


def test_the_artifact_block_precedes_the_method_block(monkeypatch):
    """Spec §2.6 fixes the order: what to produce, then how to produce it."""
    seen = _capture_cmd(monkeypatch)
    _run(artifact="spec", method_text="Write it in one page.")
    prompt = _system_prompt(seen["cmd"])
    assert prompt.index(".engineering/specs/w1.md") < prompt.index(skill.HEADING)


def test_resolve_invocation_reads_the_skill(tmp_path):
    inv = agent.resolve_invocation(
        {"command": "c", "skill": "chain-review"}, None, None, skills_dir=tmp_path
    )
    assert inv.method_text.startswith("---")


def test_resolve_invocation_without_a_skill_carries_no_method(tmp_path):
    inv = agent.resolve_invocation({"command": "c"}, None, None, skills_dir=tmp_path)
    assert inv.method_text is None


def test_artifact_path_is_the_kind_pluralised():
    assert agent.artifact_path("spec", "w1") == ".engineering/specs/w1.md"
    assert agent.artifact_path("plan", "w1") == ".engineering/plans/w1.md"


def test_permission_mode_is_always_passed(monkeypatch):
    """Without it, `claude -p` has nobody to answer a prompt and denies instead.

    Measured on work item 6363c65e: the spec worker logged 13 permission
    denials, the Write of its own artifact among them, and the node still
    reported success (Kraft-8pe, Kraft-7lu).
    """
    seen = _capture_cmd(monkeypatch)
    _run()
    cmd = seen["cmd"]
    assert "--permission-mode" in cmd
    assert cmd[cmd.index("--permission-mode") + 1] == "auto"


def test_a_declared_artifact_that_was_never_written_fails_the_node(tmp_path, monkeypatch):
    """A worker that produced no artifact did not do its job (Kraft-7lu).

    On work item 6363c65e the spec worker was refused every Write, wrote
    nothing, and still reported success — so the executor completed the node
    and opened `spec_approval` over an empty gate, which cost $2.15 and asked
    a human to approve nothing.
    """
    monkeypatch.setenv("KRAFT_FAKE_AGENT_SKIP_ARTIFACT", "1")
    repo = make_repo(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            await _seed(database, repo)
            status = await agent.run_agent_task(
                database,
                rd,
                session_id="s9",
                work_item_id="w1",
                node_id="spec",
                hook_point="on.spec.requested",
                command=f"{sys.executable} {_FAKE}",
                title="t",
                task_instruction="t",
                repo_path=str(repo),
                cwd=repo,
                artifact="spec",
            )
            assert not (repo / agent.artifact_path("spec", "w1")).exists()
            assert status == "failed"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_a_declared_artifact_that_was_written_keeps_the_node_green(tmp_path, monkeypatch):
    """The other half: the guard must not fail a worker that did its job."""
    repo = make_repo(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            await _seed(database, repo)
            written = repo / agent.artifact_path("spec", "w1")
            written.parent.mkdir(parents=True, exist_ok=True)
            written.write_text("# spec\n")
            status = await agent.run_agent_task(
                database,
                rd,
                session_id="s10",
                work_item_id="w1",
                node_id="spec",
                hook_point="on.spec.requested",
                command=f"{sys.executable} {_FAKE}",
                title="t",
                task_instruction="t",
                repo_path=str(repo),
                cwd=repo,
                artifact="spec",
            )
            assert status != "failed"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_effort_is_read_off_the_hook_binding():
    assert agent.resolve_invocation({"command": "c", "effort": "high"}, {}, None).effort == "high"


def test_no_effort_anywhere_leaves_it_unset():
    assert agent.resolve_invocation({"command": "c"}, {}, None).effort is None


def test_effort_becomes_a_flag(monkeypatch):
    seen = _capture_cmd(monkeypatch)
    _run(effort="high")
    assert seen["cmd"][-2:] == ["--effort", "high"]


def test_no_effort_emits_no_flag(monkeypatch):
    """Regression guard: an unset effort must leave the CLI's own default."""
    seen = _capture_cmd(monkeypatch)
    _run()
    assert "--effort" not in seen["cmd"]


def test_needs_context_survives_the_artifact_guard(tmp_path, monkeypatch):
    """A worker that stopped to ask a question wrote no artifact *because* it
    stopped — rewriting that to `failed` loses the question.

    `kraft.executor.dispatch.needs_context_question` matches on the session row's status, so
    a downgrade here makes the stop reason generic and `kraft.api.routes.board._needs_context_stop`
    false, which 409s both /steer and /resume. The guard is for a worker that
    claimed success without producing its artifact, not for one that said
    plainly it could not finish.
    """
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")
    monkeypatch.setenv("KRAFT_FAKE_AGENT_STATUS", "needs_context")
    monkeypatch.setenv("KRAFT_FAKE_AGENT_QUESTION", "which editor policy?")
    monkeypatch.setenv("KRAFT_FAKE_AGENT_SKIP_ARTIFACT", "1")
    repo = make_repo(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            await _seed(database, repo)
            status = await agent.run_agent_task(
                database,
                rd,
                session_id="s11",
                work_item_id="w1",
                node_id="spec",
                hook_point="on.spec.requested",
                command=f"{sys.executable} {_FAKE}",
                title="t",
                task_instruction="t",
                repo_path=str(repo),
                cwd=repo,
                artifact="spec",
            )
            assert not (repo / agent.artifact_path("spec", "w1")).exists()
            assert status == "needs_context"
            row = database.read(
                lambda c: c.execute(
                    "SELECT status, result_path FROM worker_sessions WHERE id='s11'"
                ).fetchone()
            )
            assert row["status"] == "needs_context"
            assert read_question(Path(row["result_path"])) == "which editor policy?"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_repo_agent_docs_do_not_forbid_a_kraft_worker_from_committing():
    """Kraft-f3mv. The implementation node is a bare `claude` in this repo, so
    it reads CLAUDE.md's Conservative profile -- "Do not run git commits, git
    pushes, or Dolt remote sync unless explicitly asked" -- and correctly
    leaves the work uncommitted. Task 2's injected contract says the opposite;
    the two must not argue.

    The carve-out has to sit after every generated `bd` block: the Conservative
    line lives inside a hashed block, and editing inside one invites `bd` to
    regenerate it and drop the carve-out silently.
    """
    for name in ("CLAUDE.md", "AGENTS.md"):
        text = (_REPO_ROOT / name).read_text()
        assert "## Kraft Workers" in text, f"{name} has no Kraft-worker carve-out"
        assert "KRAFT_WORK_ITEM_ID" in text, f"{name} does not say who the carve-out applies to"
        assert "commit" in text[text.index("## Kraft Workers") :], name
        assert text.index("## Kraft Workers") > text.rindex("<!-- END BEADS"), (
            f"{name}: the carve-out is inside or above a generated bd block, "
            "where a regeneration would drop it"
        )


def test_resume_session_id_adds_resume_and_autocompact_flags(monkeypatch):
    seen = _capture_cmd(monkeypatch)
    _run(resume_session_id="cli-session-abc", autocompact="auto")
    cmd = seen["cmd"]
    assert "--resume" in cmd
    assert cmd[cmd.index("--resume") + 1] == "cli-session-abc"
    assert "--autocompact" in cmd
    assert cmd[cmd.index("--autocompact") + 1] == "auto"


def test_no_resume_session_id_omits_resume_and_autocompact_flags(monkeypatch):
    seen = _capture_cmd(monkeypatch)
    _run()
    assert "--resume" not in seen["cmd"]
    assert "--autocompact" not in seen["cmd"]


def test_identify_as_worker_false_omits_the_work_item_env_var(monkeypatch):
    seen = {}

    async def fake_run_task(db, run_dirs, *, env, **kw):
        seen["env"] = env
        return "done"

    monkeypatch.setattr("kraft.adapters.agent._subprocess.run_task", fake_run_task)
    _run(identify_as_worker=False)
    assert "KRAFT_WORK_ITEM_ID" not in seen["env"]
    assert seen["env"]["KRAFT_SESSION_ID"] == "s1"


def test_identify_as_worker_defaults_true(monkeypatch):
    seen = {}

    async def fake_run_task(db, run_dirs, *, env, **kw):
        seen["env"] = env
        return "done"

    monkeypatch.setattr("kraft.adapters.agent._subprocess.run_task", fake_run_task)
    _run()
    assert seen["env"]["KRAFT_WORK_ITEM_ID"] == "w1"
