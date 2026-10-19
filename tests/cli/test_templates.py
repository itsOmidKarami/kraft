"""`kraft admin templates show --resolved`: the rendering an extending chain gets."""

import yaml

from kraft.cli import templates


def test_show_resolved_marks_where_each_node_came_from():
    payload = {
        "chain": {"id": "team", "nodes": [{"id": "build"}, {"id": "lint"}]},
        "provenance": {
            "parent": "default",
            "nodes": {"build": "overridden", "lint": "added"},
            "overrides": {"build": ["build.main.t"]},
            "removed": ["external_approval"],
        },
    }
    out = templates._render_resolved(payload)
    assert out.splitlines()[:4] == [
        "# extends default",
        "#   build: overridden (build.main.t)",
        "#   lint: added",
        "#   external_approval: removed",
    ]
    assert yaml.safe_load(out)["id"] == "team"
    plain = yaml.safe_dump(payload["chain"], sort_keys=False).rstrip("\n")
    assert templates._render_resolved({**payload, "provenance": None}) == plain
