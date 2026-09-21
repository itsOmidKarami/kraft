"""`dispatch.dispatch_node`: which adapter a task reaches, and what an agent
launch is handed -- its prompt, its overrides, its harness, its safety rule.

Scope selection is tests/executor/test_scopes.py; a measuring pass and what it
reads back, tests/executor/test_measurement.py; a task that could not start and
the stop reason that names it, tests/executor/test_walk.py."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from support.harness import (
    fake_docker_bin,
    fake_harness_home,
    seed_v1_library,
    v1_chain,
    v1_item,
    v1_walk,
    write_harness_profiles,
)

from kraft import executor, store
from kraft.adapters import agent as agent_mod
from kraft.executor import dispatch
from kraft.executor.context import LaunchContext

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FAKE_AGENT = Path(__file__).resolve().parents[1] / "support" / "fake_agent.py"
NO_SETUP = LaunchContext(repo_entry={"setup_command": ""}, steering_dir=None)


def _agent(task_id="implement", **fields):
    return {"id": task_id, "kind": "agent", "harness": "fake", "prompt": "Do it.", **fields}


def _exec(node_id, *tasks):
    return {"id": node_id, "kind": "exec", "tasks": list(tasks)}


def _implementer_prompt(fake_agent) -> str:
    """The shipped implementer's own `prompt:`, which a V1 agent instruction
    leads with -- read from the chain rather than restated here."""
    return fake_agent.quick_task.nodes[0].steps[0].tasks[0].task.prompt


def _walk(tmp_path, repo, chain, **kwargs):
    """File `chain` (node list or `ResolvedChain`) on `repo` and walk it once."""
    return v1_walk(tmp_path, v1_chain(chain, repo=repo), repo=repo, **kwargs)


def _attach(repo, kind, text="# doc\n"):
    """An attachment record for a document at `.engineering/<kind>s/a.md`, on
    disk in `repo` where the worktree copies it from."""
    path = f".engineering/{kind}s/a.md"
    (repo / path).parent.mkdir(parents=True, exist_ok=True)
    (repo / path).write_text(text)
    return {"kind": kind, "path": path}


async def _dispatch_each_node(it, *, launch=None, **kwargs):
    """`dispatch_node` on the first task of each of the item's nodes, in order."""
    for node in it.chain.chain.nodes:
        await dispatch.dispatch_node(
            it.database,
            it.run_dirs,
            node.steps[0].tasks[0],
            node,
            it.row(),
            it.repo,
            launch=launch,
            **kwargs,
        )


# -- the prompt an agent is sent ----------------------------------------------


async def _sent_prompts(tmp_path, repo, fake_agent, **item_kwargs):
    """Every prompt a real walk of quick-task sends, for an item filed with
    `item_kwargs` (title, description, attachments)."""
    status, *_ = await _walk(tmp_path, repo, fake_agent.quick_task, **item_kwargs)
    assert status == "completed"
    return fake_agent.prompts()


async def test_dispatch_puts_the_attachment_note_after_the_title(tmp_path, repo, fake_agent):
    """Unit tests on attachment_note/attachments_of alone don't prove dispatch_node
    composes them correctly (wrong order, or dropping the note entirely, would
    still pass those). This drives a real agent launch and reads back the exact
    prompt sent, the way test_fix_loop asserts steer-note ordering.

    V1: an agent task's instruction leads with the task's own `prompt:`, and
    the brief (title first) follows it."""
    title = "make the failing test pass"

    sent = await _sent_prompts(
        tmp_path, repo, fake_agent, title=title, attachments=[_attach(repo, "spec")]
    )

    # quick-task's only agent dispatch is the implementation node.
    assert len(sent) == 1
    prompt = sent[0]
    assert prompt.startswith(f"{_implementer_prompt(fake_agent)}\n\n{title}")
    assert prompt.index("Spec: .engineering/specs/a.md") > prompt.index(title)
    assert "Do not re-plan." in prompt
    # The bead note (Kraft-a03) is appended after everything else, including
    # the attachment note.
    assert prompt.index("Do not re-plan.") < prompt.index("Do not run `bd close`")
    assert prompt.rstrip().endswith(executor.BEAD_NOTE.strip())


async def test_dispatch_puts_the_description_after_the_title(tmp_path, repo, fake_agent):
    """The description is the brief; the title is a label. Both reach the agent,
    in that order. This is the assertion the whole feature exists for."""
    title = "make the failing test pass"
    description = "test_widget_totals asserts a float; the code returns Decimal."

    [prompt] = await _sent_prompts(tmp_path, repo, fake_agent, title=title, description=description)

    assert title in prompt
    assert description in prompt
    assert prompt.index(title) < prompt.index(description)


async def test_dispatch_without_a_description_sends_the_title_alone(tmp_path, repo, fake_agent):
    """No description must reproduce today's instruction exactly — no stray blank
    lines, no 'None' rendered into the prompt. V1: after the task's own
    `prompt:`, which leads every agent instruction."""
    title = "make the failing test pass"

    [prompt] = await _sent_prompts(tmp_path, repo, fake_agent, title=title)

    assert prompt.strip() == f"{_implementer_prompt(fake_agent)}\n\n{title}{executor.BEAD_NOTE}"


async def test_the_implementer_is_told_to_report_progress_through_a_tasked_plan(
    tmp_path, repo, fake_agent
):
    plan = _attach(repo, "plan", "# p\n\n## Task 1 — parse\n\n## Task 2 — serve\n")

    [prompt] = await _sent_prompts(tmp_path, repo, fake_agent, attachments=[plan])

    assert "This plan has 2 tasks." in prompt
    assert "`kraft item progress K`" in prompt
    assert "(task K)" in prompt
    # after the attachment note, before the bead note that always closes the prompt
    assert (
        prompt.index("Do not re-plan.")
        < prompt.index("This plan has 2 tasks.")
        < prompt.index("Do not run `bd close`")
    )


async def test_a_plan_without_task_headings_gets_no_progress_note(tmp_path, repo, fake_agent):
    plan = _attach(repo, "plan", "# p\n\n## Step one\n")

    [prompt] = await _sent_prompts(tmp_path, repo, fake_agent, attachments=[plan])

    assert "kraft item progress" not in prompt


async def test_a_hook_with_its_own_skill_is_not_told_to_implement(item_on, fake_agent):
    """A reviewer task carries a skill. It must not be handed the implementer's
    "follow the plan, do not re-plan" -- that framing is why the
    MR-description node ran the full test suite (Kraft-s7c04.52). V1 keys the
    two framings on the task having a `skill:`, not on a hook name."""
    it = await item_on(
        [
            _exec("implementation", _agent()),
            _exec("verify", _agent("review", skill="kraft:code-review")),
        ],
        attachments=[
            {"kind": "spec", "path": ".engineering/specs/a.md"},
            {"kind": "plan", "path": ".engineering/plans/a.md"},
        ],
    )

    await _dispatch_each_node(it, launch=LaunchContext(repo_entry=None, steering_dir=None))

    impl_prompt, review_prompt = fake_agent.prompts()
    assert "Do not re-plan." in impl_prompt
    assert "Implementing this work item is a different node's job" not in impl_prompt

    assert "Do not re-plan" not in review_prompt
    assert "Follow the documents above" not in review_prompt
    assert "Implementing this work item is a different node's job" in review_prompt
    assert "Judge the change against them." in review_prompt


async def test_the_implementer_is_told_which_commands_gate_its_paths(item_on, fake_agent):
    """49c0cefd's agent could run `just e2e-ci` and was never told it existed
    (Kraft-s7c04.8). The implementation prompt now carries the repo's
    path->command mapping. An agent task with a skill of its own -- a
    different job, dispatched in the same run -- must not get it
    (Kraft-s7c04.45: keyed on the task, not the node id)."""
    it = await item_on(
        [
            _exec("implementation", _agent()),
            _exec("open_mr", _agent("describe", skill="kraft:mr-metadata")),
        ]
    )
    scopes = [
        {"paths": ["frontend/**"], "command": "just test-ui"},
        {"paths": ["frontend/**"], "command": "just e2e-ci"},
        {"paths": ["src/**", "tests/**"], "command": "just ci-test"},
    ]

    await _dispatch_each_node(
        it, launch=LaunchContext(repo_entry={"test_scopes": scopes}, steering_dir=None)
    )

    impl_prompt, other_prompt = fake_agent.prompts()
    for cmd in ("just test-ui", "just e2e-ci", "just ci-test"):
        assert cmd in impl_prompt
        assert cmd not in other_prompt


async def test_dispatch_carries_the_notes_authorship_into_the_prompt(
    item_on, fake_agent, monkeypatch
):
    """`dispatch_node` reads `Steer.source` off the note it takes, so a seeded
    steer reaches the agent framed as Kraft's (the framing itself is
    test_prompts' `steer_prefix` table). Without it the field exists but never
    reaches the prompt."""
    seen = {}

    async def fake_run_agent_task(db_, run_dirs_, *, work_item_id, **kw):
        seen[work_item_id] = kw["task_instruction"]
        return "done"

    monkeypatch.setattr(dispatch._agent, "run_agent_task", fake_run_agent_task)
    for source in ("seeded", "human"):
        it = await item_on([_exec("verify", _agent("review"))], wid=source)
        await _dispatch_each_node(
            it,
            launch=LaunchContext(repo_entry=None, steering_dir=None),
            steer=executor.Steer("findings left unresolved: x", source=source),
        )

    assert "no human" in seen["seeded"]
    assert "A human has steered" not in seen["seeded"]
    assert seen["human"].startswith("A human has steered this run:")


# -- overrides and the launch context ------------------------------------------


async def test_repo_default_model_reaches_the_agent_launch(tmp_path, repo, fake_agent):
    """`executor.run_once` -> `walk.walk_node` -> `dispatch.measure_node` ->
    `dispatch.dispatch_node` must carry the launch context all the way to
    `run_agent_task`, or a repo's configured default_model silently never
    reaches the agent.

    An agent task that sets no `model:` of its own: the shipped implementer
    names one, and a task's own model rightly beats the repo default."""
    status, *_ = await _walk(
        tmp_path,
        repo,
        [_exec("implementation", _agent())],
        repo_entry={"default_model": "haiku", "setup_command": ""},
    )

    assert status == "completed"
    [argv] = fake_agent.argv()  # the chain's only agent dispatch
    assert argv[argv.index("--model") + 1] == "haiku"


@pytest.mark.parametrize(
    ("task", "item", "node", "escalate", "flag", "expected"),
    [
        (
            {"model": "task-model"},
            {"model": "item-model"},
            {"model": "node-model"},
            False,
            "--model",
            "node-model",
        ),
        # `effort` takes a different path out of `resolve_invocation` than
        # `model` does (task-level only, no repo default).
        ({"effort": "low"}, {"effort": "medium"}, {"effort": "high"}, False, "--effort", "high"),
        # The fix loop's capability bump reaches the wire as `--model` from a
        # different branch of `resolve_invocation` (`escalate=True`). A V1 task
        # has no `escalate_model` of its own, so the tiers are item and node.
        (
            {"model": "task-model"},
            {"model": "item-model", "escalate_model": "item-escalate"},
            {"model": "node-model", "escalate_model": "node-escalate"},
            True,
            "--model",
            "node-escalate",
        ),
    ],
    ids=["model", "effort", "escalate_model"],
)
async def test_node_override_beats_item_override_beats_the_task(
    item_on, fake_agent, task, item, node, escalate, flag, expected
):
    """Precedence (Kraft-df4tc design point 2): node_overrides > item-wide
    agent_overrides > the task's own field, covered per field (spec section 6)
    rather than once for the whole dial, with all three tiers set in one item so
    a bug that collapses any two shows up as a wrong flag on the wire. Direct
    `dispatch_node`: the merge under test lives at that one call site."""
    it = await item_on([_exec("implementation", _agent(**task))])
    await it.database.write(lambda c: store.set_agent_overrides(c, it.id, json.dumps(item)))
    await it.database.write(lambda c: store.set_node_overrides(c, it.id, {"implementation": node}))

    await _dispatch_each_node(it, escalate=escalate)

    argv = fake_agent.argv()[0]
    assert argv[argv.index(flag) + 1] == expected


# -- what a node's tasks leave behind --------------------------------------------


async def test_run_gathers_multi_task_node(tmp_path, repo):
    status, evts, sessions, _row = await _walk(
        tmp_path,
        repo,
        [
            _exec(
                "work",
                {"id": "a", "kind": "subprocess", "command": "true"},
                {"id": "b", "kind": "subprocess", "command": "true"},
            )
        ],
    )

    assert status == "completed"
    assert [s["node_id"] for s in sessions] == ["work", "work"]
    # One node: V1 has no `env_setup` node beside it.
    assert [e["type"] for e in evts].count("node_completed") == 1


async def test_agent_node_commits_what_the_worker_left_behind(tmp_path, repo, fake_agent):
    """Kraft-7fip. The fake agent edits calc.py and never commits it, which is
    exactly what a real worker did on work item 2506daf4: `verify` passed on the
    files on disk, then `on.mr.open` refused the dirty worktree two nodes later
    and a human had to `git commit` by hand. Kraft owns the worktree, so the
    edit must be in a commit by the time the node is done."""
    status, *_ = await _walk(tmp_path, repo, fake_agent.quick_task)
    assert status == "completed"
    worktree = tmp_path / "run" / "worktrees" / "w1"

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


async def test_a_failed_straggler_sweep_does_not_fail_a_good_agent_run(
    tmp_path, repo, fake_agent, monkeypatch
):
    """The sweep is a courtesy, not the task. An index lock a co-task holds or
    an unset user.email would otherwise turn a successful agent run into a
    failed node — and losing the sweep only puts us back where Kraft-7fip
    found us, with the work on disk and `_assert_clean` naming it at open_mr.

    It must not vanish silently either (Kraft-hf12): a `sweep_failed` event
    is the only trail back to why that eventual `open_mr` refusal happened,
    since the exception itself only ever reached the server's own log.
    """

    async def boom(*args, **kwargs):
        raise dispatch._forge.ForgeError("git commit failed: .git/index.lock exists")

    monkeypatch.setattr(dispatch._forge, "commit_stragglers", boom)

    status, evts, _sessions, _row = await _walk(tmp_path, repo, fake_agent.quick_task)

    assert status == "completed"
    swept = [e for e in evts if e["type"] == "sweep_failed"]
    assert swept, "no sweep_failed event survived the swallowed ForgeError"
    assert "index.lock" in swept[0]["payload"]["error"]


async def test_a_sandboxed_subprocess_hook_actually_runs_through_docker(
    tmp_path, repo, monkeypatch
):
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
    marker = tmp_path / "ran.txt"
    command = f"{sys.executable} -c \"open({str(marker)!r}, 'w').write('ran')\""

    await _walk(
        tmp_path,
        repo,
        [_exec("verify", {"id": "suite", "kind": "subprocess", "command": command})],
        repo_entry={"setup_command": "", "sandbox": {"kind": "docker", "image": "kraft-worker:py"}},
    )

    assert marker.read_text() == "ran"
    assert called.exists()


async def test_a_chain_can_run_two_harnesses(tmp_path, repo, fake_agent):
    """The point of the whole agent-harnesses spec, exercised with no tokens:
    one node on claude, the next on codex, both reaching the same
    result-file contract."""
    # `codex` overlaid like the fixture overlays `claude`: the bundled
    # declaration, launching the fake agent.
    bundled = (_REPO_ROOT / "src" / "kraft" / "harnesses" / "codex.yaml").read_text()
    overlay = Path(os.environ["KRAFT_HOME"]) / "templates" / "harnesses" / "codex.yaml"
    fake_codex = json.dumps([sys.executable, str(_FAKE_AGENT), "codex", "exec"])
    overlay.write_text(bundled.replace("command: [codex, exec]", f"command: {fake_codex}"))
    write_harness_profiles(overlay.parents[1], {"codex": {"provider": "codex"}})

    status, *_ = await _walk(
        tmp_path,
        repo,
        [
            _exec("claude_node", _agent("a", harness="claude")),
            _exec("codex_node", _agent("b", harness="codex")),
        ],
    )

    assert status == "completed"
    # Two harnesses, two spellings, one contract.
    records = fake_agent.argv()
    assert any("--append-system-prompt" in r for r in records)
    assert any(any(a.startswith("developer_instructions=") for a in r) for r in records)


# -- Template Schema V1: typed dispatch ------------------------------------------


async def _dispatch_one(it, launch=NO_SETUP):
    node = it.chain.chain.nodes[0]
    return await dispatch.dispatch_node(
        it.database, it.run_dirs, node.steps[0].tasks[0], node, it.row(), it.repo, launch=launch
    )


def _one_task(raw, node_id="verify", step_id="checks"):
    return [{"id": node_id, "kind": "exec", "steps": [{"id": step_id, "tasks": [raw]}]}]


async def test_each_task_kind_reaches_its_own_adapter(item_on, tmp_path, monkeypatch):
    """Ruling 3: the task's *type* chooses the adapter, and the model's own
    fields -- not a looked-up binding -- are what the adapter is handed. The
    adapters are recorded rather than run: this is about which one is picked and
    with what, and each one's own behaviour has its own tests."""
    seen: dict[str, dict] = {}

    def record(name, first_only=False):
        async def adapter(_db, _rd, **kw):
            if not (first_only and name in seen):
                seen[name] = kw
            return "done"

        return adapter

    async def commit_stragglers(*_a, **_k):
        return None

    monkeypatch.setattr(dispatch._subprocess, "run_task", record("subprocess", first_only=True))
    monkeypatch.setattr(dispatch._agent, "run_agent_task", record("agent"))
    monkeypatch.setattr(dispatch._forge, "run_task", record("forge"))
    monkeypatch.setattr(dispatch._builtins, "restore_branch", lambda *a, **k: None)
    monkeypatch.setattr(dispatch._forge, "commit_stragglers", commit_stragglers)
    fake_harness_home(tmp_path, [sys.executable, "-c", ""])

    for raw, entry in (
        ({"id": "t", "kind": "subprocess", "command": "just ci-test"}, None),
        (
            {"id": "t", "kind": "builtin", "ref": "kraft.verify_changed_test_scopes"},
            {"setup_command": "", "test_command": "just test"},
        ),
        ({"id": "t", "kind": "agent", "harness": "fake", "prompt": "do the work"}, None),
        ({"id": "t", "kind": "forge", "target": "mr.open_draft"}, None),
    ):
        it = await item_on(_one_task(raw), wid=raw["kind"])
        launch = LaunchContext(repo_entry=entry or {"setup_command": ""}, steering_dir=None)
        assert await _dispatch_one(it, launch) == "done", raw

    assert set(seen) == {"subprocess", "agent", "forge"}
    # The subprocess task's own command, split by the adapter's boundary, not a
    # registry list. (The builtin ran through the same adapter with the repo's
    # command -- test_scopes covers that side.)
    assert seen["subprocess"]["cmd"] == ["just", "ci-test"]
    assert seen["agent"]["harness"] == "fake"
    assert "do the work" in seen["agent"]["task_instruction"]
    # The chain writes the lifecycle point; the adapter owns which handler runs.
    assert seen["forge"]["handler"] == "open_mr"
    assert seen["forge"]["hook_point"] == "verify.checks.t"


async def test_an_agent_task_contract_precedes_its_skill_and_steering(
    item_on, tmp_path, repo, monkeypatch
):
    """`agent-task-contract-precedes-skill-and-steering`: Kraft's own output and
    lifecycle contract is delivered first, then the selected method, then
    steering -- and a selected skill cannot displace the contract."""
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")
    argv_log = tmp_path / "argv.txt"
    monkeypatch.setenv("KRAFT_FAKE_AGENT_ARGV_LOG", str(argv_log))
    fake_harness_home(tmp_path, [sys.executable, str(_FAKE_AGENT)])
    skills = tmp_path / "skills"
    (skills / "house-method").mkdir(parents=True)
    (skills / "house-method" / "SKILL.md").write_text("THE-METHOD\n")
    task = {
        "id": "write",
        "kind": "agent",
        "harness": "fake",
        "prompt": "Produce the specification.",
        "skill": "house-method",
        "steering": ["project-standards"],
    }
    it = await item_on(
        v1_chain(
            _one_task(task, "spec", "author"),
            repo=repo,
            steering={"project-standards": "THE-STEERING\n"},
        )
    )

    status = await _dispatch_one(
        it,
        LaunchContext(repo_entry={"setup_command": ""}, steering_dir=None, skills_dir=skills),
    )

    assert status == "done"
    argv = json.loads(argv_log.read_text().splitlines()[0])
    context = argv[argv.index("--append-system-prompt") + 1]
    assert context.index("write a short session summary") < context.index("THE-METHOD")
    assert context.index("THE-METHOD") < context.index("THE-STEERING")


async def test_a_typed_agent_task_reports_the_providers_own_normalized_result(
    item_on, tmp_path, monkeypatch
):
    """`provider-owns-runtime-mechanics` (normalized task results): the status a
    typed agent task reports is the provider's own result, normalized by the
    adapter into Kraft's vocabulary and written onto the session row -- Kraft
    does not infer it from an exit code."""
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")
    monkeypatch.setenv("KRAFT_FAKE_AGENT_STATUS", "done_with_concerns")
    monkeypatch.setenv("KRAFT_FAKE_AGENT_CONCERNS", "the totals are still Decimal")
    fake_harness_home(tmp_path, [sys.executable, str(_FAKE_AGENT)])
    raw = {"id": "write", "kind": "agent", "harness": "fake", "prompt": "do the work"}
    it = await item_on(_one_task(raw, "spec", "author"))

    status = await _dispatch_one(it)

    [session] = it.sessions()
    assert status == "done_with_concerns"
    assert (session["hook_point"], session["status"]) == ("spec.author.write", status)
    assert "the totals are still Decimal" in Path(session["result_path"]).read_text()


# -- harness availability ----------------------------------------------------------


async def test_an_unloadable_selected_skill_stops_for_a_human(tmp_path, repo):
    """`selected-skill-must-be-available`: no substitute method, no launch."""
    fake_harness_home(tmp_path, [sys.executable, "-c", ""])
    task = _agent("write", prompt="Produce the specification.", skill="no-such-method")

    status, evts, sessions, _row = await _walk(tmp_path, repo, [_exec("spec", task)])

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
async def test_an_unavailable_selected_harness_stops_for_a_human(
    tmp_path, repo, monkeypatch, selected, profiles, why
):
    """`unavailable-selected-harness-needs-human`: never silently another
    harness. Each case makes the selected profile unavailable a different
    way -- absent, disabled, on a provider this install lacks, no
    `harnesses.yaml` at all, or carrying a default Kraft cannot apply -- and
    each stops before anything launches."""
    monkeypatch.setenv("KRAFT_HOME", str(tmp_path / "empty-home"))
    templates = tmp_path / "templates"
    templates.mkdir()
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(templates))
    if profiles is not None:
        (templates / "harnesses.yaml").write_text(json.dumps({"harnesses": profiles}))
    task = _agent("write", harness=selected, prompt="Produce the specification.")

    status, _evts, sessions, _row = await _walk(tmp_path, repo, [_exec("spec", task)])

    assert status == "needs_human"
    assert [s["status"] for s in sessions] == ["config_error"]
    log = Path(sessions[0]["log_path"]).read_text()
    assert f"selects harness {selected!r}, which is not available" in log
    assert why in log, log


# -- the seeded library, dispatched on its own harness profiles --------------------


_FAKE_CLAUDE_SH = _REPO_ROOT / "fixtures" / "fake-claude.sh"

#: The shipped `project-standards` profile's instructions, as `library.yaml` has them.
_PROJECT_STANDARDS = "Keep changes focused. Run the relevant checks before finishing."


def _seeded(tmp_path, monkeypatch):
    """The shipped library seeded into `tmp_path/templates`, with no steering
    file: the product ships none for a V1 profile, so a test that left one
    there would pass on a setup no operator has."""
    templates = seed_v1_library(tmp_path / "templates")
    shutil.rmtree(templates / "steering", ignore_errors=True)
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(templates))
    return templates


def _materialize(templates, chain_id, repo):
    from kraft.policy import InstancePolicy, InstancePolicyInput
    from kraft.templates.environment import Repository, WorkItemTarget
    from kraft.templates.library import TemplateLibrary

    return (
        TemplateLibrary.from_yaml_dir(templates)
        .resolve_chain(chain_id)
        .materialize(
            target=WorkItemTarget.for_repository(Repository(id="target", path=str(repo))),
            effective_policy=InstancePolicy.from_input(InstancePolicyInput.model_validate({})),
        )
    )


async def _dispatch_seeded(
    tmp_path, repo, database, run_dirs, monkeypatch, node_id, *, after_intake=None, snapshot=None
):
    """Materialize the shipped `default` chain and run it from `node_id` with
    only each profile's `executable:` pointed at a fake.

    `after_intake(templates)` runs between intake and dispatch; `snapshot(json)`
    rewrites the stored snapshot. Returns the worker sessions and the fake
    agent's argv log, one argument per line (so a multi-line prompt spans
    several)."""
    templates = _seeded(tmp_path, monkeypatch)
    shipped = (_REPO_ROOT / "templates" / "harnesses.yaml").read_text()
    # The executable only: provider, enabled and defaults stay as shipped.
    (templates / "harnesses.yaml").write_text(
        shipped.replace("executable: codex", f"executable: {_FAKE_CLAUDE_SH}").replace(
            "executable: claude", f"executable: {_FAKE_CLAUDE_SH}"
        )
    )
    assert shipped.count("executable:") == 2
    argv_log = tmp_path / "argv.log"
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE_ARGV_LOG", str(argv_log))
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "noop")

    chain = _materialize(templates, "default", repo)
    start = [n.id for n in chain.chain.nodes].index(node_id)
    if snapshot is not None:
        stored = snapshot(chain.to_json())
        chain = SimpleNamespace(chain=chain.chain, to_json=lambda: stored)
    if after_intake is not None:
        after_intake(templates)
    await v1_item(database, chain, repo=repo)
    await executor.run_once(
        database,
        run_dirs,
        work_item_id="w1",
        registry=None,
        start_index=start,
        launch=LaunchContext(repo_entry={"setup_command": ""}, steering_dir=templates / "steering"),
    )
    sessions = database.read(
        lambda c: c.execute("SELECT * FROM worker_sessions ORDER BY created_at").fetchall()
    )
    return [dict(r) for r in sessions], argv_log.read_text() if argv_log.exists() else ""


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
async def test_the_seeded_library_dispatches_through_its_real_harness_profiles(
    tmp_path, repo, database, run_dirs, monkeypatch, node_id, argv_marks
):
    """The library an operator is seeded with names *profile* ids
    (`codex_default`, `claude_review`), which `templates/harnesses.yaml`
    defines. Dispatched unrewritten -- the task's `harness:` untouched, only the
    profile's `executable:` pointed at a fake -- each must launch its
    provider's argv with the profile's defaults, not stop at "not available".
    `seed_v1_library(agent_command=...)` rewrites every id to `fake`, which is
    why nothing caught that it never could."""
    sessions, argv = await _dispatch_seeded(
        tmp_path, repo, database, run_dirs, monkeypatch, node_id
    )

    assert sessions[0]["status"] == "done", Path(sessions[0]["log_path"]).read_text()
    for mark in argv_marks:
        assert mark in argv.split("\n"), argv


async def test_the_seeded_library_steers_from_its_own_profiles_with_no_steering_file(
    tmp_path, repo, database, run_dirs, monkeypatch
):
    """`spec.main.author` selects `steering: [project-standards]`, a profile
    `library.yaml` declares inline. With no `templates/steering/*.md` on disk
    -- which is what a fresh install has -- the profile's instructions still
    reach the agent, after the contract
    (`agent-task-contract-precedes-skill-and-steering`)."""
    sessions, prompt = await _dispatch_seeded(
        tmp_path, repo, database, run_dirs, monkeypatch, "spec"
    )

    first = sessions[0]
    assert (first["hook_point"], first["status"]) == ("spec.main.author", "done"), Path(
        first["log_path"]
    ).read_text()
    assert _PROJECT_STANDARDS in prompt
    assert prompt.index("Write your spec to") < prompt.index("## Project standards")


async def test_editing_the_library_after_intake_does_not_change_a_running_items_steering(
    tmp_path, repo, database, run_dirs, monkeypatch
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

    sessions, prompt = await _dispatch_seeded(
        tmp_path, repo, database, run_dirs, monkeypatch, "spec", after_intake=edit
    )

    assert sessions[0]["status"] == "done", Path(sessions[0]["log_path"]).read_text()
    assert _PROJECT_STANDARDS in prompt
    assert "EDITED AFTER INTAKE" not in prompt
    assert "FILE AFTER INTAKE" not in prompt


async def test_a_snapshot_without_frozen_steering_stops_for_a_human(
    tmp_path, repo, database, run_dirs, monkeypatch
):
    """An item materialized before steering was frozen into its snapshot has
    names and no text. It must not run unsteered, and must not quietly take
    today's library text as if it were the intake's: it stops, saying why and
    what to do."""

    def unfrozen(raw):
        stored = json.loads(raw)
        del stored["steering"]
        return json.dumps(stored)

    sessions, argv = await _dispatch_seeded(
        tmp_path, repo, database, run_dirs, monkeypatch, "spec", snapshot=unfrozen
    )

    assert [s["status"] for s in sessions] == ["config_error"]
    assert argv == ""
    log = Path(sessions[0]["log_path"]).read_text()
    assert "project-standards" in log and "before steering was frozen" in log, log


# -- every agent launch carries Kraft's safety rule ---------------------------------


def _capture_launches(monkeypatch) -> dict[str, str]:
    """Stand in for the process spawn only: every agent launch still goes
    through `run_agent_task`, `build_context` and `harness.build_argv`, and the
    argv it would have run is recorded by hook point, one argument per line."""
    launched: dict[str, str] = {}

    async def _spawn(_db, _rd, *, hook_point, cmd, **_kw):
        launched[hook_point] = "\n".join(cmd)
        return "done"

    monkeypatch.setattr(agent_mod._subprocess, "run_task", _spawn)
    return launched


async def test_every_seeded_agent_task_launches_with_the_never_signal_rule(
    tmp_path, repo, database, run_dirs, monkeypatch
):
    """`every-agent-launch-carries-kraft-safety-rules` (Kraft-5x93b): the legacy
    registry gave every agent the never-signal steering by default; V1 has no
    such default, so the rule is Kraft's own contract text instead. Every agent
    task of every chain the shipped seed selects is materialized the way intake
    does and dispatched on the shipped harness profiles, so a new seeded agent
    task is covered the moment it exists."""
    from kraft.templates.library import TemplateLibrary
    from kraft.templates.models import AgentTask

    templates = _seeded(tmp_path, monkeypatch)
    launched = _capture_launches(monkeypatch)
    launch = LaunchContext(repo_entry={"setup_command": ""}, steering_dir=templates / "steering")
    argv: dict[str, str] = {}
    for n, chain_id in enumerate(TemplateLibrary.from_yaml_dir(templates).chain_ids):
        chain = _materialize(templates, chain_id, repo)
        wid = f"w{n}"
        await v1_item(database, chain, repo=repo, wid=wid)
        row = database.read(
            lambda c, wid=wid: c.execute("SELECT * FROM work_items WHERE id = ?", (wid,)).fetchone()
        )
        for node in chain.chain.nodes:
            for task in node.tasks():
                if not isinstance(task.task, AgentTask):
                    continue
                launched.clear()
                await dispatch.dispatch_node(
                    database, run_dirs, task, node, row, repo, launch=launch
                )
                argv[f"{chain_id}:{task.path}"] = launched.get(task.path, "")

    # Not vacuous: the seed was read, and its known agent tasks were launched.
    assert {
        "default:implementation.main.implement",
        "default:spec.main.author",
        "quick-task:implementation.main.implement",
    } <= set(argv), argv
    # The rule itself, not only its env-var hint: dropping the headline
    # sentence must go red too (Kraft-5x93b review, finding 1).
    for phrase in (
        "Never signal a process you did not start",
        "KRAFT_DAEMON_PID",
        "a question for a human",
    ):
        assert phrase in agent_mod.SAFETY_RULES, phrase
    missing = sorted(p for p, a in argv.items() if agent_mod.SAFETY_RULES not in a)
    assert missing == [], missing


async def test_an_operator_agent_task_with_no_skill_or_steering_gets_the_never_signal_rule(
    item_on, tmp_path, monkeypatch
):
    """The rule is not something a task opts into, so a task an operator
    writes without any steering or skill carries it all the same."""
    fake_harness_home(tmp_path, ["true"])
    launched = _capture_launches(monkeypatch)
    raw = {"id": "write", "kind": "agent", "harness": "fake", "prompt": "Do the thing."}
    it = await item_on(_one_task(raw, "work", "do"))

    assert await _dispatch_one(it) == "done"
    assert agent_mod.SAFETY_RULES in launched["work.do.write"]


# -- `templates.with_inputs`: parked, no `src/` caller (templates/__init__.py) ------


@pytest.mark.parametrize(
    ("binding", "hook", "expected"),
    [
        # Kraft-ouoqx: repo test scopes must not replace a reviewer's command.
        ({"kind": "subprocess", "command": ["my-reviewer"]}, "on.review.local.run", {}),
        (
            {"kind": "subprocess", "command": ["uv", "run", "pytest", "-q"]},
            "TEST_HOOK",
            {"test_scopes": {"channel": "argv"}},
        ),
        # An explicit inputs table is authoritative.
        ({"kind": "subprocess", "command": ["x"], "inputs": {}}, "TEST_HOOK", {}),
        ({"kind": "agent", "harness": "claude"}, "on.review.local.run", {}),
    ],
    ids=[
        "a-non-test-hook-runs-its-own-command",
        "the-test-hook-takes-repo-scopes",
        "an-explicit-inputs-table-wins",
        "an-agent-hook-takes-none",
    ],
)
def test_with_inputs_resolves_a_bindings_inputs(binding, hook, expected):
    from kraft import templates

    hook = templates.TEST_HOOK if hook == "TEST_HOOK" else hook
    assert templates.with_inputs(binding, hook) == expected
