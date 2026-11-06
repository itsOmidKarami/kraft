"""A task declaring `outputs` is launched with `$KRAFT_OUTPUT_DIR`: its own
session's folder, the declared names in its prompt, the cap it launched under
recorded beside it (`kraft.outputs`)."""

from __future__ import annotations

import pytest
from support.harness import entry_of, v1_node, v1_task

from kraft import executor, item_paths, outputs
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


async def test_outputs_note_survives_resume(item_on, fake_agent, paused_session):
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
