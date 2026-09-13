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


def _default_template() -> Template:
    reg = load_registry(_REPO_ROOT / "templates" / "registry.yaml")
    return load_templates(_REPO_ROOT / "templates", reg).valid["default"]


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
    assert argvs[0][argvs[0].index("--model") + 1] == "haiku"


def test_node_override_model_beats_item_override_beats_binding(tmp_path, monkeypatch):
    """Precedence (Kraft-df4tc design point 2): node_overrides > item-wide
    agent_overrides > the registry binding's own model. Exercises all three
    tiers in one item so a bug that makes any two collapse into one shows up
    as a wrong --model on the wire, not a passing test."""
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
                title="t",
                repo=str(repo),
                template=_quick_task(),
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
                database, rd, work_item_id=wid, registry=registry, bd_cwd=str(tracker)
            )
            assert result == "completed"
        finally:
            await database.close()

    asyncio.run(scenario())

    argvs = _argv_lines(argv_log)
    assert argvs[0][argvs[0].index("--model") + 1] == "node-model"


def test_chain_review_dispatch_prompt_carries_resolved_hook_bindings(tmp_path, monkeypatch):
    """Kraft-df4tc point 4: the chain-review hook's prompt is appended with
    the resolved registry.yaml binding for every hook point in its
    not-yet-executed tail, so a reviewer can name a real flag or override
    instead of guessing."""
    monkeypatch.delenv("KRAFT_FAKE_AGENT", raising=False)
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    prompt_log = tmp_path / "prompts.txt"
    monkeypatch.setenv("KRAFT_FAKE_AGENT_PROMPT_LOG", str(prompt_log))

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
                template=_default_template(),
                bd_cwd=str(tracker),
            )
            row = database.read(
                lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (wid,)).fetchone()
            )
            chain = json.loads(row["chain_definition"])
            node = next(n for n in chain["nodes"] if n["id"] == "chain_review")
            worktree = repo
            return await dispatch.dispatch_node(
                database,
                rd,
                "on.chain.review_ready",
                node,
                row,
                registry,
                worktree,
                launch=executor.LaunchContext(
                    repo_entry=None, steering_dir=_REPO_ROOT / "templates" / "steering"
                ),
            )
        finally:
            await database.close()

    asyncio.run(scenario())

    prompt = prompt_log.read_text()
    assert "Resolved hook bindings for the current tail" in prompt
    assert "on.test.run" in prompt
    # already-run node ids, so a backward reject_to/rebase_bounce_to can name
    # one (Kraft-df4tc) -- the bindings above list hook points, never node ids
    assert "Nodes already run" in prompt
    assert "spec, plan, chain_review" in prompt


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


def test_measure_node_reuses_a_done_session_at_the_current_head(tmp_path, monkeypatch):
    """Kraft-gl9d: a task whose session for this (node, round) already exited
    'done' against the worktree's current HEAD is not redispatched; a task
    with no matching 'done' session is."""

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            worktree = make_repo(tmp_path)
            head = git_read(worktree, "rev-parse", "HEAD")

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
            node = {"id": "verify", "tasks": ["on.test.run", "on.review.local.run"]}
            await database.write(
                lambda c: store.create_session(
                    c,
                    id="s-done",
                    work_item_id=wid,
                    node_id="verify",
                    hook_point="on.review.local.run",
                    log_path="/l",
                    result_path="/r",
                    round=0,
                    head_sha=head,
                )
            )
            await database.write(lambda c: store.session_exited(c, "s-done", "done"))

            dispatched = []

            async def fake_dispatch_node(db_, run_dirs_, task_hook, *a, **kw):
                dispatched.append(task_hook)
                return "failed"

            monkeypatch.setattr(dispatch, "dispatch_node", fake_dispatch_node)

            row = database.read(
                lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (wid,)).fetchone()
            )
            verdict, failed, excs = await dispatch.measure_node(
                database, rd, wid, node, row, Registry(hooks={}), worktree, round=0
            )
            # the reused hook never dispatches; the other one does
            assert dispatched == ["on.test.run"]
            assert verdict == "failed"
            assert failed == ["on.test.run"]
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
    second half) travels as a `Steer` with `human=False`. Both human templates
    name an author it does not have -- `_STEER_PROMPT` says "A human has
    steered this run", and over an existing artifact `_REVISE_PROMPT` says "A
    human read ... and sent it back with this note"."""
    (tmp_path / ".engineering" / "plans").mkdir(parents=True)
    (tmp_path / ".engineering" / "plans" / "w1.md").write_text("# the plan\n")
    binding = {"kind": "agent", "command": "claude", "skill": "plan", "artifact": "plan"}
    note = "Findings the last review of this node left unresolved:\n- [important] a.py:1 — x (cr)"

    seeded = executor.steer_prefix(binding, {"id": "w1"}, tmp_path, note, human=False)

    assert note in seeded
    assert "no human" in seeded
    assert "A human has steered" not in seeded
    assert "A human read" not in seeded
    # the default is unchanged: every existing caller still means a human.
    assert executor.steer_prefix(binding, {"id": "w1"}, tmp_path, note).startswith("A human read")


def test_dispatch_carries_the_notes_authorship_into_the_prompt(tmp_path, monkeypatch):
    """The wiring behind the test above: `dispatch_node` reads `Steer.human`
    off the note it takes, so a seeded steer reaches the agent framed as
    Kraft's. Without it the flag exists but never reaches the prompt."""
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    seen = {}

    async def fake_run_agent_task(db_, run_dirs_, **kw):
        seen["instruction"] = kw["task_instruction"]
        return "done"

    monkeypatch.setattr("kraft.executor.dispatch._agent.run_agent_task", fake_run_agent_task)

    async def scenario(human):
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
            row = database.read(
                lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (wid,)).fetchone()
            )
            registry = Registry(hooks={"on.test.run": {"kind": "agent", "command": "claude"}})
            await dispatch.dispatch_node(
                database,
                rd,
                "on.test.run",
                {"id": "verify"},
                row,
                registry,
                repo,
                steer=executor.Steer("findings left unresolved: x", human=human),
            )
        finally:
            await database.close()
        return seen["instruction"]

    seeded = asyncio.run(scenario(False))
    assert "no human" in seeded
    assert "A human has steered" not in seeded

    typed = asyncio.run(scenario(True))
    assert typed.startswith("A human has steered this run:")


def test_chain_review_context_names_a_forge_hook_handler():
    """Kraft-43kw gave the default chain two forge hooks whose bindings are
    otherwise identical (`on.merge` and `on.merge.watch` are both `{kind:
    forge, backend: auto}`). Without `handler` the reviewer cannot tell what
    a forge node actually runs, the same way `command`/`skill` tell it for an
    agent hook."""
    from kraft.executor import prompts
    from kraft.templates import Registry

    registry = Registry(
        hooks={
            "on.merge": {"kind": "forge", "handler": "merge", "backend": "auto"},
            "on.merge.watch": {"kind": "forge", "handler": "merge_watch", "backend": "auto"},
        }
    )
    text = prompts.chain_review_context(
        [
            {"id": "merge", "tasks": ["on.merge"]},
            {"id": "post_merge_watch", "tasks": ["on.merge.watch"]},
        ],
        registry,
    )
    assert "'handler': 'merge'" in text
    assert "'handler': 'merge_watch'" in text


def _dispatch_one_agent_node(tmp_path, monkeypatch, *, binding_extra, item, node_ov, escalate):
    """One `on.implementation.start` dispatch with all three override tiers
    populated, returning the argv the agent was actually launched with.

    Direct `dispatch_node` rather than a whole `executor.run`: the merge under
    test lives at that one call site, and the three-tier fixture is the same
    for every field it merges."""
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
            hook = "on.implementation.start"
            registry.hooks[hook] = {**registry.hooks[hook], **binding_extra}
            wid = await executor.intake(
                database,
                rd,
                title="t",
                repo=str(repo),
                template=_quick_task(),
                bd_cwd=str(tracker),
            )
            await database.write(lambda c: store.set_agent_overrides(c, wid, json.dumps(item)))
            await database.write(
                lambda c: store.set_node_overrides(c, wid, {"implementation": node_ov})
            )
            row = database.read(
                lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (wid,)).fetchone()
            )
            node = next(
                n
                for n in json.loads(row["chain_definition"])["nodes"]
                if n["id"] == "implementation"
            )
            await dispatch.dispatch_node(
                database,
                rd,
                hook,
                node,
                row,
                registry,
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
    `resolve_invocation` than `model` does (hook-level only, no repo default),
    so `model` passing says nothing about this one."""
    argv = _dispatch_one_agent_node(
        tmp_path,
        monkeypatch,
        binding_extra={"effort": "low"},
        item={"effort": "medium"},
        node_ov={"effort": "high"},
        escalate=False,
    )
    assert argv[argv.index("--effort") + 1] == "high"


def test_node_override_escalate_model_beats_item_override_beats_binding(tmp_path, monkeypatch):
    """The fix loop's capability bump is the third field of the dial, and the
    only one that reaches the wire as `--model` from a *different* branch of
    `resolve_invocation` (`escalate=True`)."""
    argv = _dispatch_one_agent_node(
        tmp_path,
        monkeypatch,
        binding_extra={"model": "binding-model", "escalate_model": "binding-escalate"},
        item={"model": "item-model", "escalate_model": "item-escalate"},
        node_ov={"model": "node-model", "escalate_model": "node-escalate"},
        escalate=True,
    )
    assert argv[argv.index("--model") + 1] == "node-escalate"
