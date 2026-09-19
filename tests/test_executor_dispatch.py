import ast
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

from support.harness import _git, fake_docker_bin, fake_registry, isolated_bd, make_repo
from support.store_fixtures import mk_item, open_db

from kraft import db, events, executor, store
from kraft.config import git_read
from kraft.executor import dispatch
from kraft.findings import JobRef
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
                repo_entry={"default_model": "haiku", "setup_command": ""}, steering_dir=None
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


def test_a_review_hook_with_no_package_does_not_launch_the_agent(tmp_path, monkeypatch):
    """.40: a hook in `REVIEW_HOOKS` dispatched with nothing to review must not
    silently launch a paid agent to review nothing. `base_ref` is set (this is
    the "should have a package but doesn't" case -- a git failure, or a
    genuinely missing package -- not the legitimate no-`base_ref` one), and
    `review_package` is forced to `None` to exercise it without needing to
    fabricate the exact git state that produces it for real."""
    monkeypatch.delenv("KRAFT_FAKE_AGENT", raising=False)
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    prompt_log = tmp_path / "prompts.txt"
    monkeypatch.setenv("KRAFT_FAKE_AGENT_PROMPT_LOG", str(prompt_log))
    monkeypatch.setattr(dispatch.prompts, "review_package", lambda *a, **k: None)

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
            base_sha = git_read(repo, "rev-parse", "HEAD")
            await database.write(lambda c: store.set_base_ref(c, wid, base_sha))
            row = database.read(
                lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (wid,)).fetchone()
            )
            chain = json.loads(row["chain_definition"])
            node = next(n for n in chain["nodes"] if n["id"] == "verify")
            return await dispatch.dispatch_node(
                database,
                rd,
                "on.review.local.run",
                node,
                row,
                registry,
                repo,
                launch=executor.LaunchContext(
                    repo_entry=None, steering_dir=_REPO_ROOT / "templates" / "steering"
                ),
            )
        finally:
            await database.close()

    result = asyncio.run(scenario())
    assert result == dispatch.CONFIG_ERROR
    assert not prompt_log.exists()  # the fake agent never ran


def test_a_review_hook_with_no_base_ref_yet_still_runs(tmp_path, monkeypatch):
    """`review_package` legitimately returns `None` for an item with no
    `base_ref` yet -- pre-migration items, and any template with no
    `env_setup` node. That case must keep working unchanged: the new guard is
    "a review hook dispatched with nothing to review", not "package is None"."""
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
            assert row["base_ref"] is None  # env_setup never ran
            chain = json.loads(row["chain_definition"])
            node = next(n for n in chain["nodes"] if n["id"] == "verify")
            return await dispatch.dispatch_node(
                database,
                rd,
                "on.review.local.run",
                node,
                row,
                registry,
                repo,
                launch=executor.LaunchContext(
                    repo_entry=None, steering_dir=_REPO_ROOT / "templates" / "steering"
                ),
            )
        finally:
            await database.close()

    asyncio.run(scenario())
    assert prompt_log.exists()  # ran unchanged, no config_error


def test_a_hook_with_its_own_skill_is_not_told_to_implement(tmp_path, monkeypatch):
    """`on.review.local.run` carries skill `code-review`. It must not be handed
    the implementer's "follow the plan, do not re-plan" -- that framing is why
    `on.mr.describe` ran the full test suite in the MR-description node
    (Kraft-s7c04.52)."""
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
                attachments=[
                    {"kind": "spec", "path": ".engineering/specs/a.md"},
                    {"kind": "plan", "path": ".engineering/plans/a.md"},
                ],
            )
            row = database.read(
                lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (wid,)).fetchone()
            )
            chain = json.loads(row["chain_definition"])
            launch = executor.LaunchContext(
                repo_entry=None, steering_dir=_REPO_ROOT / "templates" / "steering"
            )
            impl_node = next(n for n in chain["nodes"] if n["id"] == "implementation")
            await dispatch.dispatch_node(
                database,
                rd,
                "on.implementation.start",
                impl_node,
                row,
                registry,
                repo,
                launch=launch,
            )
            verify_node = next(n for n in chain["nodes"] if n["id"] == "verify")
            await dispatch.dispatch_node(
                database,
                rd,
                "on.review.local.run",
                verify_node,
                row,
                registry,
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
                        ),
                        "setup_command": "",
                    },
                    steering_dir=None,
                ),
            )
        finally:
            await database.close()

    asyncio.run(scenario())
    assert marker.read_text() == "repo", "the registry's hardcoded command won"


def test_the_repos_declared_env_reaches_a_test_scopes_run_task(tmp_path, monkeypatch):
    """dispatch.py:471 calls `_subprocess.run_task` directly for each matched
    test scope, bypassing `resolve_invocation`. Left unwired, that call hands
    `run_task` a `repo_entry` it never reads, and the repo's declared `env`
    never reaches the one path whose job is to decide whether the MR is safe
    to merge (Kraft-69atv Step 4b). The call-site override
    (`PYTHONDONTWRITEBYTECODE=1`) must still win alongside it."""
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    dumped = tmp_path / "child-env.txt"

    registry_base = fake_registry(sys.executable, _FAKE_AGENT)
    registry = Registry(hooks={**registry_base.hooks, "on.test.run": {"kind": "subprocess"}})

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
    """`on.test.run` with `sandbox` on its binding wraps into `docker run` --
    proven by pointing PATH at a fake `docker` that unwraps back to the real
    command, one layer further out than
    `test_a_subprocess_hook_prefers_the_repos_test_command` proves which
    command ran. The marker file alone would not prove this: the real
    command writes it whether or not anything wrapped it, so this also
    checks the sentinel only the fake `docker` itself touches."""
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")
    monkeypatch.setenv("PATH", f"{fake_docker_bin(tmp_path)}:{os.environ['PATH']}")
    called = tmp_path / "docker-was-called"
    monkeypatch.setenv("FAKE_DOCKER_CALLED", str(called))
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    marker = tmp_path / "ran.txt"

    registry_base = fake_registry(sys.executable, _FAKE_AGENT)
    registry = Registry(
        hooks={
            **registry_base.hooks,
            "on.test.run": {
                "kind": "subprocess",
                "command": [sys.executable, "-c", f"open({str(marker)!r}, 'w').write('ran')"],
                "sandbox": {"kind": "docker", "image": "kraft-worker:py"},
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
                launch=executor.LaunchContext(repo_entry={"setup_command": ""}, steering_dir=None),
            )
        finally:
            await database.close()

    asyncio.run(scenario())
    assert marker.read_text() == "ran"
    assert called.exists()


def test_a_repo_can_turn_off_a_binding_that_turned_sandboxing_on(tmp_path, monkeypatch):
    """No fake `docker` anywhere on PATH -- if the repo's `sandbox: false`
    didn't win over the binding's, this would config_error on a missing
    `docker` binary instead of running the real command directly."""
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    marker = tmp_path / "ran.txt"

    registry_base = fake_registry(sys.executable, _FAKE_AGENT)
    registry = Registry(
        hooks={
            **registry_base.hooks,
            "on.test.run": {
                "kind": "subprocess",
                "command": [sys.executable, "-c", f"open({str(marker)!r}, 'w').write('ran')"],
                "sandbox": {"kind": "docker", "image": "kraft-worker:py"},
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
                    repo_entry={"sandbox": False, "setup_command": ""}, steering_dir=None
                ),
            )
        finally:
            await database.close()

    asyncio.run(scenario())
    assert marker.read_text() == "ran"


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
                launch=executor.LaunchContext(repo_entry={"setup_command": ""}, steering_dir=None),
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


def test_the_implementer_is_told_which_commands_gate_its_paths(tmp_path, monkeypatch):
    """49c0cefd's agent could run `just e2e-ci` and was never told it existed
    (Kraft-s7c04.8). The implementation prompt now carries the repo's
    path->command mapping. `on.mr.describe` -- a different `kind: agent` hook
    dispatched in the same run -- must not get it (Kraft-s7c04.45: keyed on
    the hook, not the node id)."""
    monkeypatch.delenv("KRAFT_FAKE_AGENT", raising=False)
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    prompt_log = tmp_path / "prompts.txt"
    monkeypatch.setenv("KRAFT_FAKE_AGENT_PROMPT_LOG", str(prompt_log))
    fake = f"{sys.executable} {_FAKE_AGENT}"
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

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            registry_base = fake_registry(sys.executable, _FAKE_AGENT)
            registry = Registry(
                hooks={
                    **registry_base.hooks,
                    "on.mr.describe": {"kind": "agent", "command": fake},
                }
            )
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
            impl_node = next(n for n in chain["nodes"] if n["id"] == "implementation")
            await dispatch.dispatch_node(
                database,
                rd,
                "on.implementation.start",
                impl_node,
                row,
                registry,
                repo,
                launch=launch,
            )
            mr_node = next(n for n in chain["nodes"] if n["id"] == "open_mr")
            await dispatch.dispatch_node(
                database, rd, "on.mr.describe", mr_node, row, registry, repo, launch=launch
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
    """`on.test.run`'s scope loop mints one session per scope under one
    hook_point (dispatch.py's identity problem, spec 2026-09-15-batch-c1-design
    §"The identity problem"). A last-wins read of the round's sessions would
    let scope 3's pass erase scope 1's real failure -- exactly the blind
    failure gap 65f3ed90 closed, and C2 regresses it without this fix."""

    async def scenario():
        database = await open_db(tmp_path)
        try:
            await mk_item(database)
            node = {"id": "verify", "tasks": ["on.test.run"]}
            registry = Registry(
                hooks={"on.test.run": {"kind": "subprocess", "command": ["just", "ci-test"]}}
            )
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
                        hook_point="on.test.run",
                        log_path=log_path,
                        result_path=str(tmp_path / f"{sid}.json"),
                        round=0,
                        head_sha="sha-a",
                    )
                )
                await database.write(
                    lambda c, sid=sid, status=status: store.session_exited(c, sid, status)
                )
            found, reported = dispatch.collect_findings(database, "w1", node, 0, registry)
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
    failure, so two scopes under on.test.run can fail in the same round --
    the fix agent needs one coherent notice naming both, not two identical-
    looking critical findings."""

    async def scenario():
        database = await open_db(tmp_path)
        try:
            await mk_item(database)
            node = {"id": "verify", "tasks": ["on.test.run"]}
            registry = Registry(
                hooks={"on.test.run": {"kind": "subprocess", "command": ["just", "ci-test"]}}
            )
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
                        hook_point="on.test.run",
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
            found, reported = dispatch.collect_findings(database, "w1", node, 0, registry)
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
    (`prints == previous_prints`, `walk.py:840`): aggregating N failing
    scopes into one Finding shrinks how many fingerprints one round
    produces (2 -> 1 for the scenario above), which nothing at the
    `walk.py` level exercises directly in this bundle. Pinning it here
    instead: the same two scopes failing identically in two different
    rounds (different session ids, different round numbers -- the
    round-to-round reality) must still produce the same single fingerprint,
    or the stuck-detector's streak can never advance past 1."""

    async def scenario():
        database = await open_db(tmp_path)
        try:
            await mk_item(database)
            node = {"id": "verify", "tasks": ["on.test.run"]}
            registry = Registry(
                hooks={"on.test.run": {"kind": "subprocess", "command": ["just", "ci-test"]}}
            )
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
                                hook_point="on.test.run",
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
                found, _ = dispatch.collect_findings(database, "w1", node, round_, registry)
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
    the resumed pass. Unlike `on.test.run`'s subprocess scope loop, an agent
    hook only ever mints one *real* session per pass, so this must stay
    last-wins: reading every same-head row here would hit `_FAILING_STATUSES`
    on the stale row and mint a bogus `from_blind_failure` critical finding
    for a failure that never happened."""

    async def scenario():
        database = await open_db(tmp_path)
        try:
            await mk_item(database)
            node = {"id": "verify", "tasks": ["on.review.x"]}
            registry = Registry(hooks={"on.review.x": {"kind": "agent", "command": "claude"}})
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
                        hook_point="on.review.x",
                        log_path=log_path,
                        result_path=str(tmp_path / f"{sid}.json"),
                        round=0,
                        head_sha="sha-a",
                    )
                )
                await database.write(
                    lambda c, sid=sid, status=status: store.session_exited(c, sid, status)
                )
            found, reported = dispatch.collect_findings(database, "w1", node, 0, registry)
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
            binding = {"kind": "subprocess", "command": ["true"]}
            repo_entry = {"test_scopes": [_FRONTEND_SCOPE, _BACKEND_SCOPE]}
            return dispatch._select_scopes(
                database, "w1", repo, "verify", "on.test.run", 0, binding, repo_entry
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
                        hook_point="on.test.run",
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

            binding = {"kind": "subprocess", "command": ["true"]}
            repo_entry = {"test_scopes": [_FRONTEND_SCOPE, _BACKEND_SCOPE]}
            return dispatch._select_scopes(
                database, "w1", repo, "verify", "on.test.run", 1, binding, repo_entry
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
                        hook_point="on.test.run",
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

            binding = {"kind": "subprocess", "command": ["true"]}
            repo_entry = {"test_scopes": [_FRONTEND_SCOPE, _BACKEND_SCOPE]}
            return dispatch._select_scopes(
                database, "w1", repo, "verify", "on.test.run", 1, binding, repo_entry
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
                    hook_point="on.test.run",
                    log_path="/l",
                    result_path="/r",
                    round=0,
                    head_sha=head,
                )
            )
            await database.write(lambda c: store.session_exited(c, "c1-gate", "done"))

            binding = {"kind": "subprocess", "command": ["true"]}
            repo_entry = {"test_scopes": [_FRONTEND_SCOPE, _BACKEND_SCOPE]}
            return dispatch._select_scopes(
                database, "w1", repo, "verify", "on.test.run", 0, binding, repo_entry
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
                        hook_point="on.test.run",
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

            binding = {"kind": "subprocess", "command": ["true"]}
            repo_entry = {"test_scopes": [_FRONTEND_SCOPE, _BACKEND_SCOPE]}
            return dispatch._select_scopes(
                database, "w1", repo, "verify", "on.test.run", 0, binding, repo_entry
            )
        finally:
            await database.close()

    to_run, _sandbox = asyncio.run(scenario())
    assert _cmds(to_run) == {("frontend-cmd",), ("backend-cmd",)}


def test_dispatch_runs_every_matched_scope_even_after_an_earlier_failure(tmp_path, monkeypatch):
    """C2 (Kraft-s7c04.9): two overlapping scopes, the changed path matches
    both -- an earlier scope's failure must not stop a later one from
    running at all. `_FAKE_AGENT`-less, so this is real subprocess dispatch,
    not the fake agent's own status."""
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
    assert marker2.read_text() == "ran", "a later scope must still run after an earlier failure"


def test_dispatch_aggregates_three_scopes_the_first_of_which_fails(tmp_path, monkeypatch):
    """C2 (Kraft-s7c04.9): three scopes, the first failing. All three must
    run -- the second and third are the defect this closes, "did not run"
    silently reading as a pass -- and the aggregate status still fails the
    node even though the last scope run was a pass."""
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")
    tracker = isolated_bd(tmp_path)
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
                            {"paths": ["**"], "command": f"{sys.executable} {fail_script}"},
                            {"paths": ["**"], "command": f"{sys.executable} {ok_script_a}"},
                            {"paths": ["**"], "command": f"{sys.executable} {ok_script_b}"},
                        ]
                    },
                    steering_dir=None,
                ),
            )
        finally:
            await database.close()

    status = asyncio.run(scenario())
    assert status == "failed", "the last scope's pass must not overwrite the aggregate"
    assert all(m.read_text() == "ran" for m in ran), "every scope must run, not just the first"


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


def test_a_failing_task_runs_its_bindings_repair_then_retries_that_task_alone(
    tmp_path, monkeypatch
):
    async def scenario():
        database, rd, worktree, wid, row = await _setup_measure_node_scenario(tmp_path)
        try:
            calls = []

            async def fake_dispatch_node(db_, run_dirs_, task_hook, node, row_, reg, wt, **kw):
                calls.append(task_hook)
                if task_hook == "on.a" and calls.count("on.a") == 1:
                    return "failed"
                return "done"

            monkeypatch.setattr(dispatch, "dispatch_node", fake_dispatch_node)
            registry = Registry(
                hooks={
                    "on.a": {"kind": "builtin", "handler": "noop", "on_failure": ["on.fix"]},
                    "on.b": {"kind": "builtin", "handler": "noop"},
                    "on.fix": {"kind": "builtin", "handler": "noop"},
                },
                raw={},
            )
            node = {"id": "n", "tasks": ["on.a", "on.b"], "on_failure": None}

            verdict, failed, excs = await dispatch.measure_node(
                database, rd, wid, node, row, registry, worktree, round=0
            )

            assert verdict == "ok"
            assert failed == []
            assert calls.count("on.fix") == 1
            assert calls.count("on.a") == 2, "the repaired task is re-dispatched"
            assert calls.count("on.b") == 1, "a passing sibling is never re-dispatched"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_a_task_repair_that_does_not_take_reports_the_original_failure(tmp_path, monkeypatch):
    async def scenario():
        database, rd, worktree, wid, row = await _setup_measure_node_scenario(tmp_path)
        try:

            async def fake_dispatch_node(db_, run_dirs_, task_hook, node, row_, reg, wt, **kw):
                return "done" if task_hook == "on.fix" else "failed"

            monkeypatch.setattr(dispatch, "dispatch_node", fake_dispatch_node)
            registry = Registry(
                hooks={
                    "on.a": {"kind": "builtin", "handler": "noop", "on_failure": ["on.fix"]},
                    "on.fix": {"kind": "builtin", "handler": "noop"},
                },
                raw={},
            )
            node = {"id": "n", "tasks": ["on.a"], "on_failure": None}

            verdict, failed, excs = await dispatch.measure_node(
                database, rd, wid, node, row, registry, worktree, round=0
            )

            assert verdict == "failed"
            assert failed == ["on.a"]
        finally:
            await database.close()

    asyncio.run(scenario())


def test_a_repair_is_not_run_for_a_pause_or_a_budget_stop(tmp_path, monkeypatch):
    """Only `_FAILING_STATUSES` is a task failing. A pause is a human's
    instruction and a budget breach is Kraft refusing to start -- neither is
    evidence about the task, so neither may spend a repair."""

    async def scenario():
        for i, stop in enumerate(("paused", dispatch.BUDGET, dispatch.RATE_LIMITED)):
            database, rd, worktree, wid, row = await _setup_measure_node_scenario(tmp_path / str(i))
            try:
                calls = []

                async def fake_dispatch_node(
                    db_, run_dirs_, task_hook, node, row_, reg, wt, _s=stop, _c=calls, **kw
                ):
                    _c.append(task_hook)
                    return _s

                monkeypatch.setattr(dispatch, "dispatch_node", fake_dispatch_node)
                registry = Registry(
                    hooks={
                        "on.a": {"kind": "builtin", "handler": "noop", "on_failure": ["on.fix"]},
                        "on.fix": {"kind": "builtin", "handler": "noop"},
                    },
                    raw={},
                )
                node = {"id": "n", "tasks": ["on.a"], "on_failure": None}
                await dispatch.measure_node(
                    database, rd, wid, node, row, registry, worktree, round=0
                )
                assert "on.fix" not in calls, f"{stop} must not spend a repair"
            finally:
                await database.close()

    asyncio.run(scenario())


def test_steps_run_in_order_and_a_failing_group_stops_the_node(tmp_path, monkeypatch):
    async def scenario():
        database, rd, worktree, wid, row = await _setup_measure_node_scenario(tmp_path)
        try:
            calls = []

            async def fake_dispatch_node(db_, run_dirs_, task_hook, node, row_, reg, wt, **kw):
                calls.append(task_hook)
                return "failed" if task_hook == "on.a" else "done"

            monkeypatch.setattr(dispatch, "dispatch_node", fake_dispatch_node)
            registry = Registry(
                hooks={
                    "on.a": {"kind": "builtin", "handler": "noop"},
                    "on.b": {"kind": "builtin", "handler": "noop"},
                },
                raw={},
            )
            node = {
                "id": "n",
                "steps": [["on.a"], ["on.b"]],
                "tasks": ["on.a", "on.b"],
                "on_failure": None,
            }

            verdict, failed, excs = await dispatch.measure_node(
                database, rd, wid, node, row, registry, worktree, round=0
            )

            assert verdict == "failed"
            assert failed == ["on.a"], "names the task that actually failed"
            assert "on.b" not in calls, "a later group must not run after an earlier one failed"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_a_later_group_runs_only_after_the_earlier_one_finishes(tmp_path, monkeypatch):
    async def scenario():
        database, rd, worktree, wid, row = await _setup_measure_node_scenario(tmp_path)
        try:
            order = []

            async def fake_dispatch_node(db_, run_dirs_, task_hook, node, row_, reg, wt, **kw):
                order.append(f"start:{task_hook}")
                await asyncio.sleep(0.01 if task_hook == "on.a" else 0)
                order.append(f"end:{task_hook}")
                return "done"

            monkeypatch.setattr(dispatch, "dispatch_node", fake_dispatch_node)
            registry = Registry(
                hooks={
                    "on.a": {"kind": "builtin", "handler": "noop"},
                    "on.b": {"kind": "builtin", "handler": "noop"},
                },
                raw={},
            )
            node = {
                "id": "n",
                "steps": [["on.a"], ["on.b"]],
                "tasks": ["on.a", "on.b"],
                "on_failure": None,
            }

            await dispatch.measure_node(database, rd, wid, node, row, registry, worktree, round=0)

            assert order.index("end:on.a") < order.index("start:on.b")
        finally:
            await database.close()

    asyncio.run(scenario())


def test_one_group_still_runs_concurrently(tmp_path, monkeypatch):
    """The no-regression case: every template today is one group."""

    async def scenario():
        database, rd, worktree, wid, row = await _setup_measure_node_scenario(tmp_path)
        try:
            order = []

            async def fake_dispatch_node(db_, run_dirs_, task_hook, node, row_, reg, wt, **kw):
                order.append(f"start:{task_hook}")
                await asyncio.sleep(0.01 if task_hook == "on.a" else 0)
                order.append(f"end:{task_hook}")
                return "done"

            monkeypatch.setattr(dispatch, "dispatch_node", fake_dispatch_node)
            registry = Registry(
                hooks={
                    "on.a": {"kind": "builtin", "handler": "noop"},
                    "on.b": {"kind": "builtin", "handler": "noop"},
                },
                raw={},
            )
            node = {
                "id": "n",
                "steps": [["on.a", "on.b"]],
                "tasks": ["on.a", "on.b"],
                "on_failure": None,
            }

            await dispatch.measure_node(database, rd, wid, node, row, registry, worktree, round=0)

            assert order.index("start:on.b") < order.index("end:on.a"), "overlapped, not serialized"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_a_repairs_own_on_failure_is_never_dispatched(tmp_path, monkeypatch):
    """One repair layer only. A repair that fails is a blocker Kraft does not
    understand; pulling a second lever on it is how a loop starts."""

    async def scenario():
        database, rd, worktree, wid, row = await _setup_measure_node_scenario(tmp_path)
        try:
            calls = []

            async def fake_dispatch_node(db_, run_dirs_, task_hook, node, row_, reg, wt, **kw):
                calls.append(task_hook)
                return "failed"

            monkeypatch.setattr(dispatch, "dispatch_node", fake_dispatch_node)
            registry = Registry(
                hooks={
                    "on.a": {"kind": "builtin", "handler": "noop", "on_failure": ["on.fix"]},
                    "on.fix": {
                        "kind": "builtin",
                        "handler": "noop",
                        "on_failure": ["on.fix2"],
                    },
                    "on.fix2": {"kind": "builtin", "handler": "noop"},
                },
                raw={},
            )
            node = {"id": "n", "tasks": ["on.a"], "on_failure": None}
            await dispatch.measure_node(database, rd, wid, node, row, registry, worktree, round=0)
            assert "on.fix2" not in calls
        finally:
            await database.close()

    asyncio.run(scenario())


def test_measure_node_reads_head_once_per_task_not_once_per_node(tmp_path, monkeypatch):
    """Kraft-37myi: the node-entry snapshot is wrong the moment anything
    dispatched inside the node moves HEAD -- a task-level repair's commit
    (Task 3) or, later, an ordered step that rebases."""

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
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
            node = {"id": "n", "tasks": ["on.a", "on.b"]}

            reads = []
            real_git_read = dispatch._config.git_read

            def counting_git_read(path, *args, **kwargs):
                if args[:1] == ("rev-parse",):
                    reads.append(args)
                return real_git_read(path, *args, **kwargs)

            monkeypatch.setattr(dispatch._config, "git_read", counting_git_read)

            async def fake_dispatch_node(db_, run_dirs_, task_hook, *a, **kw):
                return "done"

            monkeypatch.setattr(dispatch, "dispatch_node", fake_dispatch_node)

            row = database.read(
                lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (wid,)).fetchone()
            )
            await dispatch.measure_node(
                database, rd, wid, node, row, Registry(hooks={}), worktree, round=0
            )
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
    binding = {"kind": "agent", "command": "claude", "skill": "plan", "artifact": "plan"}
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
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    seen = {}

    async def fake_run_agent_task(db_, run_dirs_, **kw):
        seen["instruction"] = kw["task_instruction"]
        return "done"

    monkeypatch.setattr("kraft.executor.dispatch._agent.run_agent_task", fake_run_agent_task)

    async def scenario(source):
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
                steer=executor.Steer("findings left unresolved: x", source=source),
            )
        finally:
            await database.close()
        return seen["instruction"]

    seeded = asyncio.run(scenario("seeded"))
    assert "no human" in seeded
    assert "A human has steered" not in seeded

    typed = asyncio.run(scenario("human"))
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
                    hook_point="escalation",
                    log_path=str(tmp_path / "esc.log"),
                    result_path=str(tmp_path / "esc.json"),
                    round=0,
                )
            )
            (tmp_path / "esc.json").write_text(
                json.dumps({"status": "needs_context", "question": "which base image?"})
            )
            await database.write(lambda c: store.session_exited(c, "esc", "needs_context"))
            node = {"id": "verify", "tasks": ["on.test.run"]}
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
            await database.write(
                lambda c: store.create_session(
                    c,
                    id="meas",
                    work_item_id="wi",
                    node_id="verify",
                    hook_point="on.test.run",
                    log_path=str(tmp_path / "meas.log"),
                    result_path=str(tmp_path / "meas.json"),
                    round=0,
                )
            )
            (tmp_path / "meas.json").write_text(
                json.dumps({"status": "needs_context", "question": "which python?"})
            )
            await database.write(lambda c: store.session_exited(c, "meas", "needs_context"))
            node = {"id": "verify", "tasks": ["on.test.run"]}
            assert dispatch.needs_context_question(database, "wi", node, 0) == "which python?"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_reentry_is_not_stopped_by_the_previous_passs_fix_question(tmp_path):
    """A gate rejection or ci_wait poll re-entering a fix_loop node must not
    inherit the last pass's fix-task needs_context (review finding 5).

    walk_node now seeds `round` from the persisted counter, and the counter
    survives a non-resume re-entry -- so the previous pass's round-N fix row is
    still the latest for its hook point when the new pass takes its first
    measurement, and nothing this pass writes can displace it until it bumps to
    N+1. The unflagged call must still return the question: surfacing a fix
    task's own needs_context one iteration later is deliberate.
    """

    async def scenario():
        database = await open_db(tmp_path)
        try:
            await mk_item(database, "wi")
            await database.write(
                lambda c: store.create_session(
                    c,
                    id="fix2",
                    work_item_id="wi",
                    node_id="verify",
                    hook_point="on.implementation.start",
                    log_path=str(tmp_path / "fix2.log"),
                    result_path=str(tmp_path / "fix2.json"),
                    round=2,
                )
            )
            (tmp_path / "fix2.json").write_text(
                json.dumps({"status": "needs_context", "question": "which migration?"})
            )
            await database.write(lambda c: store.session_exited(c, "fix2", "needs_context"))
            node = {"id": "verify", "tasks": ["on.test.run"]}
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
    binding = {"kind": "agent", "command": "claude", "skill": "plan", "artifact": "plan"}
    (tmp_path / ".engineering" / "plans").mkdir(parents=True)
    (tmp_path / ".engineering" / "plans" / "w1.md").write_text("# the plan\n")
    note = "tidied the swallowed OSError and committed it"

    out = executor.steer_prefix(binding, {"id": "w1"}, tmp_path, note, source="gate_review")

    assert note in out
    assert "No human wrote it" in out
    assert "may have committed changes in this worktree itself" in out
    assert "A human has steered" not in out
    assert "A human read" not in out


def test_the_implementer_has_no_skill_so_its_brief_stays_the_task():
    """Task 2 keys off `skill:`. If someone gives the implementation hook a
    skill, every fix round silently becomes "implementing is another node's
    job" -- addressed to the node that implements."""
    hooks = load_registry(_REPO_ROOT / "templates" / "registry.yaml").hooks
    assert "skill" not in hooks["on.implementation.start"]


def test_a_chain_can_run_two_harnesses(tmp_path, monkeypatch):
    """The point of the whole agent-harnesses spec, exercised with no tokens:
    one node on claude, the next on codex, both reaching the same
    result-file contract."""
    monkeypatch.delenv("KRAFT_FAKE_AGENT", raising=False)
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    argv_log = tmp_path / "argv.jsonl"
    monkeypatch.setenv("KRAFT_FAKE_AGENT_ARGV_LOG", str(argv_log))
    fake = f"{sys.executable} {_FAKE_AGENT}"

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            registry = Registry(
                hooks={
                    "on.env.prepare": {"kind": "builtin", "handler": "env_setup"},
                    "on.a": {"kind": "agent", "command": fake},
                    "on.b": {"kind": "agent", "command": fake, "harness": "codex"},
                }
            )
            tmpl = Template(
                id="two-harness",
                nodes=[
                    {"id": "env_setup", "tasks": ["on.env.prepare"], "gate_after": None},
                    {"id": "claude_node", "tasks": ["on.a"], "gate_after": None},
                    {"id": "codex_node", "tasks": ["on.b"], "gate_after": None},
                ],
            )
            wid = await executor.intake(
                database,
                rd,
                title="make the failing test pass",
                repo=str(repo),
                template=tmpl,
                bd_cwd=str(tracker),
            )
            result = await executor.run(
                database, rd, work_item_id=wid, registry=registry, bd_cwd=str(tracker)
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


def test_chain_review_context_shows_the_tail_it_is_revising():
    """The reviewer must re-emit the complete tail and, since Kraft-bcg25, may
    group it -- and nothing told it what the tail currently is. It got the
    grouping right on this repo only because the worker's worktree *is* this
    repo and `templates/default.yaml` was there to read; on any other repo the
    shape is not on disk anywhere it can reach."""
    from kraft.executor import prompts
    from kraft.templates import Registry

    registry = Registry(
        hooks={
            "on.test.run": {"kind": "subprocess"},
            "on.review.local.run": {"kind": "agent", "command": "claude"},
            "on.mr.open": {"kind": "forge", "handler": "open_mr"},
        }
    )
    text = prompts.chain_review_context(
        [
            {
                "id": "verify",
                "tasks": ["on.test.run", "on.review.local.run"],
                "steps": [["on.test.run"], ["on.review.local.run"]],
            },
            {"id": "open_mr", "tasks": ["on.mr.open"], "steps": [["on.mr.open"]]},
        ],
        registry,
    )
    assert "steps: [on.test.run] -> [on.review.local.run]" in text
    assert "tasks: [on.mr.open]" in text


def test_measure_node_stops_at_a_rebase_that_moved_the_base(tmp_path, monkeypatch):
    """The later groups must not run against a base the first group just moved."""
    from kraft.executor.context import BASE_MOVED

    async def scenario():
        database, rd, worktree, wid, row = await _setup_measure_node_scenario(tmp_path)
        try:
            calls = []

            async def fake_dispatch_node(db_, run_dirs_, task_hook, node, row_, reg, wt, **kw):
                calls.append(task_hook)
                return BASE_MOVED if task_hook == "on.a" else "done"

            monkeypatch.setattr(dispatch, "dispatch_node", fake_dispatch_node)
            registry = Registry(
                hooks={
                    "on.a": {"kind": "builtin", "handler": "noop"},
                    "on.b": {"kind": "builtin", "handler": "noop"},
                },
                raw={},
            )
            node = {"id": "n", "steps": [["on.a"], ["on.b"]], "tasks": ["on.a", "on.b"]}
            verdict, failed, _ = await dispatch.measure_node(
                database, rd, wid, node, row, registry, worktree, round=0
            )
            assert verdict == BASE_MOVED
            assert failed == []
            assert calls == ["on.a"]
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


def test_a_binding_repair_is_given_the_failure_it_is_repairing(tmp_path, monkeypatch):
    """on.ci.repair reported the diagnosis was absent from its prompt and from
    the item's events; a node-level repair gets a seeded context, a binding one
    got whatever unrelated steer was in flight."""

    async def scenario():
        database, rd, worktree, wid, row = await _setup_measure_node_scenario(tmp_path)
        try:
            steers = {}

            async def fake_dispatch_node(db_, run_dirs_, task_hook, node, row_, reg, wt, **kw):
                steers[task_hook] = kw.get("steer")
                return "failed" if task_hook == "on.a" else "done"

            monkeypatch.setattr(dispatch, "dispatch_node", fake_dispatch_node)
            registry = Registry(
                hooks={
                    "on.a": {"kind": "builtin", "handler": "noop", "on_failure": ["on.fix"]},
                    "on.fix": {"kind": "builtin", "handler": "noop"},
                },
                raw={},
            )
            node = {"id": "n", "tasks": ["on.a"], "on_failure": None}
            await dispatch.measure_node(
                database, rd, wid, node, row, registry, worktree, round=0, steer=None
            )
            repair = steers["on.fix"]
            assert repair.source == "seeded"
            assert "on.a" in repair.take()

            steers.clear()
            from kraft.executor.context import Steer

            human = Steer("look at the lockfile")
            await dispatch.measure_node(
                database, rd, wid, node, row, registry, worktree, round=0, steer=human
            )
            text = steers["on.fix"].take()
            assert text.startswith("look at the lockfile") and "on.a" in text
        finally:
            await database.close()

    asyncio.run(scenario())


def test_measure_node_records_the_group_it_reached(tmp_path, monkeypatch):
    """current_node_id alone cannot say 'step 3 of 4', so every re-entry
    restarted the node."""

    async def scenario():
        database, rd, worktree, wid, row = await _setup_measure_node_scenario(tmp_path)
        try:
            seen = {}

            async def fake_dispatch_node(db_, run_dirs_, task_hook, node, row_, reg, wt, **kw):
                seen[task_hook] = database.read(
                    lambda c: c.execute(
                        "SELECT current_step FROM work_items WHERE id = ?", (wid,)
                    ).fetchone()[0]
                )
                return "failed" if task_hook == "on.c" else "done"

            monkeypatch.setattr(dispatch, "dispatch_node", fake_dispatch_node)
            node = {
                "id": "n",
                "tasks": ["on.a", "on.b", "on.c"],
                "steps": [["on.a"], ["on.b"], ["on.c"]],
            }
            await dispatch.measure_node(
                database, rd, wid, node, row, Registry(hooks={}, raw={}), worktree, round=0
            )
            assert seen == {"on.a": 0, "on.b": 1, "on.c": 2}
        finally:
            await database.close()

    asyncio.run(scenario())


def test_a_resumed_node_skips_passed_groups_but_still_runs_its_rebase_step(tmp_path, monkeypatch):
    """Skipping the rebase on a resume is how a moved base goes unnoticed. The
    rebase is found through its binding, not its name."""

    async def scenario():
        database, rd, worktree, wid, row = await _setup_measure_node_scenario(tmp_path)
        try:
            dispatched = []

            async def fake_dispatch_node(db_, run_dirs_, task_hook, node, row_, reg, wt, **kw):
                dispatched.append(task_hook)
                return "done"

            monkeypatch.setattr(dispatch, "dispatch_node", fake_dispatch_node)
            registry = Registry(
                hooks={
                    "on.sync": {"kind": "builtin", "handler": "mr_rebase"},
                    "on.prep": {"kind": "builtin", "handler": "noop"},
                    "on.test": {"kind": "builtin", "handler": "noop"},
                    "on.poll": {"kind": "builtin", "handler": "noop"},
                },
                raw={},
            )
            steps = [["on.sync"], ["on.prep"], ["on.test"], ["on.poll"]]
            node = {"id": "n", "tasks": [t for g in steps for t in g], "steps": steps}
            await dispatch.measure_node(
                database, rd, wid, node, row, registry, worktree, round=0, start_step=3
            )
            assert dispatched == ["on.sync", "on.poll"]
            cursor = database.read(
                lambda c: c.execute(
                    "SELECT current_step FROM work_items WHERE id = ?", (wid,)
                ).fetchone()[0]
            )
            assert cursor == 3
        finally:
            await database.close()

    asyncio.run(scenario())
