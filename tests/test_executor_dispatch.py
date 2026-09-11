import asyncio
import json
import subprocess
import sys
from pathlib import Path

from support.harness import _git, fake_registry, isolated_bd, make_repo

from kraft import db, events, executor, store
from kraft.config import git_read
from kraft.executor import dispatch
from kraft.paths import RunDirs
from kraft.templates import Registry, Template, load_registry, load_templates

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FAKE_AGENT = Path(__file__).parent / "support" / "fake_agent.py"


def _quick_task() -> Template:
    reg = load_registry(_REPO_ROOT / "templates" / "registry.yaml")
    return load_templates(_REPO_ROOT / "templates", reg).valid["quick-task"]


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
    prompt sent, the way test_fix_loop asserts steer-note ordering."""
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
            registry = fake_registry(sys.executable, _FAKE_AGENT)
            wid = await executor.intake(
                database,
                rd,
                title=title,
                repo=str(repo),
                template=_quick_task(),
                bd_cwd=str(tracker),
                attachments=[{"kind": "spec", "path": ".engineering/specs/a.md"}],
            )
            result = await executor.run(
                database, rd, work_item_id=wid, registry=registry, bd_cwd=str(tracker)
            )
            assert result == "completed"
        finally:
            await database.close()

    asyncio.run(scenario())

    sent = [p for p in prompts.read_text().split("\n\x00\n") if p.strip()]
    # quick-task's only agent dispatch is the implementation node.
    assert len(sent) == 1
    prompt = sent[0]
    assert prompt.startswith(title)
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
            registry = fake_registry(sys.executable, _FAKE_AGENT)
            wid = await executor.intake(
                database,
                rd,
                title=title,
                description=description,
                repo=str(repo),
                template=_quick_task(),
                bd_cwd=str(tracker),
            )
            result = await executor.run(
                database, rd, work_item_id=wid, registry=registry, bd_cwd=str(tracker)
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
    lines, no 'None' rendered into the prompt."""
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
            registry = fake_registry(sys.executable, _FAKE_AGENT)
            wid = await executor.intake(
                database,
                rd,
                title=title,
                repo=str(repo),
                template=_quick_task(),
                bd_cwd=str(tracker),
            )
            await executor.run(
                database, rd, work_item_id=wid, registry=registry, bd_cwd=str(tracker)
            )
        finally:
            await database.close()

    asyncio.run(scenario())

    sent = [p for p in prompts.read_text().split("\n\x00\n") if p.strip()]
    assert len(sent) == 1
    assert sent[0].strip() == title + executor.BEAD_NOTE


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
            registry = fake_registry(sys.executable, _FAKE_AGENT)
            wid = await executor.intake(
                database,
                rd,
                title="t",
                repo=str(repo),
                template=_quick_task(),
                bd_cwd=str(tracker),
            )
            await executor.run(
                database, rd, work_item_id=wid, registry=registry, bd_cwd=str(tracker)
            )
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
    reaches the agent."""
    monkeypatch.delenv("KRAFT_FAKE_AGENT", raising=False)
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    argv_log = tmp_path / "argv.jsonl"
    monkeypatch.setenv("KRAFT_FAKE_AGENT_ARGV_LOG", str(argv_log))

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            registry = fake_registry(sys.executable, _FAKE_AGENT)
            wid = await executor.intake(
                database,
                rd,
                title="make the failing test pass",
                repo=str(repo),
                template=_quick_task(),
                bd_cwd=str(tracker),
            )
            launch = executor.LaunchContext(
                repo_entry={"default_model": "haiku"}, steering_dir=None
            )
            result = await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=registry,
                bd_cwd=str(tracker),
                launch=launch,
            )
            assert result == "completed"
        finally:
            await database.close()

    asyncio.run(scenario())

    argvs = _argv_lines(argv_log)
    assert len(argvs) == 1  # quick-task's only agent dispatch is implementation
    assert argvs[0][-2:] == ["--model", "haiku"]


def test_run_gathers_multi_task_node(tmp_path):
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            registry = Registry(
                hooks={
                    "on.env.prepare": {"kind": "builtin", "handler": "env_setup"},
                    "on.a": {"kind": "subprocess", "command": ["true"]},
                    "on.b": {"kind": "subprocess", "command": ["true"]},
                }
            )
            tmpl = Template(
                id="fan",
                nodes=[
                    {"id": "env_setup", "tasks": ["on.env.prepare"], "gate_after": None},
                    {"id": "work", "tasks": ["on.a", "on.b"], "gate_after": None},
                ],
            )
            wid = await executor.intake(
                database, rd, title="t", repo=str(repo), template=tmpl, bd_cwd=str(tracker)
            )
            result = await executor.run(
                database, rd, work_item_id=wid, registry=registry, bd_cwd=str(tracker)
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
            assert types.count("node_completed") == 2
        finally:
            await database.close()

    asyncio.run(scenario())


def test_a_subprocess_hook_prefers_the_repos_test_command(tmp_path, monkeypatch):
    """The registry's command is the fallback, not the authority: verify and CI
    drift apart exactly when the hardcoded one wins (Kraft-579)."""
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    marker = tmp_path / "which-ran.txt"

    registry_base = fake_registry(sys.executable, _FAKE_AGENT)
    registry = Registry(
        hooks={
            **registry_base.hooks,
            "on.test.run": {
                "kind": "subprocess",
                "command": [sys.executable, "-c", f"open({str(marker)!r}, 'w').write('registry')"],
            },
        }
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
                template=_quick_task(),
                bd_cwd=str(tracker),
            )
            await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=registry,
                bd_cwd=str(tracker),
                launch=executor.LaunchContext(
                    repo_entry={
                        "test_command": (
                            f"{sys.executable} -c \"open({str(marker)!r}, 'w').write('repo')\""
                        )
                    },
                    steering_dir=None,
                ),
            )
        finally:
            await database.close()

    asyncio.run(scenario())
    assert marker.read_text() == "repo", "the registry's hardcoded command won"


def test_a_subprocess_hook_falls_back_to_the_registry_command(tmp_path, monkeypatch):
    """A repo entry with no test_command keeps today's behaviour byte for byte —
    this is what makes the change safe for every existing install."""
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    marker = tmp_path / "which-ran.txt"

    registry_base = fake_registry(sys.executable, _FAKE_AGENT)
    registry = Registry(
        hooks={
            **registry_base.hooks,
            "on.test.run": {
                "kind": "subprocess",
                "command": [sys.executable, "-c", f"open({str(marker)!r}, 'w').write('registry')"],
            },
        }
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
                template=_quick_task(),
                bd_cwd=str(tracker),
            )
            await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=registry,
                bd_cwd=str(tracker),
                launch=executor.LaunchContext(repo_entry={}, steering_dir=None),
            )
        finally:
            await database.close()

    asyncio.run(scenario())
    assert marker.read_text() == "registry"


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
            registry = fake_registry(sys.executable, _FAKE_AGENT)
            wid = await executor.intake(
                database,
                rd,
                title="make the failing test pass",
                repo=str(repo),
                template=_quick_task(),
                bd_cwd=str(tracker),
                attachments=[{"kind": "plan", "path": ".engineering/plans/p.md"}],
            )
            await executor.run(
                database, rd, work_item_id=wid, registry=registry, bd_cwd=str(tracker)
            )
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


def test_dispatch_runs_matched_scopes_in_order_and_stops_at_the_first_failure(
    tmp_path, monkeypatch
):
    """Two overlapping scopes; the changed path matches both, both run, in
    declaration order, and a first-command failure short-circuits the
    second (test-scope design §3.6)."""
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    marker1 = tmp_path / "frontend-ran.txt"
    marker2 = tmp_path / "root-ran.txt"

    fail_script = tmp_path / "fail.py"
    fail_script.write_text(
        f"import pathlib, sys\npathlib.Path({str(marker1)!r}).write_text('ran')\nsys.exit(1)\n"
    )
    succeed_script = tmp_path / "succeed.py"
    succeed_script.write_text(f"import pathlib\npathlib.Path({str(marker2)!r}).write_text('ran')\n")

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = await executor.intake(
                database,
                rd,
                title="t",
                repo=str(repo),
                template=_quick_task(),
                bd_cwd=str(tracker),
            )
            base_sha = git_read(repo, "rev-parse", "HEAD")
            (repo / "frontend").mkdir()
            (repo / "frontend" / "x.txt").write_text("hi")
            _git(repo, "add", "-A")
            _git(repo, "commit", "-m", "touch frontend")
            await database.write(lambda c: store.set_base_ref(c, wid, base_sha))
            row = database.read(
                lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (wid,)).fetchone()
            )
            registry = Registry(hooks={"on.test.run": {"kind": "subprocess"}})
            return await dispatch.dispatch_node(
                database,
                rd,
                "on.test.run",
                {"id": "verify"},
                row,
                registry,
                repo,
                launch=executor.LaunchContext(
                    repo_entry={
                        "test_scopes": [
                            {
                                "paths": ["frontend/**"],
                                "command": f"{sys.executable} {fail_script}",
                            },
                            {"paths": ["**"], "command": f"{sys.executable} {succeed_script}"},
                        ]
                    },
                    steering_dir=None,
                ),
            )
        finally:
            await database.close()

    status = asyncio.run(scenario())
    assert status == "failed"
    assert marker1.read_text() == "ran"
    assert not marker2.exists(), "the first scope's failure must short-circuit the second"


def test_a_steered_rerun_over_an_existing_artifact_is_framed_as_a_revision(tmp_path):
    """Kraft-bol: a rejected plan cost a full re-plan because the dispatch
    never mentioned the document the agent had already written."""
    (tmp_path / ".engineering" / "plans").mkdir(parents=True)
    (tmp_path / ".engineering" / "plans" / "w1.md").write_text("# the plan\n")

    prefix = executor.steer_prefix(
        {"kind": "agent", "command": "claude", "skill": "plan", "artifact": "plan"},
        {"id": "w1"},
        tmp_path,
        "task 4 has no test",
    )

    assert ".engineering/plans/w1.md" in prefix
    assert "evise" in prefix  # "Revise that document in place"
    assert "task 4 has no test" in prefix
    assert not prefix.startswith("A human has steered this run:")


def test_a_steered_node_with_no_artifact_yet_keeps_the_plain_steer_prompt(tmp_path):
    """The branch fires on the document's existence, not on any gate name: a
    node whose artifact was never written has nothing to revise."""
    with_binding = executor.steer_prefix(
        {"kind": "agent", "command": "claude", "skill": "plan", "artifact": "plan"},
        {"id": "w1"},
        tmp_path,
        "go left",
    )
    no_binding = executor.steer_prefix(
        {"kind": "agent", "command": "claude"}, {"id": "w1"}, tmp_path, "go left"
    )

    assert with_binding == no_binding == "A human has steered this run: go left\n\n"


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
                template=_quick_task(),
                bd_cwd=str(tracker),
            )
            assert (
                await executor.run(
                    database,
                    rd,
                    work_item_id=wid,
                    registry=fake_registry(sys.executable, _FAKE_AGENT),
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
                template=_quick_task(),
                bd_cwd=str(tracker),
            )
            status = await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=fake_registry(sys.executable, _FAKE_AGENT),
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
