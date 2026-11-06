"""The draft ops write a task's `outputs` and `reads` as plain lists."""

from __future__ import annotations

from support.drafts import apply_ops as apply
from support.drafts import draft_state

from kraft.drafts import authored


async def test_set_field_writes_outputs_and_reads_as_lists(templates_dir, database):
    st = draft_state(templates_dir, database)
    writer, reader = "spec.main.author", "implementation.main.implement"
    set_field = [
        ("set_field", {"path": path, "field": field, "value": value})
        for path, field, value in [
            (writer, "outputs.files", ["report final.xml"]),
            (writer, "outputs.variables", ["ID"]),
            (reader, "reads", [writer]),
        ]
    ]
    r = apply(st, *set_field)
    assert authored.at(r.model, writer)["outputs"] == {
        "files": ["report final.xml"],
        "variables": ["ID"],
    }
    assert authored.at(r.model, reader)["reads"] == [writer]
    assert r.problems == []
