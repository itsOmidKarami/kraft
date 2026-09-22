"""`kraft.adapters.agent`: the command line and system prompt an agent task
launches with, `resolve_invocation`'s precedence, and real launches of the
Python fake agent (`tests/support/fake_agent.py`) through `run_agent_task`."""

import json
import os
import shlex
import sys
from pathlib import Path

import pytest
from support.harness import write_harness_profiles

from kraft import skill, store
from kraft.adapters import agent
from kraft.adapters.subprocess import read_concerns, read_question
from kraft.worker import steering

_FAKE = Path(__file__).resolve().parents[1] / "support" / "fake_agent.py"
_REPO_ROOT = Path(__file__).resolve().parents[2]
PY_FAKE = f"{sys.executable} {_FAKE}"


# --- real launches of the fake agent ------------------------------------------------


@pytest.fixture
async def launch(database, run_dirs, repo):
    """`await launch(sid, **run_agent_task_kwargs)` -> `(status, session row)`:
    one real launch of the Python fake agent in `repo`, for work item `w1`."""
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

    async def go(sid="s1", **kw):
        kw = {
            "node_id": "implementation",
            "hook_point": "on.implementation.start",
            "command": PY_FAKE,
            "title": "make the failing test pass",
            "task_instruction": "make the failing test pass",
            **kw,
        }
        status = await agent.run_agent_task(
            database,
            run_dirs,
            session_id=sid,
            work_item_id="w1",
            repo_path=str(repo),
            cwd=repo,
            **kw,
        )
        row = database.read(
            lambda c: c.execute("SELECT * FROM worker_sessions WHERE id = ?", (sid,)).fetchone()
        )
        return status, dict(row)

    return go


async def test_agent_fix_mode_patches_repo_and_never_writes_claudemd(launch, repo, monkeypatch):
    (repo / "CLAUDE.md").write_text("DO NOT EDIT\n")
    monkeypatch.delenv("KRAFT_FAKE_AGENT", raising=False)

    status, _ = await launch()

    assert status == "done"
    assert "a + b" in (repo / "calc.py").read_text()
    assert (repo / "CLAUDE.md").read_text() == "DO NOT EDIT\n"


async def test_agent_error_envelope_downgrades_to_failed(launch, monkeypatch):
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "error")

    status, row = await launch()

    assert (status, row["status"]) == ("failed", "failed")


async def test_agent_writes_session_summary_and_ref_lands_in_db(launch, repo, monkeypatch):
    """04 §6: the injected prompt carries the linkage fields, the worker writes
    .engineering/sessions/<session>.md, and its ref lands on worker_sessions."""
    monkeypatch.delenv("KRAFT_FAKE_AGENT", raising=False)

    status, row = await launch("s3")

    assert status == "done"
    assert row["session_summary_ref"] == ".engineering/sessions/s3.md"
    summary = (repo / ".engineering" / "sessions" / "s3.md").read_text()
    for line in (
        "work_item_ids: [w1]",
        "node_id: implementation",
        "hook_point: on.implementation.start",
        "worker_session_id: s3",
    ):
        assert line in summary


@pytest.mark.parametrize(
    "status, field, reader",
    [
        ("done_with_concerns", "CONCERNS", read_concerns),
        ("needs_context", "QUESTION", read_question),
    ],
    ids=["done_with_concerns", "needs_context"],
)
async def test_agent_status_knob_reaches_the_session_row(
    launch, monkeypatch, status, field, reader
):
    """The fake's KRAFT_FAKE_AGENT_STATUS knob round-trips through the real
    result-file resolution path (`kraft.adapters.subprocess._resolve_result_file`)
    onto worker_sessions.status -- not asserted against the raw JSON the fake
    wrote, which would pass even if nothing downstream read it."""
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")
    monkeypatch.setenv("KRAFT_FAKE_AGENT_STATUS", status)
    monkeypatch.setenv(f"KRAFT_FAKE_AGENT_{field}", "the words")

    returned, row = await launch()

    assert (returned, row["status"]) == (status, status)
    assert reader(Path(row["result_path"])) == "the words"


async def test_agent_plan_scripts_status_per_invocation(launch, tmp_path, monkeypatch):
    """KRAFT_FAKE_AGENT_PLAN drives two launches to two different statuses from
    one process's worth of env -- the mechanism Sub-project F's fake reviewer
    established, reused rather than reinvented here."""
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

    assert [(await launch(sid))[0] for sid in ("s6", "s7")] == [
        "done_with_concerns",
        "needs_context",
    ]


@pytest.mark.parametrize(
    "prewritten, fake_env, expected",
    [
        # Kraft-7lu, work item 6363c65e: the spec worker was refused every
        # Write, wrote nothing, and still reported success -- the executor
        # opened `spec_approval` over an empty gate, $2.15 to approve nothing.
        (False, {}, "failed"),
        # The guard must not fail a worker that did its job.
        (True, {}, "done"),
        # A worker that stopped to ask wrote no artifact *because* it stopped:
        # rewriting that to `failed` loses the question, and
        # `dispatch.needs_context_question` matches on the row's status, so
        # /steer and /resume would 409.
        (
            False,
            {
                "KRAFT_FAKE_AGENT": "noop",
                "KRAFT_FAKE_AGENT_STATUS": "needs_context",
                "KRAFT_FAKE_AGENT_QUESTION": "which editor policy?",
            },
            "needs_context",
        ),
    ],
    ids=["missing-fails-the-node", "written-stays-green", "needs-context-survives-the-guard"],
)
async def test_a_declared_artifact_guards_the_node(
    launch, repo, monkeypatch, prewritten, fake_env, expected
):
    monkeypatch.setenv("KRAFT_FAKE_AGENT_SKIP_ARTIFACT", "1")
    for k, v in fake_env.items():
        monkeypatch.setenv(k, v)
    written = repo / agent.artifact_path("spec", "w1")
    if prewritten:
        written.parent.mkdir(parents=True)
        written.write_text("# spec\n")

    status, row = await launch(node_id="spec", hook_point="on.spec.requested", artifact="spec")

    assert (status, row["status"]) == (expected, expected)
    assert written.exists() is prewritten
    assert read_question(Path(row["result_path"])) == fake_env.get("KRAFT_FAKE_AGENT_QUESTION")


def _argv_log(monkeypatch, tmp_path) -> Path:
    """Where the fake agent writes each launch's argv, one JSON list per line."""
    log = tmp_path / "argv.jsonl"
    monkeypatch.delenv("KRAFT_FAKE_AGENT", raising=False)
    monkeypatch.setenv("KRAFT_FAKE_AGENT_ARGV_LOG", str(log))
    return log


def _last_argv(log: Path) -> list[str]:
    return json.loads(log.read_text().splitlines()[-1])


async def test_a_real_launch_never_leaks_repo_file_content_into_the_prompt(
    launch, repo, tmp_path, monkeypatch
):
    """Spec §6's boundary regression, against a real (fake) agent process -- not
    `"CLAUDE.md" not in prompt`, which holds for any implementation, including
    one that read the repo's CLAUDE.md and injected it under another name. Put
    recognisable content in CLAUDE.md and AGENTS.md, launch, and check neither
    file's *content* reached the prompt and neither file was touched."""
    markers = {
        name: f"REPO-SECRET-{name}-CONTENT-MUST-NOT-BE-INJECTED"
        for name in ("CLAUDE.md", "AGENTS.md")
    }
    for name, marker in markers.items():
        (repo / name).write_text(marker + "\n")
    log = _argv_log(monkeypatch, tmp_path)

    assert (await launch("s9"))[0] == "done"

    argv = _last_argv(log)
    prompt = argv[argv.index("--append-system-prompt") + 1]
    for name, marker in markers.items():
        assert marker not in prompt
        assert (repo / name).read_text() == marker + "\n"


async def test_run_agent_task_builds_a_codex_command_line(launch, tmp_path, monkeypatch):
    """A node bound to codex launches codex, with codex's own spellings."""
    log = _argv_log(monkeypatch, tmp_path)

    await launch(harness="codex")

    argv = _last_argv(log)
    assert argv[0] == "exec", "`exec` survives the multiword command override (Task 2)"
    assert any(a.startswith("developer_instructions=") for a in argv)
    assert "--append-system-prompt" not in argv
    # Spec leak 1: Monitor was unconditional, so it would have reached codex.
    assert "Monitor" not in argv
    assert "--permission-prompt-tool" not in argv


async def test_run_agent_task_still_builds_todays_claude_command_line(
    launch, tmp_path, monkeypatch
):
    log = _argv_log(monkeypatch, tmp_path)

    await launch(harness="claude")

    argv = _last_argv(log)
    assert argv[0] == "-p"
    assert "--append-system-prompt" in argv
    assert argv[argv.index("--disallowed-tools") + 1] == "Monitor"
    assert argv[argv.index("--permission-prompt-tool") + 1] == "mcp__kraft__permission_request"


# --- the command line, captured before launch ------------------------------------------


def _system_prompt(cmd):
    return cmd[cmd.index("--append-system-prompt") + 1]


def test_default_profile_reproduces_todays_command_line(run):
    """The whole argv for a binding that sets none of the optional keys, pinned
    in full rather than by flag, so a change to what every worker is launched
    with cannot land without being read. Order is claude.yaml's own
    `capabilities:` declaration order (harness.build_argv). What that pins:

    - `stream-json` + `--verbose`, once each: `--output-format json` prints one
      object at exit, so no log to tail, no tokens to count, no model until the
      node is over (Kraft-77z, 54dk, 2r8s); without `--verbose` the CLI exits 1.
    - no `--model`/`--effort`: an unset one leaves the CLI's own default.
    - `Monitor` always denied (Kraft-avpe): a one-shot `claude -p` session has
      no later turn to resume into.
    - `--permission-mode auto`, always: without it `claude -p` has nobody to
      answer a prompt and denies instead (6363c65e: 13 denials, Kraft-8pe/7lu);
      no `--allowedTools` for a silent binding.
    - the permission prompt tool on every task: the decision and its event are
      the point (Kraft-oor).
    - no `--resume`/`--autocompact`: only an escalation turn resumes.
    - no steering, artifact or method: the system prompt is byte-identical to
      the bare context plus the safety rules.
    """
    ctx = (
        agent._CTX.format(
            title="t",
            task_instruction="do the thing",
            repo_path="/repo",
            work_item_id="w1",
            node_id="implementation",
            hook_point="on.implementation.start",
            session_id="s1",
        )
        + agent.SAFETY_RULES
    )

    assert run()["cmd"] == [
        *shlex.split("claude"),
        "-p",
        "do the thing",
        "--append-system-prompt",
        ctx,
        "--output-format",
        "stream-json",
        "--verbose",
        "--disallowed-tools",
        "Monitor",
        "--permission-mode",
        "auto",
        "--permission-prompt-tool",
        "mcp__kraft__permission_request",
    ]


@pytest.mark.parametrize(
    "overrides, flag, value",
    [
        ({"model": "opus"}, "--model", "opus"),
        ({"effort": "high"}, "--effort", "high"),
        # `always: [Monitor]` is unioned first, per harness.build_argv ...
        ({"deny_tools": ("WebFetch", "Bash")}, "--disallowed-tools", "Monitor,WebFetch,Bash"),
        # ... and not duplicated when a caller already denies it.
        ({"deny_tools": ("Monitor", "Bash")}, "--disallowed-tools", "Monitor,Bash"),
        # A node that needs no shell can say so (Kraft-3tw).
        ({"allowed_tools": ("Read", "Grep")}, "--allowedTools", "Read,Grep"),
        ({"permission_mode": "acceptEdits"}, "--permission-mode", "acceptEdits"),
        ({"resume_session_id": "cli-session-abc"}, "--resume", "cli-session-abc"),
        (
            {"resume_session_id": "cli-session-abc", "autocompact": "auto"},
            "--autocompact",
            "auto",
        ),
        # A capability the harness declares still launches, in its spelling.
        (
            {"harness": "codex", "command": "codex", "model": "gpt-5", "effort": "high"},
            "-m",
            "gpt-5",
        ),
    ],
    ids=[
        "model",
        "effort",
        "deny-tools-after-monitor",
        "monitor-not-duplicated",
        "allowed-tools",
        "permission-mode",
        "resume",
        "autocompact-with-resume",
        "codex-model",
    ],
)
def test_an_option_becomes_its_flag(run, overrides, flag, value):
    cmd = run(**overrides)["cmd"]
    assert cmd.count(flag) == 1
    assert cmd[cmd.index(flag) + 1] == value


@pytest.mark.parametrize(
    "allowed, tools",
    [
        (("Read", "Grep"), "Read,Grep"),
        # Allow nothing: no built-in tool exists, and every MCP ask is denied.
        ((), ""),
        # `--tools` takes bare built-in names and does not govern MCP tools,
        # which the permission gate answers instead.
        (("Bash(git *)", "mcp__kraft__report_progress"), "Bash"),
    ],
    ids=["listed", "empty", "scoped-rule-and-mcp-tool"],
)
def test_under_an_allowlist_claude_has_only_those_tools_and_asks_for_the_rest(run, allowed, tools):
    """Kraft-nt6tt: `--allowedTools` only pre-approves, and in `auto` mode the
    CLI's classifier approves an unlisted tool itself, so the ask never reaches
    the permission gate. Under an allowlist the built-ins are cut to the list
    (`--tools`) and the mode is `manual`, which sends every other ask to
    `--permission-prompt-tool` -- the gate, which denies what is not listed."""
    cmd = run(allowed_tools=allowed)["cmd"]
    assert cmd[cmd.index("--tools") + 1] == tools
    assert cmd.count("--permission-mode") == 1
    assert cmd[cmd.index("--permission-mode") + 1] == "manual"
    assert cmd[cmd.index("--permission-prompt-tool") + 1] == "mcp__kraft__permission_request"


@pytest.mark.parametrize(
    "overrides, match",
    [
        ({"harness": "nope"}, "nope"),
        # `load_registry` only sees a binding's own keys; `resolve_invocation`
        # folds in a repo's `deny_tools` and an item's overrides afterwards, so
        # these reach a launch without the load-time check and must not be
        # silently dropped.
        ({"harness": "codex", "command": "codex", "deny_tools": ("Monitor",)}, "deny_tools"),
        ({"harness": "gemini", "command": "gemini", "effort": "high"}, "effort"),
        # Kraft-nt6tt: a harness that cannot restrict its tools launches under
        # no allowlist at all -- the empty one included, which spells no flag.
        ({"harness": "codex", "command": "codex", "allowed_tools": ()}, "allowed_tools"),
        # A mode that approves asks itself would bypass the gate.
        ({"allowed_tools": ("Read",), "permission_mode": "auto"}, "permission_mode"),
    ],
    ids=[
        "unknown-harness",
        "repo-deny-list-on-codex",
        "effort-on-gemini",
        "empty-allowlist-on-codex",
        "allowlist-under-a-self-approving-mode",
    ],
)
def test_a_launch_the_harness_cannot_express_is_refused(run, overrides, match):
    with pytest.raises(ValueError, match=match):
        run(**overrides)


def test_autocompact_is_exempt_because_it_rides_along_with_resume(run):
    """`escalate.dispatch` pairs `autocompact` with resume unconditionally, and
    a harness that declares neither is a capability fact rather than a failure
    -- so this one is dropped, not raised over."""
    assert "--autocompact" not in run(harness="gemini", command="gemini", autocompact="auto")["cmd"]


@pytest.mark.parametrize(
    "overrides, key, expected",
    [
        (
            {"sandbox": {"kind": "docker", "image": "kraft-worker"}},
            "sandbox",
            {"kind": "docker", "image": "kraft-worker"},
        ),
        ({}, "sandbox", None),
    ],
    ids=["sandbox-forwarded", "sandbox-defaults-to-none"],
)
def test_run_agent_task_forwards_to_run_task(run, overrides, key, expected):
    assert run(**overrides)[key] == expected


@pytest.mark.parametrize(
    "identify, work_item_id",
    [(True, "w1"), (False, None)],
    ids=["a-worker-by-default", "an-escalation-turn-is-not-a-worker"],
)
def test_identify_as_worker_sets_the_work_item_env_var(run, identify, work_item_id):
    env = run(identify_as_worker=identify)["env"]
    assert env.get("KRAFT_WORK_ITEM_ID") == work_item_id
    assert env["KRAFT_SESSION_ID"] == "s1"


# --- the system prompt ---------------------------------------------------------------


@pytest.mark.parametrize(
    "phrases",
    [
        # Sub-project G §1: the injected context is the only channel that tells
        # an agent the status vocabulary exists.
        ("done", "done_with_concerns", "failed", "needs_context", "concerns", "question"),
        # Spec §5: needs_context costs a full stop and relaunch, so ask for
        # every missing fact at once.
        ("ask for all of them in that one question",),
        # Kraft-avpe: a headless session has no next turn to receive a
        # background job's notification.
        ("there is no notification", "background"),
        # Kraft-brq: the node that writes the code was never told to commit it.
        ("Commit everything you change before you exit", "destroyed with the worktree"),
        # Kraft is the only pusher (spec §1, §4).
        ("Do not push and do not merge",),
    ],
    ids=["statuses-and-fields", "one-question-per-stop", "no-backgrounding", "commit", "no-push"],
)
def test_the_injected_context_says(run, phrases):
    prompt = _system_prompt(run()["cmd"])
    for phrase in phrases:
        assert phrase in prompt


@pytest.mark.parametrize(
    "overrides, in_order",
    [
        # The exact path, not a directory to guess inside.
        ({"artifact": "spec"}, (".engineering/specs/w1.md",)),
        # `on.mr.describe`'s artifact carries fields read straight into
        # `glab`/`gh` create arguments (spec §2): ask for them by name.
        (
            {"artifact": "mr_meta"},
            (".engineering/mr_metas/", "title:", "labels:", "assignees:", "reviewers:"),
        ),
        # repo body before hook body -- resolve_invocation orders the tuple;
        # run_agent_task must not reorder or drop bodies.
        ({"steering_texts": ("repo body", "hook body")}, ("repo body", "hook body")),
        # Spec §2.6: what to produce, then how to produce it.
        (
            {"artifact": "spec", "method_text": "Write it in one page."},
            (".engineering/specs/w1.md", skill.HEADING + "Write it in one page."),
        ),
    ],
    ids=["artifact-path", "mr-meta-fields", "steering-order", "artifact-before-method"],
)
def test_the_system_prompt_carries_in_order(run, overrides, in_order):
    prompt = _system_prompt(run(**overrides)["cmd"])
    for phrase in in_order:
        assert phrase in prompt, phrase
    firsts = [prompt.index(phrase) for phrase in in_order]
    assert firsts == sorted(firsts)


@pytest.mark.parametrize(
    "overrides, tail",
    [
        ({"steering_texts": ("Use tabs.",)}, steering.HEADING + "Use tabs."),
        ({"method_text": "Write it in one page."}, skill.UNAVAILABLE),
    ],
    ids=["steering-under-its-heading", "method-then-the-unavailable-note"],
)
def test_the_system_prompt_ends_with(run, overrides, tail):
    assert _system_prompt(run(**overrides)["cmd"]).endswith(tail)


def test_the_artifact_contract_does_not_claim_the_gate_cannot_see_uncommitted_files(run):
    """Kraft-i47n. `_ARTIFACT` justified its commit sentence with "an
    uncommitted file is invisible to them", which is false: the gate reads the
    file off the worktree's disk, deliberately. With the contract in `_CTX` the
    artifact needs no commit rule of its own, and must not state one twice."""
    prompt = _system_prompt(run(artifact="spec")["cmd"])
    assert "invisible to them" not in prompt
    assert prompt.count("Commit everything you change before you exit") == 1


def _ctx_kwargs():
    return dict(
        title="t",
        task_instruction="do it",
        repo_path="/r",
        work_item_id="w1",
        node_id="implementation",
        hook_point="on.implementation.start",
        session_id="s1",
    )


def test_ctx_asks_for_usage_only_when_the_log_cannot_be_read():
    """`source: result_file` is half an answer unless the agent is asked."""
    envelope = agent.build_context(
        usage_source="envelope", context_channel="system_prompt", **_ctx_kwargs()
    )
    result_file = agent.build_context(
        usage_source="result_file", context_channel="system_prompt", **_ctx_kwargs()
    )
    assert "input_tokens" not in envelope
    assert "input_tokens" in result_file and "cost_usd" in result_file


def test_prompt_channel_still_delivers_the_whole_contract():
    """Gemini has no out-of-band channel, so the contract rides in the prompt.
    Nothing in it may be dropped on the way."""
    ctx = agent.build_context(usage_source="result_file", context_channel="prompt", **_ctx_kwargs())
    for required in (
        "$KRAFT_RESULT_PATH",
        "needs_context",
        "git commit",
        ".engineering/",
        "Monitor tool is",
    ):
        assert required in ctx


def test_artifact_path_is_the_kind_pluralised():
    assert agent.artifact_path("spec", "w1") == ".engineering/specs/w1.md"
    assert agent.artifact_path("plan", "w1") == ".engineering/plans/w1.md"


def test_repo_agent_docs_do_not_forbid_a_kraft_worker_from_committing():
    for name in ("CLAUDE.md", "AGENTS.md"):
        text = (_REPO_ROOT / name).read_text()
        assert "## Kraft Workers" in text, f"{name} has no Kraft-worker carve-out"
        carve_out = text[text.index("## Kraft Workers") :]
        assert "commit" in carve_out, name
        # A carve-out nested inside the BEADS block would go when that block is
        # stripped.
        if "<!-- BEGIN BEADS" in text:
            begin = text.index("<!-- BEGIN BEADS")
            end = text.index("<!-- END BEADS") if "<!-- END BEADS" in text else len(text)
            assert not (begin < text.index("## Kraft Workers") < end), (
                f"{name}: Kraft Workers carve-out is nested inside the BEADS block"
            )


# --- resolve_invocation --------------------------------------------------------------

C = {"command": "c"}
DOCKER = {"kind": "docker", "image": "kraft-worker"}


@pytest.mark.parametrize(
    "binding, repo, kw, field, expected",
    [
        ({**C, "model": "opus"}, {"models": {"p": "haiku"}}, {"profile": "p"}, "model", "opus"),
        (C, {"models": {"p": "haiku"}}, {"profile": "p"}, "model", "haiku"),
        (C, {"models": {"p": "haiku"}}, {"profile": "codex_default"}, "model", None),
        (C, {}, {}, "model", None),
        # Precedence lives here and only here (spec §6): a fix cycle past
        # `escalate_after` asks for the bump, it does not name a model.
        ({**C, "model": "sonnet", "escalate_model": "opus"}, {}, {}, "model", "sonnet"),
        (
            {**C, "model": "sonnet", "escalate_model": "opus"},
            {},
            {"escalate": True},
            "model",
            "opus",
        ),
        ({**C, "model": "sonnet"}, {}, {"escalate": True}, "model", "sonnet"),
        (C, {"models": {"p": "haiku"}}, {"profile": "p", "escalate": True}, "model", "haiku"),
        ({**C, "model": "sonnet"}, {}, {"item_override": {"model": "opus"}}, "model", "opus"),
        (
            {**C, "escalate_model": "opus"},
            {},
            {"escalate": True, "item_override": {"escalate_model": "opus-max"}},
            "model",
            "opus-max",
        ),
        # The bead's own worked case: a cheap item override must not suppress
        # the escalation valve -- a plain `model` is not `escalate_model`.
        (
            {**C, "model": "sonnet", "escalate_model": "opus"},
            {},
            {"escalate": True, "item_override": {"model": "haiku"}},
            "model",
            "opus",
        ),
        ({**C, "effort": "high"}, {}, {}, "effort", "high"),
        (C, {}, {}, "effort", None),
        ({**C, "effort": "low"}, {}, {"item_override": {"effort": "max"}}, "effort", "max"),
        ({**C, "model": "sonnet"}, {}, {"item_override": None}, "model", "sonnet"),
        ({**C, "effort": "low"}, {}, {"item_override": None}, "effort", "low"),
        ({**C, "allowed_tools": ["Read", "Grep"]}, {}, {}, "allowed_tools", ("Read", "Grep")),
        (C, {}, {}, "allowed_tools", None),  # unbounded; `()` would allow nothing
        ({**C, "permission_mode": "plan"}, {}, {}, "permission_mode", "plan"),
        (C, {}, {}, "permission_mode", None),
        # Unioning two allowlists widens the narrower one, the opposite of
        # what an allowlist is for -- unlike deny_tools, which only narrows.
        (
            {**C, "allowed_tools": ["Read"]},
            {"allowed_tools": ["Bash"]},
            {},
            "allowed_tools",
            ("Read",),
        ),
        (
            {**C, "deny_tools": ["WebFetch", "Bash"]},
            {"deny_tools": ["Bash"]},
            {},
            "deny_tools",
            ("Bash", "WebFetch"),
        ),
        # Spec §6: the harness says how a CLI is spoken to, the command which
        # binary -- one hook can change either without the other.
        ({"command": "claude-next"}, None, {}, "command", "claude-next"),
        ({"command": "claude-next"}, None, {}, "harness", "claude"),
        ({**C, "sandbox": DOCKER}, {}, {}, "sandbox", DOCKER),
        ({**C, "sandbox": DOCKER}, {"sandbox": False}, {}, "sandbox", None),
        (C, {}, {}, "sandbox", None),
    ],
    ids=[
        "hook-model-beats-repo-default",
        "repo-default-when-the-hook-is-silent",
        "repo-model-for-another-profile-does-not-apply",
        "no-model-anywhere",
        "escalate-model-only-when-escalating",
        "escalate-picks-the-escalate-model",
        "escalate-falls-back-to-the-model",
        "escalate-falls-back-to-the-repo-default",
        "item-model-beats-binding",
        "item-escalate-model-beats-binding-escalate-model",
        "binding-escalate-model-beats-item-plain-model",
        "effort-off-the-binding",
        "no-effort-anywhere",
        "item-effort-beats-binding",
        "no-item-override-keeps-the-model",
        "no-item-override-keeps-the-effort",
        "allowed-tools-off-the-binding",
        "no-allowed-tools",
        "permission-mode-off-the-binding",
        "no-permission-mode",
        "allowed-tools-never-from-the-repo",
        "deny-tools-union-repo-first-deduplicated",
        "command-varies-alone",
        "harness-stays-claude",
        "sandbox-off-the-binding",
        "repo-sandbox-false-overrides-the-binding",
        "no-sandbox",
    ],
)
def test_resolve_invocation_picks(binding, repo, kw, field, expected):
    assert getattr(agent.resolve_invocation(binding, repo, None, **kw), field) == expected


def test_steering_is_repo_first_then_hook(tmp_path):
    (tmp_path / "repo-note.md").write_text("repo")
    (tmp_path / "hook-note.md").write_text("hook")
    inv = agent.resolve_invocation(
        {**C, "steering": ["hook-note"]}, {"steering": ["repo-note"]}, tmp_path
    )
    # A tuple: ("repo", "hook") == ["repo", "hook"] is False.
    assert inv.steering_texts == ("repo", "hook")


def test_a_repo_entry_of_none_behaves_like_an_empty_one(tmp_path):
    assert agent.resolve_invocation(C, None, tmp_path) == agent.resolve_invocation(C, {}, tmp_path)


def test_combined_repo_and_hook_steering_over_budget_raises(tmp_path):
    """`steering.validate` runs over repos.yaml's names and a hook's names
    separately at config-load time -- each valid here. Nothing at load time
    measures the concatenation `resolve_invocation` builds, which can still
    blow the shared 8 KB budget."""
    (tmp_path / "repo-note.md").write_text("x" * (steering.MAX_BYTES // 2))
    (tmp_path / "hook-note.md").write_text("y" * (steering.MAX_BYTES // 2))
    steering.validate(tmp_path, ["repo-note"], where="repos.yaml")
    steering.validate(tmp_path, ["hook-note"], where="registry.yaml")

    with pytest.raises(steering.SteeringError, match="8192"):
        agent.resolve_invocation(
            {**C, "steering": ["hook-note"]}, {"steering": ["repo-note"]}, tmp_path
        )


def test_resolve_invocation_reads_the_skill(tmp_path):
    inv = agent.resolve_invocation({**C, "skill": "chain-review"}, None, None, skills_dir=tmp_path)
    assert inv.method_text.startswith("---")


def test_resolve_invocation_without_a_skill_carries_no_method(tmp_path):
    assert agent.resolve_invocation(C, None, None, skills_dir=tmp_path).method_text is None


# --- Template Schema V1: the provider owns the runtime mechanics --------------------


def _v1_agent_task(**fields):
    from kraft.templates.models import AgentTask

    return AgentTask.model_validate({"kind": "agent", **fields})


def _claude_profile():
    """A profile `claude` on the provider of that name, for a task selecting it."""
    write_harness_profiles(
        Path(os.environ["KRAFT_HOME"]) / "templates", {"claude": {"provider": "claude"}}
    )


def _bare_provider(provider_id: str):
    """A provider declaring only prompt/context/usage: no model, effort or resume."""
    from kraft import harness as _harness

    return _harness.parse(
        {
            "id": provider_id,
            "kind": "cli",
            "command": [provider_id],
            "capabilities": {
                "prompt": {"cli": ["-p", "{value}"]},
                "context": {"channel": "prompt"},
                "usage": {"source": "result_file"},
            },
        },
        where="test",
    )


def test_a_task_overrides_its_harness_profiles_defaults():
    """`agent-task-selects-capability-compatible-runtime-options`: a profile's
    `defaults:` are the lowest rung. The task's own field beats them, the repo's
    model for that profile beats them, the item's override beats all three -- and
    what nothing overrides still arrives from the profile's provider and executable."""
    profile = {"provider": "claude", "executable": "/opt/claude-wrapper"}
    defaults = {"model": "sonnet", "effort": "medium", "permission_mode": "plan"}
    write_harness_profiles(
        Path(os.environ["KRAFT_HOME"]) / "templates", {"review": {**profile, "defaults": defaults}}
    )
    task = _v1_agent_task(id="t", harness="review", prompt="p", effort="high")

    inv = agent.resolve_agent_task(task, None, None)
    assert (inv.harness, inv.command) == ("claude", "/opt/claude-wrapper")
    assert (inv.model, inv.effort, inv.permission_mode) == ("sonnet", "high", "plan")

    repo = {"models": {"review": "haiku", "codex_default": "gpt-5"}}
    assert agent.resolve_agent_task(task, repo, None).model == "haiku"
    item = {"model": "opus", "effort": "low"}
    overridden = agent.resolve_agent_task(task, repo, None, item_override=item)
    assert (overridden.model, overridden.effort) == ("opus", "low")


def test_a_typed_agent_task_resolves_through_provider_declared_options(tmp_path):
    """`provider-owns-runtime-mechanics` (invocation, runtime options, skill
    loading): the task selects a harness profile and options by name, and the
    provider's own declaration is what turns them into a command line. An
    option the provider does not declare is refused, not emitted flagless."""
    from kraft import harness as _harness

    skills = tmp_path / "skills"
    (skills / "house-method").mkdir(parents=True)
    (skills / "house-method" / "SKILL.md").write_text("THE-METHOD\n")
    task = _v1_agent_task(
        id="write",
        harness="claude",
        prompt="Produce the specification.",
        skill="house-method",
        model="opus",
        effort="high",
    )

    _claude_profile()
    inv = agent.resolve_agent_task(task, None, None, skills_dir=skills)

    assert (inv.harness, inv.model, inv.effort) == ("claude", "opus", "high")
    assert inv.method_text == "THE-METHOD\n"
    h = _harness.load(None).valid["claude"]
    argv = _harness.build_argv(
        h, command=None, prompt="p", context="c", options={"model": inv.model, "effort": inv.effort}
    )
    # The provider spells them; Kraft only names them.
    assert argv[argv.index("--model") + 1] == "opus"
    assert argv[argv.index("--effort") + 1] == "high"
    # A provider that declares neither gets neither, rather than a bare value.
    assert "--model" not in _harness.build_argv(
        _bare_provider("plain"), command=None, prompt="p", context="c", options={"model": "opus"}
    )


def test_the_provider_spells_session_resumption_for_an_agent_task(tmp_path):
    """`provider-owns-runtime-mechanics` (session resumption): resuming a
    provider's own agent session is the provider's spelling of `resume`, and a
    provider that declares no `resume` capability silently starts fresh rather
    than being handed a flag it does not understand. Not process reattachment,
    which is `kraft.worker.reattach`'s unrelated concern."""
    from kraft import harness as _harness

    task = _v1_agent_task(id="turn", harness="claude", prompt="carry on")
    _claude_profile()
    inv = agent.resolve_agent_task(task, None, None)
    h = _harness.load(None).valid[inv.harness]

    resumed = _harness.build_argv(h, command=None, prompt="p", context="c", resume="sess-1")
    assert resumed[resumed.index("--resume") + 1] == "sess-1"
    assert "--resume" not in _harness.build_argv(h, command=None, prompt="p", context="c")
    assert "--resume" not in _harness.build_argv(
        _bare_provider("oneshot"), command=None, prompt="p", context="c", resume="sess-1"
    )
