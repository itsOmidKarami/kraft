"""Plan-driven chain revision (Kraft-oydes, Ruling 208): the change set a
`chain_revision` artifact carries, read strictly, and applied to a
materialized chain only when the result still validates as one."""

import json

import pytest

from kraft.templates import revision


def _artifact(change: dict | str, *, before: str = "", after: str = "") -> str:
    body = change if isinstance(change, str) else json.dumps(change)
    return f"---\nwork_item_ids: [w1]\n---\n{before}```json\n{body}\n```\n{after}"


# ── the artifact is read strictly ──


def test_a_fenced_change_set_parses():
    changes = revision.parse(
        _artifact(
            {
                "rationale": "the plan adds a migration",
                "skip": [{"node": "brief", "evidence": "plan: no brief needed"}],
                "overrides": {"build.main.run": {"effort": "high", "evidence": "plan task 3"}},
            }
        )
    )
    assert [s.node for s in changes.skip] == ["brief"]
    assert changes.overrides["build.main.run"].effort == "high"
    assert not changes.empty


def test_a_change_set_with_only_a_rationale_is_empty():
    assert revision.parse(_artifact({"rationale": "the chain fits"})).empty


@pytest.mark.parametrize(
    ("text", "match"),
    [
        (_artifact({"rationale": "ok"}, before="Here it is:\n"), "exactly one"),
        (_artifact({"rationale": "ok"}, after="\nThanks.\n"), "exactly one"),
        ("---\nwork_item_ids: [w1]\n---\n" + json.dumps({"rationale": "ok"}), "exactly one"),
        (_artifact("{not json"), "not JSON"),
        (_artifact('{"rationale": "a", "rationale": "b"}'), "twice"),
        (_artifact({"rationale": "ok", "reorder": ["a"]}), "reorder"),
        (_artifact({"rationale": ""}), "rationale"),
        (_artifact({"rationale": "ok", "skip": ["brief"]}), "skip"),
        (_artifact({"rationale": "ok", "skip": [{"node": "brief"}]}), "evidence"),
        (
            _artifact(
                {
                    "rationale": "ok",
                    "overrides": {"a.main.b": {"allowed_tools": [], "evidence": "e"}},
                }
            ),
            "allowed_tools",
        ),
        (_artifact({"rationale": "ok", "overrides": {"a": {"evidence": "e"}}}), "sets nothing"),
        (
            _artifact(
                {
                    "rationale": "ok",
                    "add": [
                        {
                            "after": "build",
                            "evidence": "e",
                            "node": {"id": "x", "tasks": [{"id": "t", "prompt": "anything"}]},
                        }
                    ],
                }
            ),
            r"node\.tasks\.0",
        ),
        (
            _artifact(
                {
                    "rationale": "ok",
                    "add": [{"after": "build", "evidence": "e", "node": {"id": "x"}}],
                }
            ),
            "extends",
        ),
    ],
    ids=[
        "prose-before",
        "prose-after",
        "no-fence",
        "not-json",
        "duplicate-key",
        "unknown-key",
        "empty-rationale",
        "bare-skip-id",
        "skip-without-evidence",
        "safety-policy-field",
        "override-setting-nothing",
        "added-task-not-from-the-library",
        "added-node-with-no-shape",
    ],
)
def test_anything_but_one_strict_change_set_is_refused(text, match):
    with pytest.raises(revision.RevisionError, match=match):
        revision.parse(text)
