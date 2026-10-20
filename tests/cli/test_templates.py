"""`kraft admin templates show --resolved`: the rendering an extending chain gets."""

import pytest
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


def _addon(mark, parent="company"):
    inheritance = None
    if mark:
        inheritance = {
            "origin": mark,
            "parent": {"id": "company", "namespace": parent},
            "author": {"id": "company", "version": "1.0.0"},
        }
    return {"id": "platform-dept@platform", "version": "2.1.0", "inheritance": inheritance}


def _chain_payload(mark):
    return {"id": "platform-dept:hotfix", "text": "id: hotfix\n", "addon": _addon(mark)}


def _resolved_payload(**extra):
    return {
        "chain": {"id": "platform-dept:default", "nodes": [{"id": "scan"}]},
        "addon": _addon("inherited"),
        **extra,
    }


_PROVENANCE = {"parent": "base", "nodes": {"scan": "inherited"}, "overrides": {}, "removed": []}
_OVERRIDDEN = {"scan": {"origin": "overridden", "entry": "company:security_scan"}}
_ADDED = {"scan": {"origin": "added", "entry": "team:security_scan"}}


@pytest.mark.parametrize(
    "render, payload, expected",
    [
        pytest.param(
            templates._render_chain_text,
            _chain_payload("inherited"),
            "# platform-dept:hotfix (platform-dept@platform 2.1.0; inherited from company)",
            id="show-inherited",
        ),
        pytest.param(
            templates._render_chain_text,
            _chain_payload("overridden"),
            "# platform-dept:hotfix (platform-dept@platform 2.1.0; overridden here)",
            id="show-overridden",
        ),
        pytest.param(
            templates._render_components,
            {
                "components": [
                    {
                        "id": "team-payments:pci_review",
                        "kind": "task",
                        "used_by": [],
                        "addon": {
                            **_addon("added"),
                            "id": "team-payments@payments",
                            "version": "1.4.0",
                        },
                    }
                ]
            },
            "team-payments:pci_review (team-payments@payments 1.4.0; added here)",
            id="library-added",
        ),
        pytest.param(
            templates._render_resolved,
            _resolved_payload(inherited_nodes=_OVERRIDDEN),
            [
                "# platform-dept:default (platform-dept@platform 2.1.0; inherited from company)",
                "#   scan: security_scan overridden here",
            ],
            id="resolved-nodes",
        ),
        pytest.param(
            templates._render_resolved,
            _resolved_payload(provenance=_PROVENANCE, inherited_nodes=_OVERRIDDEN),
            ["#   scan: inherited; security_scan overridden here"],
            id="resolved-with-provenance",
        ),
        pytest.param(
            templates._render_resolved,
            _resolved_payload(inherited_nodes=_ADDED),
            ["#   scan: security_scan added here"],
            id="resolved-added",
        ),
        pytest.param(
            templates._named,
            {
                "id": "release:ship",
                "addon": _addon(None) | {"id": "release@acme", "version": "1.4.0"},
            },
            "release:ship (release@acme 1.4.0)",
            id="root-addon-unchanged",
        ),
    ],
)
def test_templates_output_marks_inheritance(render, payload, expected):
    out = render(payload)
    for line in [expected] if isinstance(expected, str) else expected:
        assert line in out
    if render is templates._named:
        assert out == expected
    if render is templates._render_resolved:
        assert yaml.safe_load(out)["id"] == "platform-dept:default"
