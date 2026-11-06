"""A task declaring `outputs` is launched with `$KRAFT_OUTPUT_DIR`: its own
session's folder, the declared names in its prompt, the cap it launched under
recorded beside it (`kraft.outputs`)."""

from __future__ import annotations

import asyncio

import pytest
from support.harness import entry_of, git, v1_node, v1_task

from kraft import executor, item_paths, outputs
from kraft.adapters import forge
from kraft.adapters import subprocess as _subprocess
from kraft.executor import dispatch, prompts

NO_SETUP = entry_of({"setup_command": ""})
DECLARED = {"files": ["a.txt"], "variables": ["K"]}
#: What a writer does: record where it was told to write, then leave its outputs.
WRITE = (
    'printf %s "$KRAFT_OUTPUT_DIR" > "{seen}"; echo kept > "$KRAFT_OUTPUT_DIR/a.txt"; '
    'echo K=1 > "$KRAFT_OUTPUT_DIR/variables.env"'
)


def writer(kind: str, seen, **fields):
    """The node `w`, whose one task `t` declares `DECLARED`."""
    shell = WRITE.format(seen=seen)
    if kind == "agent":
        task = v1_task("t", kind="agent", prompt="Write it.", outputs=DECLARED, **fields)
    else:
        task = v1_task("t", command=f"sh -c '{shell}'", outputs=DECLARED, **fields)
    return [v1_node("w", tasks=[task])]


async def run(it, **launch):
    return await executor.run_once(
        it.database,
        it.run_dirs,
        work_item_id=it.id,
        launch=executor.LaunchContext(repo_entry=NO_SETUP, **launch),
    )


@pytest.mark.parametrize("kind", ["agent", "subprocess"])
async def test_writer_launch(item_on, fake_agent, monkeypatch, tmp_path, kind):
    seen = tmp_path / "seen.txt"
    monkeypatch.setenv("KRAFT_FAKE_AGENT_SHELL", WRITE.format(seen=seen))
    it = await item_on(writer(kind, seen))

    assert await run(it, outputs_max_bytes=12345) == "completed"

    [session] = it.sessions("w")
    files = it.files(session["id"])
    assert seen.read_text() == str(files.folder / "out" / "outputs")
    decl = outputs.read_decl(files)
    assert (decl.writer, decl.files, decl.variables, decl.max_bytes) == (
        "w.main.t",
        ("a.txt",),
        ("K",),
        12345,
    )
    if kind == "agent":
        [prompt] = fake_agent.prompts()
        assert prompts.outputs_note(decl) in prompt
    else:
        assert not fake_agent.prompts()


async def test_a_task_that_declares_nothing_gets_no_output_folder(
    item_on, fake_agent, monkeypatch, tmp_path
):
    seen = tmp_path / "seen.txt"
    monkeypatch.setenv(
        "KRAFT_FAKE_AGENT_SHELL", f'printf %s "${{KRAFT_OUTPUT_DIR-unset}}" > {seen}'
    )
    it = await item_on([v1_node("w", tasks=[v1_task("t", kind="agent", prompt="Go.")])])

    assert await run(it) == "completed"

    [session] = it.sessions("w")
    assert seen.read_text() == "unset"
    assert outputs.read_decl(it.files(session["id"])) is None
    assert "KRAFT_OUTPUT_DIR" not in fake_agent.prompts()[0]


async def test_each_fallback_candidate_gets_its_own_output_dir(
    item_on, fake_agent, monkeypatch, tmp_path
):
    seen = tmp_path / "seen.txt"
    monkeypatch.setenv("KRAFT_FAKE_AGENT_SHELL", WRITE.format(seen=seen))
    monkeypatch.setenv("KRAFT_FAKE_AGENT_RATE_LIMIT_MODELS", "opus")
    monkeypatch.setenv("KRAFT_FAKE_AGENT_RESETS_AT", "4102444800")
    task = v1_task(
        "t",
        kind="agent",
        prompt="Write it.",
        harness="claude",
        model="opus",
        outputs=DECLARED,
        fallback=[{"harness": "claude", "model": "sonnet"}],
    )
    it = await item_on([v1_node("w", tasks=[task])])

    assert await run(it) == "completed"

    first, second = sorted(it.sessions("w"), key=lambda s: s["started_at"])
    assert first["id"] != second["id"]
    for session in (first, second):
        assert outputs.read_decl(it.files(session["id"])) is not None
    assert seen.read_text() == str(it.files(second["id"]).folder / "out" / "outputs")


@pytest.fixture
def paused_session(monkeypatch):
    """The dispatch resumes the paused session `old` of the provider's `prov`."""
    monkeypatch.setattr(dispatch, "_resumable_session", lambda *a, **k: ("old", "prov"))


async def test_outputs_note_survives_resume(item_on, fake_agent, paused_session, monkeypatch):
    monkeypatch.setenv("KRAFT_FAKE_AGENT_SHELL", WRITE.format(seen="/dev/null"))
    it = await item_on(writer("agent", "/dev/null"))

    assert await run(it) == "completed"

    [prompt] = fake_agent.prompts()
    assert prompts.AGENT_RESUMED_NOTE.strip() in prompt
    assert "$KRAFT_OUTPUT_DIR" in prompt.split(prompts.AGENT_RESUMED_NOTE.strip())[1]


async def test_resumed_agent_keeps_outputs(
    item_on, fake_agent, paused_session, monkeypatch, tmp_path
):
    seen = tmp_path / "carried.txt"
    monkeypatch.setenv(
        "KRAFT_FAKE_AGENT_SHELL",
        f'cat "$KRAFT_OUTPUT_DIR/a.txt" > {seen}; ls "$KRAFT_OUTPUT_DIR" >> {seen}',
    )
    it = await item_on(writer("agent", "/dev/null"))
    old = item_paths.writer_dir(it.files("old"))
    old.mkdir(parents=True)
    (old / "a.txt").write_text("from the paused run\n")
    (old / "link").symlink_to(tmp_path)

    await run(it)

    assert seen.read_text().split() == ["from", "the", "paused", "run", "a.txt"]


@pytest.mark.parametrize("kind", ["agent", "subprocess"])
async def test_the_output_folder_is_made_and_carried_off_the_event_loop(
    item_on, fake_agent, paused_session, monkeypatch, tmp_path, kind
):
    """A big folder left by a paused run is copied on a thread: the server keeps serving."""
    on_loop = {}
    for name in ("declare", "carry_over"):
        real = getattr(outputs, name)

        def spy(*a, _real=real, _name=name, **k):
            try:
                asyncio.get_running_loop()
                on_loop[_name] = True
            except RuntimeError:
                on_loop[_name] = False
            return _real(*a, **k)

        monkeypatch.setattr(outputs, name, spy)
    monkeypatch.setenv("KRAFT_FAKE_AGENT_SHELL", WRITE.format(seen="/dev/null"))
    it = await item_on(writer(kind, tmp_path / "seen.txt"))

    await run(it)

    assert on_loop == {"declare": False, **({"carry_over": False} if kind == "agent" else {})}


# -- readers ---------------------------------------------------------------------

WRITER = "w.main.t"
OTHER = "w.main.other"
READS = [WRITER]


@pytest.fixture
def launches(monkeypatch):
    """The keyword arguments of every `adapters.subprocess.run_task`, agent and
    subprocess launches alike: what a reader is handed."""
    seen = []
    real = _subprocess.run_task

    async def spy(db, run_dirs, **kw):
        seen.append(kw)
        return await real(db, run_dirs, **kw)

    monkeypatch.setattr(_subprocess, "run_task", spy)
    return seen


def kept(it, writer: str = WRITER, variables: str = "K=1\n"):
    """A snapshot `writer` left, as a capture would."""
    folder = item_paths.snapshot_dir(it.run_dirs, it.id, writer)
    folder.mkdir(parents=True)
    (folder / "a.txt").write_text("data")
    (folder / "variables.env").write_text(variables)
    return folder


def reader(kind: str, **fields):
    if kind == "agent":
        return v1_task("rd", kind="agent", prompt="Read it.", **fields)
    return v1_task("rd", **fields)


def writers_then(reader_node: dict) -> list[dict]:
    """The node `w` with two writers, then `reader_node`."""
    both = [
        v1_task("t", outputs=DECLARED),
        v1_task("other", outputs={"files": ["b.txt"]}),
    ]
    return [v1_node("w", tasks=both), reader_node]


@pytest.mark.parametrize("kind", ["agent", "subprocess"])
async def test_reader_launch(item_on, fake_agent, launches, kind):
    it = await item_on(writers_then(v1_node("r", tasks=[reader(kind, reads=READS)])), "r")
    folder = kept(it)
    kept(it, OTHER)

    assert await run(it) == "completed"

    [launch] = launches
    assert launch["ro_paths"] == (str(folder),)
    assert launch["env"]["KRAFT_READS_DIR"] == str(item_paths.outputs_dir(it.run_dirs, it.id))
    note = prompts.reads_note([WRITER], {WRITER: ("K",)})
    assert [note in p for p in fake_agent.prompts()] == ([True] if kind == "agent" else [])


async def test_reader_without_reads_gets_no_env(item_on, launches):
    it = await item_on(writers_then(v1_node("r", tasks=[reader("subprocess")])), "r")
    kept(it)

    assert await run(it) == "completed"

    [launch] = launches
    assert "KRAFT_READS_DIR" not in launch["env"]
    assert not launch.get("ro_paths")


async def test_reader_gets_nothing_from_missing_snapshot(item_on, fake_agent, launches):
    it = await item_on(writers_then(v1_node("r", tasks=[reader("agent", reads=READS)])), "r")

    assert await run(it) == "completed"

    [launch] = launches
    assert not launch["ro_paths"]
    assert launch["env"]["KRAFT_READS_DIR"]
    assert "KRAFT_READS_DIR" not in fake_agent.prompts()[0]


SLOTS = {
    "on_failure": (
        lambda node: node.on_failure[0].tasks[0],
        lambda t: {"on_failure": {"tasks": [t]}},
    ),
    "repair": (
        lambda node: node.fix_loop[0].tasks[0],
        lambda t: {"fix_loop": {"tasks": [t]}},
    ),
    "judge": (
        lambda node: node.judge,
        lambda t: {"fix_loop": {"tasks": [v1_task("fix")], "judge": t}},
    ),
    "escalation": (lambda node: node.escalation, lambda t: {"escalation": t}),
}


@pytest.mark.parametrize("slot", SLOTS)
async def test_reads_note_survives_instruction_override(item_on, fake_agent, slot):
    pick, place = SLOTS[slot]
    handler = reader("agent", reads=READS)
    it = await item_on(writers_then(v1_node("r", tasks=[v1_task("main")], **place(handler))), "r")
    kept(it)
    node = it.chain.chain.nodes[1]

    await dispatch.dispatch_node(
        it.database,
        it.run_dirs,
        pick(node),
        node,
        it.row(),
        it.repo,
        launch=executor.LaunchContext(repo_entry=NO_SETUP),
        instruction_override="Fix the failing check.",
    )

    [prompt] = fake_agent.prompts()
    assert "Fix the failing check." in prompt
    assert prompts.reads_note([WRITER], {WRITER: ()}).strip().splitlines()[0] in prompt


async def test_each_fallback_candidate_gets_the_reads(item_on, fake_agent, launches, monkeypatch):
    monkeypatch.setenv("KRAFT_FAKE_AGENT_RATE_LIMIT_MODELS", "opus")
    monkeypatch.setenv("KRAFT_FAKE_AGENT_RESETS_AT", "4102444800")
    task = reader(
        "agent",
        reads=READS,
        harness="claude",
        model="opus",
        fallback=[{"harness": "claude", "model": "sonnet"}],
    )
    it = await item_on(writers_then(v1_node("r", tasks=[task])), "r")
    folder = kept(it)

    assert await run(it) == "completed"

    assert len(launches) == 2
    assert {launch["ro_paths"] for launch in launches} == {(str(folder),)}
    assert all("KRAFT_READS_DIR" in launch["env"] for launch in launches)
    assert all("`$KRAFT_READS_DIR/w.main.t/`" in p for p in fake_agent.prompts())


@pytest.mark.parametrize("reused", [False, True], ids=["new-run", "reused-session-keeps"])
async def test_redispatch_clears_snapshot(item_on, reused):
    it = await item_on([v1_node("w", tasks=[v1_task("t", outputs={"files": ["a.txt"]})])])
    if reused:
        # A crash re-entry: the writer's session is done against the branch as it stands.
        head = git(it.repo, "rev-parse", "HEAD").strip()
        await it.session("s1", "w.main.t", "done", head_sha=head)
    folder = kept(it)
    [node] = it.chain.chain.nodes

    await dispatch.measure_node(
        it.database, it.run_dirs, it.id, node, it.row(), it.repo,
        launch=executor.LaunchContext(repo_entry=NO_SETUP),
    )  # fmt: skip

    assert (folder / "a.txt").exists() is reused


async def test_writer_rerun_after_reader_passed_does_not_retrigger(item_on, repo):
    """Clearing a snapshot affects only readers still to come: the reader that
    already passed is not run again, and its node stays complete."""
    chain = [
        v1_node("w", tasks=[v1_task("t", outputs={"files": ["a.txt"]})]),
        v1_node("r", tasks=[v1_task("rd", reads=READS)]),
    ]
    it = await item_on(chain)
    assert await run(it) == "completed"
    before = [s["id"] for s in it.sessions("r")]
    assert before

    git(repo, "commit", "--allow-empty", "-m", "moved")
    [writer_node, _] = it.chain.chain.nodes
    await dispatch.measure_node(
        it.database, it.run_dirs, it.id, writer_node, it.row(), it.repo,
        launch=executor.LaunchContext(repo_entry=NO_SETUP),
    )  # fmt: skip

    assert [s["id"] for s in it.sessions("r")] == before
    assert it.status() == "completed"


async def test_declared_variables_are_the_chains_current_declaration(item_on):
    it = await item_on(writers_then(v1_node("r", tasks=[reader("subprocess", reads=READS)])), "r")

    assert dispatch.declared_variables(it.row(), [WRITER, OTHER, "r.main.rd", "gone.main.x"]) == {
        WRITER: ("K",),
        OTHER: (),
        "r.main.rd": (),
        "gone.main.x": (),
    }


@pytest.mark.parametrize(
    ("target", "fields", "reads_chain"),
    [
        pytest.param("ci.pipeline", {"pipeline": "7"}, True, id="ci.pipeline"),
        pytest.param("mr.ci", {}, False, id="mr.ci"),
    ],
)
async def test_only_a_ci_pipeline_dispatch_reads_the_chains_declarations(
    item_on, monkeypatch, target, fields, reads_chain
):
    """Every forge dispatch (each `mr.ci` poll) would otherwise re-validate the
    item's whole chain to hand a handler variables it never reads."""
    monkeypatch.setattr(forge.run, "resolve", lambda name: forge.FakeForge())
    read = []
    real = dispatch.declared_variables
    monkeypatch.setattr(dispatch, "declared_variables", lambda *a: read.append(a) or real(*a))
    task = {"id": "ci", "kind": "forge", "target": target, **fields}
    it = await item_on([{"id": "w", "kind": "exec", "tasks": [task]}])

    await executor.run_once(
        it.database,
        it.run_dirs,
        work_item_id=it.id,
        launch=executor.LaunchContext(
            repo_entry=entry_of({"setup_command": "", "forge": "github"})
        ),
    )

    assert bool(read) is reads_chain
