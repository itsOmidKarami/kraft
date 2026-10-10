"""A plugin's chains and components on the Templates and Library screens:
listed with their plugin, refused for editing, and copied into the local
library through the draft operations."""

import pytest
import yaml
from support.plugins import load_release

READ_ONLY = "comes from plugin release@acme; extend it or copy it to your library"


@pytest.fixture
def loaded(client, tmp_path):
    load_release(client, tmp_path)
    return client


def test_plugin_entries_carry_their_plugin(loaded):
    chains = {c["id"]: c["plugin"] for c in loaded.get("/api/templates/chains").json()}
    components = {
        c["id"]: c["plugin"] for c in loaded.get("/api/templates/library").json()["components"]
    }

    plugin = {"id": "release@acme", "version": "1.0.0"}
    assert (chains["release:ship"], chains["default"]) == (plugin, None)
    assert components["tasks.release:base"] == plugin
    assert {v for k, v in components.items() if ":" not in k} == {None}


def _put_chain(client):
    return client.put("/api/templates/chains/release:ship", json={"text": "nodes: []\n"})


def _chains_draft(client):
    op = {"op": "add_node", "id": "x", "after": None}
    return client.post("/api/drafts/chains/release:ship/ops", json={"ops": [op]})


def _library_op(client):
    op = {"op": "set_field", "path": "tasks.release:base", "field": "prompt", "value": "mine"}
    return client.post("/api/drafts/library/library/ops", json={"ops": [op]})


@pytest.mark.parametrize(
    "write, names",
    [
        (_put_chain, "release:ship"),
        (_chains_draft, "release:ship"),
        (_library_op, "release:base"),
    ],
    ids=["put-chain", "chains-draft", "library-op"],
)
def test_saving_a_plugin_entry_answers_409(loaded, write, names):
    """409 with the way out, not the 400 a qualified id's shape would get."""
    answer = write(loaded)

    assert answer.status_code == 409, answer.text
    assert answer.json()["detail"] == f"{names} {READ_ONLY}"
    assert loaded.get("/api/drafts").json() == []


def test_a_chain_that_extends_a_plugin_component_is_the_operators_to_edit(loaded):
    """What a local chain extends is a value in it, not an address into the plugin."""
    ops = [
        {"op": "new_chain", "from": "release:ship"},
        {"op": "add_node", "at": 1, "id": "extra", "kind": "exec"},
    ]
    answer = loaded.post("/api/drafts/chains/mine/ops", json={"ops": ops})
    assert answer.status_code == 200, answer.text


def _copy_chain(client):
    op = {"op": "new_chain", "from": "release:ship"}
    answer = client.post("/api/drafts/chains/mine/ops", json={"ops": [op]})
    return answer, "chains/mine.yaml"


def _copy_component(client):
    op = {"op": "copy_component", "ref": "tasks.release:base", "name": "mine"}
    answer = client.post("/api/drafts/library/library/ops", json={"ops": [op]})
    return answer, "library.yaml"


@pytest.mark.parametrize("copy", [_copy_chain, _copy_component], ids=["chain", "component"])
def test_copy_from_a_plugin(loaded, copy):
    """The copy is the operator's own, and still resolves: what the plugin
    wrote as a bare name of its own comes along qualified."""
    answer, file = copy(loaded)
    assert answer.status_code == 200, answer.text
    kind = "chains" if file.startswith("chains/") else "library"
    key = "mine" if kind == "chains" else "library"
    draft = loaded.get(f"/api/drafts/{kind}/{key}").json()
    written = yaml.safe_load(draft["files"][file])

    if kind == "chains":
        assert written["id"] == "mine"
        assert written["nodes"][0]["tasks"][0]["extends"] == "release:base"
    else:
        assert written["tasks"]["mine"]["skill"] == "release:notes"
        assert "release:base" not in written["tasks"]
    assert loaded.post(f"/api/drafts/{kind}/{key}/publish").status_code == 200
    assert not loaded.get("/api/health").json()["invalid_templates"]


@pytest.mark.parametrize("ref", ["tasks.implementer", "tasks.another-tool:base"])
def test_only_a_plugins_component_is_copied(loaded, ref):
    """A local component is edited where it is; a namespace no loaded plugin
    holds is not a plugin's."""
    op = {"op": "copy_component", "ref": ref, "name": "mine"}
    answer = loaded.post("/api/drafts/library/library/ops", json={"ops": [op]})
    assert answer.status_code == 422 and "is not a plugin's component" in answer.json()["detail"]


def test_a_plugins_agent_profile_is_listed_with_its_plugin(client, tmp_path):
    load_release(client, tmp_path, profiles={"deep": {"model": {"codex": "gpt-5"}}})

    listed = {
        p["id"]: p["plugin"] for p in client.get("/api/harnesses/profiles").json()["agent_profiles"]
    }

    assert listed["release:deep"] == {"id": "release@acme", "version": "1.0.0"}
    assert {plugin for id, plugin in listed.items() if ":" not in id} == {None}


def test_a_plugin_chain_opens_read_only_through_the_draft_view(loaded):
    """The Chains screen renders from this route: a plugin's chain is shown as
    published, with its plugin, and no draft is ever made of it."""
    view = loaded.get("/api/drafts/chains/release:ship").json()

    assert (view["draft"], view["plugin"]) == (False, {"id": "release@acme", "version": "1.0.0"})
    assert [n["id"] for n in view["result"]["resolved"]["nodes"]] == ["n"]
    assert view["result"]["problems"] == [] and view["result"]["changes"] == []
    authored = yaml.safe_load(view["files"]["chains/release:ship.yaml"])
    assert authored["nodes"][0]["tasks"][0]["extends"] == "release:base"
    assert loaded.get("/api/drafts/chains/default").json()["plugin"] is None


def test_the_library_view_carries_the_plugins_components_beside_its_own(loaded):
    """Shown on the Library screen, and never among the files a publish writes."""
    view = loaded.get("/api/drafts/library/library").json()

    assert view["plugin_library"] == {
        "tasks": {
            "release:base": {
                "kind": "agent",
                "harness": "codex",
                "prompt": "local base",
                "skill": "release:notes",
            }
        }
    }
    assert "release:base" not in view["files"]["library.yaml"]
    assert loaded.get("/api/drafts/chains/default").json()["plugin_library"] is None
