"""A launch's context as sections (`agent.context_sections`), which the
steering preview shows (B23) and `build_context` joins. In its own file because
`test_agent.py` is at its line ceiling (`dev/check_tests.py`)."""

import pytest
import yaml

from kraft.adapters import agent


def test_context_sections_join_to_the_launch_context_in_launch_order():
    every = dict(
        title="t",
        task_instruction="do it",
        repo_path="/r",
        work_item_id="w1",
        node_id="implementation",
        hook_point="implementation.main.implement",
        session_id="s1",
        usage_source="result_file",
        artifact="spec",
        review_package="/pkg",
        bot_feedback="/bots.json",
        method_text="METHOD",
        intent_dir="docs/intent/",
    )
    sections = agent.context_sections(
        **every, skill="kraft:spec", steering=[("repo:house", "HOUSE"), ("task:std", "STD")]
    )
    built = agent.build_context(context_channel="prompt", steering_texts=("HOUSE", "STD"), **every)

    assert "".join(text for _, _, text in sections) == built
    assert [(kind, source) for kind, source, _ in sections] == [
        ("contract", "kraft"),
        ("document", "spec"),
        ("review_package", "kraft"),
        ("bot_feedback", "kraft"),
        ("skill", "kraft:spec"),
        ("intent", "docs/intent/"),
        ("steering", "repo:house"),
        ("steering", "task:std"),
        ("usage", "kraft"),
    ]


@pytest.mark.parametrize("artifact", [None, "spec"], ids=["summary", "document"])
def test_front_matter_asked_for_keeps_a_digits_only_id_a_string(artifact):
    """Ids are quoted in the front matter a worker is told to write: unquoted,
    YAML reads `01234567` as a number, and the index drops the id."""
    built = agent.build_context(
        context_channel="prompt",
        title="t",
        task_instruction="do it",
        repo_path="/r",
        work_item_id="01234567",
        node_id="n",
        hook_point="h",
        session_id="012345",
        usage_source="result_file",
        artifact=artifact,
    )
    blocks = [yaml.safe_load(b) for b in built.split("---\n")[1::2]]
    by_kind = {b.get("kind", "summary"): b for b in blocks}
    assert set(by_kind) == ({"summary", "specs"} if artifact else {"summary"})
    assert by_kind["summary"]["worker_session_id"] == "012345"
    for fm in by_kind.values():
        assert fm["work_item_ids"] == ["01234567"]


def test_a_resumed_launch_names_its_own_session_files(run):
    """Kraft-vjo4g: a resumed conversation remembers the summary and result
    paths of the session it began in, and wrote there; each session's are its
    own, so the turn must be told this one's."""
    cmd = run(session_id="s2", resume_session_id="cli-session-abc")["cmd"]
    prompt = cmd[cmd.index("-p") + 1]
    out = "/kraft/run/items/w1/sessions/s2/out"
    assert f"{out}/summary.md" in prompt
    assert f"$KRAFT_RESULT_PATH, which is now {out}/result.json" in prompt
    assert 'worker_session_id: "s2"' in prompt
