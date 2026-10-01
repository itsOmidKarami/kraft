"""A launch's context as sections (`agent.context_sections`), which the
steering preview shows (B23) and `build_context` joins. In its own file because
`test_agent.py` is at its line ceiling (`dev/check_tests.py`)."""

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
        ("skill", "kraft:spec"),
        ("intent", "docs/intent/"),
        ("steering", "repo:house"),
        ("steering", "task:std"),
        ("usage", "kraft"),
    ]
