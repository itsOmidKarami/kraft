"""The template library as its own resource: every reusable component
`library.yaml` declares, which chains use it, the lint issues that name it,
and a save of the whole file that refuses an edit leaving any chain
unresolvable (Kraft-6xkkm, `template-library-api-lists-its-components`)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

#: A chain whose only task extends `lonely`, the task `_with_extras` adds.
LONELY_CHAIN = {
    "id": "lonely-chain",
    "nodes": [{"id": "run", "kind": "exec", "tasks": [{"id": "t", "extends": "lonely"}]}],
}


def snapshot(root: Path) -> dict[str, bytes]:
    """Every file under `root` with its bytes: equal before and after means
    nothing was written."""
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def _with_extras(templates_dir):
    """Components the shipped library does not have: `unused`, which no chain
    references, and `lonely`, which selects a method that does not exist and is
    used by `lonely-chain` only -- so lint has an issue that names it."""
    path = templates_dir / "library.yaml"
    library = yaml.safe_load(path.read_text())
    library["tasks"]["unused"] = {"kind": "subprocess", "command": "true"}
    # A prefix of `lonely`, in two sections: an issue naming `tasks.lonely`
    # does not name it, and the bare name `lone` is ambiguous.
    library["tasks"]["lone"] = {"kind": "subprocess", "command": "true"}
    library["nodes"]["lone"] = {"kind": "exec", "tasks": [{"id": "t", "extends": "lone"}]}
    library["tasks"]["lonely"] = {
        "kind": "agent",
        "harness": "codex_default",
        "prompt": "p",
        "skill": "kraft:no-such-method",
    }
    path.write_text(yaml.safe_dump(library, sort_keys=False))
    (templates_dir / "chains" / "lonely-chain.yaml").write_text(yaml.safe_dump(LONELY_CHAIN))


def _components(client) -> dict[str, dict]:
    response = client.get("/api/templates/library")
    assert response.status_code == 200, response.text
    return {c["id"]: c for c in response.json()["components"]}


# ── GET /templates/library ──


def test_the_library_lists_every_component_with_the_chains_that_use_it(client, templates_dir):
    body = client.get("/api/templates/library").json()
    components = {c["id"]: c for c in body["components"]}
    shipped = yaml.safe_load((templates_dir / "library.yaml").read_text())

    assert body["file"] == str(templates_dir / "library.yaml")
    assert body["text"] == (templates_dir / "library.yaml").read_text()
    # Every section, every name, nothing invented.
    assert set(components) == {
        f"{section}.{name}" for section in shipped for name in shipped[section]
    }
    implementer = components["tasks.implementer"]
    assert implementer["kind"] == "tasks"
    assert implementer["name"] == "implementer"
    # As written: no defaults filled in, no `id` injected.
    assert implementer["definition"] == shipped["tasks"]["implementer"]
    # `default` reaches it through its `implementation` node, `quick-task` directly.
    assert implementer["used_by"] == ["default", "quick-task"]
    assert components["nodes.verification"]["used_by"] == ["default"]
    # Selected by name from `spec_author`, which only `default` uses.
    assert components["steering.project-standards"]["used_by"] == ["default"]
    assert all(c["issues"] == [] for c in components.values())


@pytest.mark.api_client(edit_templates=_with_extras)
def test_a_component_no_chain_uses_is_listed_used_by_none(client):
    assert _components(client)["tasks.unused"]["used_by"] == []


@pytest.mark.api_client(edit_templates=_with_extras)
def test_a_lint_issue_is_listed_on_the_component_it_names(client):
    components = _components(client)
    [issue] = components["tasks.lonely"]["issues"]
    assert issue["chain"] == "lonely-chain"
    assert "kraft:no-such-method" in issue["message"]
    assert components["tasks.lonely"]["used_by"] == ["lonely-chain"]
    # Named by that issue only: not the component whose name it prefixes.
    assert all(c["issues"] == [] for id, c in components.items() if id != "tasks.lonely")


@pytest.mark.parametrize(
    "ref", ["tasks.implementer", "implementer"], ids=["qualified-id", "unique-bare-name"]
)
def test_one_component_by_id(client, ref):
    response = client.get(f"/api/templates/library/{ref}")
    assert response.status_code == 200
    assert response.json()["id"] == "tasks.implementer"
    assert response.json()["used_by"] == ["default", "quick-task"]


@pytest.mark.api_client(edit_templates=_with_extras)
def test_a_bare_name_two_sections_share_is_404_and_each_id_answers(client):
    assert client.get("/api/templates/library/lone").status_code == 404
    assert client.get("/api/templates/library/nodes.lone").json()["kind"] == "nodes"


def test_an_unknown_component_is_404(client):
    response = client.get("/api/templates/library/tasks.nope")
    assert response.status_code == 404
    assert "tasks.nope" in response.json()["detail"]


def test_each_chain_names_the_library_components_each_node_uses(client):
    """What the Chains screen links a node to its components by."""
    chains = {c["id"]: c for c in client.get("/api/templates/chains").json()}
    uses = chains["default"]["uses"]
    assert uses["implementation"] == ["nodes.implementation", "tasks.implementer"]
    assert uses["spec"] == ["steering.project-standards", "tasks.spec_author"]
    assert "spec_approval" not in uses  # a gate references nothing


# ── PUT /templates/library ──


def test_a_library_save_that_breaks_a_chain_is_refused_and_writes_nothing(client, templates_dir):
    path = templates_dir / "library.yaml"
    text = path.read_text().replace("  implementer:\n", "  implementer_renamed:\n")
    before = snapshot(templates_dir)

    response = client.put("/api/templates/library", json={"text": text})

    assert response.status_code == 422
    assert "implementer" in response.json()["detail"]
    assert snapshot(templates_dir) == before
    # And the running library is the old one.
    assert "tasks.implementer" in _components(client)


@pytest.mark.parametrize(
    "text, reason",
    [
        ("tasks: [unclosed\n", "not YAML"),
        ("- a list\n", "mapping"),
        ("tasks:\n  t: {kind: forge, target: mr.ci, wait: {timeout: 5}}\n", "retired"),
    ],
    ids=["not-yaml", "not-a-mapping", "retired-key"],
)
def test_a_library_save_refuses_what_the_chain_save_refuses(client, templates_dir, text, reason):
    before = snapshot(templates_dir)
    response = client.put("/api/templates/library", json={"text": text})
    assert response.status_code == 422
    assert reason in response.json()["detail"]
    assert snapshot(templates_dir) == before


def test_a_library_save_is_written_verbatim_and_live(client, templates_dir):
    path = templates_dir / "library.yaml"
    text = path.read_text() + "\n# a comment survives\n"
    text = text.replace(
        "tasks:\n", "tasks:\n  added:\n    kind: subprocess\n    command: 'true'\n", 1
    )

    response = client.put("/api/templates/library", json={"text": text})

    assert response.status_code == 200, response.text
    assert path.read_text() == text
    assert "tasks.added" in {c["id"] for c in response.json()["components"]}
    assert "tasks.added" in _components(client)


@pytest.mark.api_client(edit_templates=_with_extras)
def test_a_chain_already_broken_does_not_block_an_unrelated_save(client, templates_dir):
    """`lonely-chain` did not resolve before the edit and does not after: the
    edit is not what broke it, so it is not refused for it."""
    path = templates_dir / "library.yaml"
    text = path.read_text() + "# unrelated\n"
    assert client.put("/api/templates/library", json={"text": text}).status_code == 200
    assert path.read_text() == text
