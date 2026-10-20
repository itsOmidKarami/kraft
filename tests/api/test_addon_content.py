"""An add-on's chains and components on the Templates and Library screens:
listed with their add-on, refused for editing, and copied into the local
library through the draft operations."""

import json

import pytest
import yaml
from support.addons import AGENT, chain, load_release, load_tree

from kraft.templates.models import MaterializedChain

READ_ONLY = "comes from add-on release@acme; extend it or copy it to your library"


@pytest.fixture
def loaded(client, tmp_path):
    load_release(client, tmp_path)
    return client


def test_addon_entries_carry_their_addon(loaded):
    chains = {c["id"]: c["addon"] for c in loaded.get("/api/templates/chains").json()}
    components = {
        c["id"]: c["addon"] for c in loaded.get("/api/templates/library").json()["components"]
    }

    addon = {"id": "release@acme", "version": "1.0.0", "repo": None, "inheritance": None}
    assert (chains["release:ship"], chains["default"]) == (addon, None)
    assert components["tasks.release:base"] == addon
    assert {v for k, v in components.items() if ":" not in k} == {None}


def _put_chain(client):
    return client.put("/api/templates/chains/release:ship", json={"text": "nodes: []\n"})


def _chains_draft(client):
    op = {"op": "add_node", "id": "x", "after": None}
    return client.post("/api/drafts/chains/release:ship/ops", json={"ops": [op]})


def _library_op(client):
    op = {"op": "set_field", "path": "tasks.release:base", "field": "prompt", "value": "mine"}
    return client.post("/api/drafts/library/library/ops", json={"ops": [op]})


def _library_op_by_container(client):
    op = {"op": "add_step", "container": "nodes.release:build", "id": "extra"}
    return client.post("/api/drafts/library/library/ops", json={"ops": [op]})


@pytest.mark.parametrize(
    "write, names",
    [
        (_put_chain, "release:ship"),
        (_chains_draft, "release:ship"),
        (_library_op, "release:base"),
        (_library_op_by_container, "release:build"),
    ],
    ids=["put-chain", "chains-draft", "library-op", "library-op-by-container"],
)
def test_saving_an_addon_entry_answers_409(loaded, write, names):
    """409 with the way out, not the 400 a qualified id's shape would get."""
    answer = write(loaded)

    assert answer.status_code == 409, answer.text
    assert answer.json()["detail"] == f"{names} {READ_ONLY}"
    assert loaded.get("/api/drafts").json() == []


def test_a_chain_that_extends_an_addon_component_is_the_operators_to_edit(loaded):
    """What a local chain extends is a value in it, not an address into the add-on."""
    ops = [
        {"op": "new_chain", "from": "release:ship"},
        {"op": "add_node", "at": 1, "id": "extra", "kind": "exec"},
    ]
    answer = loaded.post("/api/drafts/chains/mine/ops", json={"ops": ops})
    assert answer.status_code == 200, answer.text


def test_a_new_chain_can_extend_an_addon_chain(loaded):
    op = {"op": "new_chain", "extends": "release:ship"}
    answer = loaded.post("/api/drafts/chains/mine/ops", json={"ops": [op]})
    assert answer.status_code == 200, answer.text
    draft = loaded.get("/api/drafts/chains/mine").json()
    written = yaml.safe_load(draft["files"]["chains/mine.yaml"])
    assert written == {"id": "mine", "extends": "release:ship"}
    assert loaded.post("/api/drafts/chains/mine/publish").status_code == 200
    assert not loaded.get("/api/health").json()["invalid_templates"]


def _copy_chain(client):
    op = {"op": "new_chain", "from": "release:ship"}
    answer = client.post("/api/drafts/chains/mine/ops", json={"ops": [op]})
    return answer, "chains/mine.yaml"


def _copy_component(client):
    op = {"op": "copy_component", "ref": "tasks.release:base", "name": "mine"}
    answer = client.post("/api/drafts/library/library/ops", json={"ops": [op]})
    return answer, "library.yaml"


@pytest.mark.parametrize("copy", [_copy_chain, _copy_component], ids=["chain", "component"])
def test_copy_from_an_addon(loaded, copy):
    """The copy is the operator's own, and still resolves: what the add-on
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


def _load_team(client, tmp_path, parent_chain="ship", own_add=()):
    """`team@pay`, a child of `release@acme`: a task and a chain (`own`, on
    `parent_chain`, with `own_add` entries) that extend the parent's by its
    name, which the library holds as a hidden level."""
    from support.addons import install, make_collection

    from kraft.api import deps

    st = client.app.state
    requires = {"kraft": "2", "harnesses": ["codex"], "addons": {"release@acme": "1"}}
    own = {"extends": f"release:{parent_chain}", **({"add": own_add} if own_add else {})}
    team = {
        "manifest": {"requires": requires},
        "library": {"tasks": {"scan": {"extends": "release:base", "prompt": "team scan"}}},
        "chains": {"own": own},
    }
    collection = make_collection(tmp_path / "pay", {"team": team}, name="pay")
    install(st.templates_dir, st.run_dirs.addons, collection, "team")
    deps._reload_templates(st)


@pytest.mark.parametrize(
    "op, file, written",
    [
        (
            {"op": "new_chain", "from": "team:own"},
            "chains/mine.yaml",
            {"id": "mine", "extends": "release:ship"},
        ),
        (
            {"op": "copy_component", "ref": "tasks.team:scan", "name": "mine"},
            "library.yaml",
            {"extends": "release:base", "prompt": "team scan"},
        ),
    ],
    ids=["child-chain", "child-component"],
)
def test_a_copy_from_a_child_addon_names_its_parent(loaded, tmp_path, op, file, written):
    """A child's reference to its parent's entry comes along as the parent's
    own id, never as the hidden level the child reads it through, so the copy
    resolves (against the parent as the parent serves it)."""
    _load_team(loaded, tmp_path)
    kind, key = ("chains", "mine") if file.startswith("chains/") else ("library", "library")
    answer = loaded.post(f"/api/drafts/{kind}/{key}/ops", json={"ops": [op]})
    assert answer.status_code == 200, answer.text
    text = loaded.get(f"/api/drafts/{kind}/{key}").json()["files"][file]
    found = yaml.safe_load(text)

    assert (found if kind == "chains" else found["tasks"]["mine"]) == written
    assert "^" not in text
    assert loaded.post(f"/api/drafts/{kind}/{key}/publish").status_code == 200
    assert not loaded.get("/api/health").json()["invalid_templates"]


def test_a_local_chain_through_a_child_addon_detaches(client, tmp_path):
    """A local chain on `team:own`, which extends `release:tuned` through a
    hidden level: its draft opens, and detaching it writes the node the
    hidden level overrides as resolved."""
    from kraft.api import deps

    tuned = {"extends": "ship", "overrides": {"n.main.t": {"prompt": "tuned"}}}
    load_release(client, tmp_path, chains={"ship": chain("base"), "tuned": tuned})
    extra = {
        "after": "n",
        "node": {"id": "extra", "kind": "exec", "tasks": [{"id": "x", "extends": "release:base"}]},
    }
    _load_team(client, tmp_path, parent_chain="tuned", own_add=[extra])
    st = client.app.state
    (st.templates_dir / "chains" / "mine.yaml").write_text("id: mine\nextends: team:own\n")
    deps._reload_templates(st)

    viewed = client.get("/api/drafts/chains/mine")
    detached = client.post("/api/drafts/chains/mine/ops", json={"ops": [{"op": "detach"}]})

    assert viewed.status_code == 200, viewed.text
    assert detached.status_code == 200, detached.text
    text = client.get("/api/drafts/chains/mine").json()["files"]["chains/mine.yaml"]
    written = yaml.safe_load(text)
    task = written["nodes"][0]["tasks"][0]
    assert task == {"id": "t", **AGENT, "skill": "release:notes", "prompt": "tuned"}
    # The node the child added names its parent's task by the parent's id.
    assert written["nodes"][1]["tasks"][0] == {"id": "x", "extends": "release:base"}
    assert "^" not in text
    assert client.post("/api/drafts/chains/mine/publish").status_code == 200
    assert not client.get("/api/health").json()["invalid_templates"]


def test_a_drafts_sources_name_inherited_chains_publicly(client, tmp_path):
    """The draft view says which chain a field comes from; one reached through
    a child add-on's hidden level reads as the ancestor's own id."""
    from kraft.api import deps

    tuned = {"extends": "ship", "overrides": {"n.main.t": {"prompt": "tuned"}}}
    load_release(client, tmp_path, chains={"ship": chain("base"), "tuned": tuned})
    _load_team(client, tmp_path, parent_chain="tuned")
    st = client.app.state
    (st.templates_dir / "chains" / "mine.yaml").write_text("id: mine\nextends: team:own\n")
    deps._reload_templates(st)

    sources = client.get("/api/drafts/chains/mine").json()["result"]["sources"]

    assert sources["n.main.t"]["prompt"]["source"] == "chain:release:tuned"
    assert "^" not in json.dumps(sources)


@pytest.fixture
def repo_library(client, tmp_path):
    from support.addons import accept_repo_library, repo_with_library

    from kraft.api import deps

    st = client.app.state
    accept_repo_library(st.templates_dir, st.run_dirs.addons, repo_with_library(tmp_path), "app")
    deps._reload_templates(st)
    return client


_IN_ITS_REPO = "edit it in that repository's .kraft/"


@pytest.mark.parametrize(
    "op, kind, key, says",
    [
        pytest.param(
            {"op": "new_chain", "from": "app:ship"},
            "chains",
            "mine",
            f"app:ship is in app's repo library, visible only to items in app; {_IN_ITS_REPO}",
            id="repo-library-chain",
        ),
        pytest.param(
            {"op": "new_chain", "extends": "app:ship"},
            "chains",
            "mine",
            f"app:ship is in app's repo library, visible only to items in app; {_IN_ITS_REPO}",
            id="repo-library-extend",
        ),
        pytest.param(
            [
                {"op": "new_chain", "extends": "default"},
                {"op": "change_chain_base", "base": "app:ship"},
            ],
            "chains",
            "mine",
            f"app:ship is in app's repo library, visible only to items in app; {_IN_ITS_REPO}",
            id="repo-library-base",
        ),
        pytest.param(
            {"op": "copy_component", "ref": "tasks.app:base", "name": "mine"},
            "library",
            "library",
            f"app:base is in app's repo library, visible only to items in app; {_IN_ITS_REPO}",
            id="repo-library-component",
        ),
        pytest.param(
            {"op": "add_node", "id": "x", "after": None},
            "chains",
            "app:ship",
            f"app:ship comes from app's repo library; {_IN_ITS_REPO}",
            id="read-only",
        ),
    ],
)
def test_a_repo_librarys_content_is_edited_in_its_repository(repo_library, op, kind, key, says):
    """Not copied into the instance's own library: that would make an
    instance-wide chain out of one repository's."""
    ops = op if isinstance(op, list) else [op]
    answer = repo_library.post(f"/api/drafts/{kind}/{key}/ops", json={"ops": ops})

    assert answer.status_code in (409, 422), answer.text
    assert answer.json()["detail"] == says
    assert repo_library.get("/api/drafts").json() == []


@pytest.mark.parametrize("ref", ["tasks.implementer", "tasks.another-tool:base"])
def test_only_an_addons_component_is_copied(loaded, ref):
    """A local component is edited where it is; a namespace no loaded add-on
    holds is not an add-on's."""
    op = {"op": "copy_component", "ref": ref, "name": "mine"}
    answer = loaded.post("/api/drafts/library/library/ops", json={"ops": [op]})
    assert answer.status_code == 422 and "is not an add-on's component" in answer.json()["detail"]


def test_an_addons_agent_profile_is_listed_with_its_addon(client, tmp_path):
    load_release(client, tmp_path, profiles={"deep": {"model": {"codex": "gpt-5"}}})

    listed = {
        p["id"]: p["addon"] for p in client.get("/api/harnesses/profiles").json()["agent_profiles"]
    }

    assert listed["release:deep"] == {"id": "release@acme", "version": "1.0.0", "repo": None}
    assert {addon for id, addon in listed.items() if ":" not in id} == {None}


def test_an_addon_chain_opens_read_only_through_the_draft_view(loaded):
    """The Chains screen renders from this route: an add-on's chain is shown as
    published, with its add-on, and no draft is ever made of it."""
    view = loaded.get("/api/drafts/chains/release:ship").json()

    assert (view["draft"], view["addon"]) == (
        False,
        {"id": "release@acme", "version": "1.0.0", "repo": None, "inheritance": None},
    )
    assert [n["id"] for n in view["result"]["resolved"]["nodes"]] == ["n"]
    assert view["result"]["problems"] == [] and view["result"]["changes"] == []
    authored = yaml.safe_load(view["files"]["chains/release:ship.yaml"])
    assert authored["nodes"][0]["tasks"][0]["extends"] == "release:base"
    assert loaded.get("/api/drafts/chains/default").json()["addon"] is None


def test_the_library_view_carries_the_addons_components_beside_its_own(loaded):
    """Shown on the Library screen, and never among the files a publish writes."""
    view = loaded.get("/api/drafts/library/library").json()

    assert view["addon_library"] == {
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
    assert loaded.get("/api/drafts/chains/default").json()["addon_library"] is None


BUILD = {
    "kind": "exec",
    "tasks": [{"id": "coder", "extends": "base"}, {"id": "checker", "extends": "base"}],
}


def _mine(client, *ops):
    start = [{"op": "new_chain"}, {"op": "add_node", "at": 0, "id": "n", "kind": "exec"}]
    answer = client.post("/api/drafts/chains/mine/ops", json={"ops": [*start, *ops]})
    assert answer.status_code == 200, answer.text
    return yaml.safe_load(answer.json()["files"]["chains/mine.yaml"])


def test_an_addons_components_are_extended_from_a_local_chain(client, tmp_path):
    """Extending is the way to use what an add-on ships: the editor's own ops
    reach its nodes and tasks, and a task it adds gets an id of its own."""
    library = {"tasks": {"base": {**AGENT, "skill": "notes"}}, "nodes": {"build": BUILD}}
    load_release(client, tmp_path, library=library)

    written = _mine(
        client,
        {"op": "add_step", "container": "n", "at": 0},
        {"op": "add_task", "container": "n", "step": "step_1", "extends": "release:base"},
        {"op": "add_node", "at": 1, "id": "m", "kind": "exec"},
        {"op": "extend", "node": "m", "base": "release:build"},
        {"op": "set_field", "path": "m.main.coder", "field": "prompt", "value": "mine"},
        {"op": "add_node", "at": 2, "id": "untouched", "kind": "exec"},
        {"op": "extend", "node": "untouched", "base": "release:build"},
    )

    first, second, third = written["nodes"]
    assert first["steps"][0]["tasks"] == [{"id": "base", "extends": "release:base"}]
    assert second["extends"] == "release:build"
    # The inherited task is owned to change it, and the step keeps its other fields.
    # Owning one inherited task keeps the one beside it.
    assert second["tasks"] == [
        {"extends": "release:base", "prompt": "mine", "id": "coder"},
        {"extends": "release:base", "id": "checker"},
    ]
    # The YAML tab reads a part the node only inherits, through the add-on parent.
    assert third == {"id": "untouched", "extends": "release:build"}
    part = client.get("/api/drafts/chains/mine/fragment", params={"path": "untouched.main.checker"})
    assert part.status_code == 200 and "extends: release:base" in part.json()["text"]
    assert client.post("/api/drafts/chains/mine/publish").status_code == 200
    assert not client.get("/api/health").json()["invalid_templates"]


def test_an_item_draft_adds_an_addons_node(client, repo, tmp_path):
    """The item draft's add-node menu lists an add-on's nodes by their
    qualified name; picking one adds it, and the item then pins the add-on."""
    library = {"tasks": {"base": {**AGENT, "skill": "notes"}}, "nodes": {"build": BUILD}}
    load_release(client, tmp_path, library=library)
    body = {"title": "draft me", "repo": str(repo), "chain_template": "default"}
    wid = client.post("/api/work-items", json=body).json()["id"]
    node = {"id": "release_build", "extends": "release:build"}
    op = {"op": "add_node", "after": "verification", "node": node}

    put = client.put(f"/api/work-items/{wid}/draft", json={"ops": [op]})

    assert put.status_code == 200, put.text
    assert put.json()["problems"] == []
    assert "release_build" in [n["id"] for n in put.json()["nodes"]]
    assert client.post(f"/api/work-items/{wid}/draft/apply").status_code == 200
    item = client.get(f"/api/work-items/{wid}").json()
    assert "release" in MaterializedChain.from_json(item["materialized_chain"]).chain.addons


def _row(rows, id):
    return next(row for row in rows if row["id"] == id)


def _chains_list(client):
    answer = client.get("/api/templates/chains")
    mark = _row(answer.json(), "team-payments:default")["addon"]["inheritance"]
    assert mark["origin"] == "overridden"
    return answer


def _library(client):
    answer = client.get("/api/templates/library")
    component = _row(answer.json()["components"], "tasks.platform-dept:security_scan")
    assert component["addon"]["inheritance"] == {
        "origin": "overridden",
        "parent": {"id": "company@acme", "namespace": "company"},
        "author": {"id": "platform-dept@acme", "version": "1.0.0"},
    }
    return answer


def _resolved(client, chain_id="platform-dept:default"):
    answer = client.get(f"/api/templates/chains/{chain_id}/resolved")
    view = answer.json()
    if chain_id == "platform-dept:default":
        assert view["inherited_nodes"]["scan"]["origin"] == "overridden"
        assert view["addon"]["inheritance"]["origin"] == "inherited"
    else:
        assert view["provenance"]["parent"] == "platform-dept:default"
    return answer


def _draft_view(client, chain_id="platform-dept:default"):
    answer = client.get(f"/api/drafts/chains/{chain_id}")
    view = answer.json()
    assert view["addon"]["inheritance"] is not None
    resolved = view["result"]["resolved"]
    if chain_id == "platform-dept:default":
        assert resolved["inherited_nodes"]["scan"]["entry"] == "tasks.platform-dept:security_scan"
    else:
        assert resolved["provenance"]["parent"] == "platform-dept:default"
    return answer


def _no_layer_names(client):
    answers = [_chains_list(client), _library(client), _resolved(client), _draft_view(client)]
    answers += [
        _resolved(client, "team-payments:default"),
        _draft_view(client, "team-payments:default"),
    ]
    for answer in answers:
        assert "^" not in answer.text, answer.request.url
    return answers[-1]


@pytest.mark.parametrize(
    "request_",
    [_chains_list, _library, _resolved, _draft_view, _no_layer_names],
    ids=["chains-list", "library", "resolved", "draft-view", "no-layer-names"],
)
def test_a_child_addons_entries_carry_their_marks(client, tmp_path, request_):
    """Every payload that names an entry's add-on says whether a child add-on
    inherited, overrode or added it, and a child's chain marks its nodes,
    never through a hidden level's name."""
    load_tree(client, tmp_path)
    answer = request_(client)
    assert answer.status_code == 200, answer.text
