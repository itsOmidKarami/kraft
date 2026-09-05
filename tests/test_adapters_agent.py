import asyncio
import json
import shlex
import sys
from pathlib import Path

import pytest
from support.harness import make_repo

from kraft import db, steering, store
from kraft.adapters import agent
from kraft.paths import RunDirs

_FAKE = Path(__file__).parent / "support" / "fake_agent.py"


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


def test_default_profile_reproduces_todays_command_line(monkeypatch):
    """Regression guard, not a red-green test: it passes before this task too.

    That is the point — the whole sub-project's compatibility claim is that a
    config setting none of the new keys builds the same argv as before.
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
        "json",
    ]


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
