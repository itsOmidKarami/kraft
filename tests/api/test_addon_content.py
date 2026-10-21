"""An add-on's chains and components on the Templates and Library screens:
listed with their add-on, refused for editing, and copied into the local
library through the draft operations."""

import json

import pytest
import yaml
from support.addons import (
    AGENT,
    COMPANY,
    TEAM,
    chain,
    load_release,
    load_tree,
    publish,
    update_addons,
)

from kraft.api import deps
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


def _release_and_team(client, tmp_path):
    load_release(client, tmp_path)
    _load_team(client, tmp_path)


def _tree_dept_extends_company(client, tmp_path):
    """The example tree with the dept's `security_scan` building on the
    company's and selecting steering by a bare name: a team copy merges two
    hidden levels, and the steering is the team's."""
    scan = {"extends": "company:security_scan", "steering": ["house"]}
    load_tree(client, tmp_path, dept={"library": {"tasks": {"security_scan": scan}}})


def _tree_aliased_dept(client, tmp_path):
    load_tree(client, tmp_path, aliases={"platform-dept": "dept"})


def _tree_dept_scan_on_a_profile(client, tmp_path):
    """The dept's `security_scan` picks a profile; the team's picks a model."""
    scan = {**AGENT, "prompt": "dept scan", "profile": "company:slow"}
    load_tree(client, tmp_path, dept={"library": {"tasks": {"security_scan": scan}}})


def _tree_node_and_task_share_a_name(client, tmp_path):
    """The company ships a node and a task both named `build`; the team's chain
    `own` has a node on the dept's `build`, by the dept's name."""
    library = COMPANY["library"]
    company = {
        **COMPANY,
        "library": {
            **library,
            "tasks": {**library["tasks"], "build": {**AGENT, "prompt": "company build"}},
            "nodes": {"build": {"kind": "exec", "tasks": [{"id": "b", "extends": "build"}]}},
        },
    }
    own = {"nodes": [{"id": "n", "extends": "platform-dept:build"}]}
    load_tree(client, tmp_path, company=company, team={**TEAM, "chains": {"own": own}})


def _tree_layer_extends_its_own(client, tmp_path):
    """The dept's `security_scan` extends its own `base_scan`, which picks a
    model and effort, and switches to a profile; the team's sets `effort`
    alone, so the profile goes and nothing brings the model back."""
    base_scan = {**AGENT, "model": "m-base", "effort": "high"}
    scan = {"extends": "base_scan", "profile": "company:slow"}
    dept = {"library": {"tasks": {"base_scan": base_scan, "security_scan": scan}}}
    team_scan = {"extends": "platform-dept:security_scan", "effort": "low"}
    load_tree(client, tmp_path, dept=dept, team=_team_tasks(security_scan=team_scan))


def _tree_layer_drops_a_policy(client, tmp_path):
    """The dept's `security_scan` sets its `base_scan`'s policy to null; the
    team's sets one of its own, which is all it keeps."""
    base_scan = {**AGENT, "policy": {"time_cap_minutes": 5}}
    scan = {"extends": "base_scan", "policy": None}
    dept = {"library": {"tasks": {"base_scan": base_scan, "security_scan": scan}}}
    team_scan = {"extends": "platform-dept:security_scan", "policy": {"total_time_cap_minutes": 9}}
    load_tree(client, tmp_path, dept=dept, team=_team_tasks(security_scan=team_scan))


def _tree_node_id_from_the_library(client, tmp_path):
    """The company's chain `default` ends in a node written `{extends: bn}`,
    whose id the library node `bn` gives."""
    library = COMPANY["library"]
    built = {"id": "built", "kind": "exec", "tasks": [{"id": "b", "extends": "hotfix"}]}
    default = COMPANY["chains"]["default"]
    company = {
        **COMPANY,
        "library": {**library, "nodes": {"bn": built}},
        "chains": {
            **COMPANY["chains"],
            "default": {"nodes": [*default["nodes"], {"extends": "bn"}]},
        },
    }
    load_tree(client, tmp_path, company=company)


def _tree_sibling_chain(client, tmp_path):
    """The team's chain `sibling` extends its own `default`, a public chain."""
    load_tree(
        client,
        tmp_path,
        team={**TEAM, "chains": {**TEAM["chains"], "sibling": {"extends": "default"}}},
    )


def _team_tasks(**tasks):
    """`TEAM` with `tasks` added to its library, or in place of its own."""
    library = TEAM["library"]
    return {**TEAM, "library": {**library, "tasks": {**library["tasks"], **tasks}}}


def _node(id, task, extends):
    return {"id": id, "kind": "exec", "tasks": [{"id": task, "extends": extends}]}


#: `team-payments:default` copied: the parent's nodes and the child's, each
#: naming the child's own entries, so the team's `security_scan` runs.
TEAM_DEFAULT = {
    "id": "mine",
    "nodes": [
        _node("verification", "v", "team-payments:verify"),
        _node("pci_review", "p", "team-payments:pci_review"),
        _node("scan", "s", "team-payments:security_scan"),
    ],
}
COPY_TEAM_DEFAULT = {"op": "new_chain", "from": "team-payments:default"}
COPY_TEAM_SCAN = {
    "op": "copy_component",
    "ref": "tasks.team-payments:security_scan",
    "name": "mine",
}


def _runs(client, chain_id, library=None):
    """Each task of `chain_id`, resolved by the running server (or `library`):
    what it runs."""
    resolved = (library or client.app.state.library).resolve_chain(chain_id)
    return {
        rt.path: rt.task.model_dump(mode="json", exclude_unset=True)
        for node in resolved.nodes
        for rt in node.tasks()
    }


def _task_runs(client, name):
    """What task `name` runs as a local chain's task extending it."""
    st = client.app.state
    probe = {"id": "probe", "nodes": [_node("n", "t", name)]}
    library, id = st.library.with_chain(st.templates_dir / "chains" / "probe.yaml", probe)
    return _runs(client, id, library)["n.main.t"]


@pytest.mark.parametrize(
    "load, op, written",
    [
        pytest.param(
            _release_and_team,
            {"op": "new_chain", "from": "team:own"},
            {"id": "mine", "nodes": [_node("n", "t", "team:base")]},
            id="child-chain",
        ),
        pytest.param(
            _release_and_team,
            {"op": "copy_component", "ref": "tasks.team:scan", "name": "mine"},
            {**AGENT, "skill": "release:notes", "prompt": "team scan"},
            id="child-component",
        ),
        pytest.param(load_tree, COPY_TEAM_DEFAULT, TEAM_DEFAULT, id="tree-chain"),
        pytest.param(_tree_aliased_dept, COPY_TEAM_DEFAULT, TEAM_DEFAULT, id="tree-chain-alias"),
        pytest.param(
            load_tree,
            COPY_TEAM_SCAN,
            {**AGENT, "prompt": "dept scan", "model": "pci-model"},
            id="tree-component",
        ),
        pytest.param(
            _tree_dept_extends_company,
            COPY_TEAM_SCAN,
            {
                **AGENT,
                "prompt": "company scan",
                "steering": ["team-payments:house"],
                "model": "pci-model",
            },
            id="tree-component-two-levels",
        ),
        pytest.param(
            _tree_dept_scan_on_a_profile,
            COPY_TEAM_SCAN,
            {**AGENT, "prompt": "dept scan", "model": "pci-model"},
            id="tree-component-own-route-wins",
        ),
        pytest.param(
            _tree_node_and_task_share_a_name,
            {"op": "new_chain", "from": "team-payments:own"},
            {"id": "mine", "nodes": [_node("n", "b", "team-payments:build")]},
            id="tree-chain-node-read-as-a-node",
        ),
        pytest.param(
            _tree_layer_extends_its_own,
            COPY_TEAM_SCAN,
            {**AGENT, "effort": "low"},
            id="tree-component-layer-extends-its-own",
        ),
        pytest.param(
            _tree_layer_drops_a_policy,
            COPY_TEAM_SCAN,
            {**AGENT, "policy": {"total_time_cap_minutes": 9}},
            id="tree-component-layer-drops-a-policy",
        ),
        pytest.param(
            _tree_node_id_from_the_library,
            COPY_TEAM_DEFAULT,
            {**TEAM_DEFAULT, "nodes": [*TEAM_DEFAULT["nodes"], {"extends": "team-payments:bn"}]},
            id="tree-chain-node-id-from-the-library",
        ),
        pytest.param(
            _tree_sibling_chain,
            {"op": "new_chain", "from": "team-payments:sibling"},
            TEAM_DEFAULT,
            id="tree-chain-extends-a-sibling",
        ),
    ],
)
def test_a_copy_from_a_child_addon_runs_what_it_serves(client, tmp_path, load, op, written):
    """A child's copy is flattened: what it inherits through a hidden level
    is written out merged, and every other reference names the child's own
    entries, so the copy runs what the child serves, overrides included, and
    never names a hidden level."""
    load(client, tmp_path)
    kind, key = ("chains", "mine") if op["op"] == "new_chain" else ("library", "library")
    file = "chains/mine.yaml" if kind == "chains" else "library.yaml"
    answer = client.post(f"/api/drafts/{kind}/{key}/ops", json={"ops": [op]})
    assert answer.status_code == 200, answer.text
    text = client.get(f"/api/drafts/{kind}/{key}").json()["files"][file]
    found = yaml.safe_load(text)

    assert (found if kind == "chains" else found["tasks"]["mine"]) == written
    assert "^" not in text
    assert client.post(f"/api/drafts/{kind}/{key}/publish").status_code == 200
    assert not client.get("/api/health").json()["invalid_templates"]
    if kind == "chains":
        assert _runs(client, "mine") == _runs(client, op["from"])
    else:
        assert _task_runs(client, "mine") == _task_runs(client, op["ref"].split(".", 1)[1])
    if op == COPY_TEAM_DEFAULT:
        assert _runs(client, "mine")["scan.main.s"]["model"] == "pci-model"


def test_a_child_addons_copy_keeps_its_nodes_across_a_parent_update(client, tmp_path):
    """The copy is the operator's own chain: an update that adds a node to the
    parent's chain does not reach it, and it still runs the child's override."""
    collection = load_tree(client, tmp_path)
    answer = client.post("/api/drafts/chains/mine/ops", json={"ops": [COPY_TEAM_DEFAULT]})
    assert answer.status_code == 200, answer.text
    assert client.post("/api/drafts/chains/mine/publish").status_code == 200
    audit = _node("audit", "a", "hotfix")
    chains = {
        **COMPANY["chains"],
        "default": {"nodes": [*COMPANY["chains"]["default"]["nodes"], audit]},
    }
    publish(collection, "company", version="1.1.0", chains=chains)
    st = client.app.state
    update_addons(st.templates_dir, st.run_dirs.addons, ["company@acme"])
    deps._reload_templates(st)

    assert "audit.main.a" in _runs(client, "team-payments:default")
    runs = _runs(client, "mine")
    assert not client.get("/api/health").json()["invalid_templates"]
    assert list(runs) == ["verification.main.v", "pci_review.main.p", "scan.main.s"]
    assert runs["scan.main.s"]["model"] == "pci-model"


#: A team whose entries reach what the dept does not ship: a task and a chain
#: task extending a missing task, a node extending a task, and a chain on that
#: chain.
BROKEN_TEAM = {
    **_team_tasks(broken={"extends": "platform-dept:nothing"}),
    "chains": {
        "uses": {"nodes": [_node("n", "t", "platform-dept:nothing")]},
        "on_uses": {"extends": "uses"},
    },
}
BROKEN_TEAM["library"] = {
    **BROKEN_TEAM["library"],
    "nodes": {"wrong_kind": {"extends": "platform-dept:hotfix"}},
}


@pytest.mark.parametrize(
    "op, says",
    [
        pytest.param(
            {"op": "copy_component", "ref": "tasks.team-payments:broken", "name": "mine"},
            "platform-dept:nothing",
            id="component",
        ),
        pytest.param(
            {"op": "copy_component", "ref": "nodes.team-payments:wrong_kind", "name": "mine"},
            "platform-dept:hotfix",
            id="component-of-another-namespace",
        ),
        pytest.param(
            {"op": "new_chain", "from": "team-payments:uses"},
            "platform-dept:nothing",
            id="chain-task",
        ),
        pytest.param(
            {"op": "new_chain", "from": "team-payments:on_uses"},
            "team-payments:on_uses does not resolve, so it can't be copied",
            id="chain-on-a-chain",
        ),
    ],
)
def test_a_broken_copy_from_a_child_addon_names_no_hidden_level(client, tmp_path, op, says):
    """A child's entry that reaches a missing ancestor entry is copied, or
    refused, naming it as the ancestor's own, never a hidden level, and never
    with a 500."""
    load_tree(client, tmp_path, team=BROKEN_TEAM)
    kind, key = ("chains", "mine") if op["op"] == "new_chain" else ("library", "library")
    answer = client.post(f"/api/drafts/{kind}/{key}/ops", json={"ops": [op]})

    assert answer.status_code < 500, answer.text
    assert says in answer.text
    assert "^" not in answer.text


def test_a_local_chain_through_a_child_addon_detaches(client, tmp_path):
    """A local chain on `team:own`, which extends `release:tuned` through a
    hidden level: its draft opens, and detaching it writes the node the
    hidden level overrides as resolved."""
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
    # The node the child added extends its parent's task: merged in, as the
    # child serves it, never named through the hidden level.
    assert written["nodes"][1]["tasks"][0] == {"id": "x", **AGENT, "skill": "release:notes"}
    assert "^" not in text
    assert client.post("/api/drafts/chains/mine/publish").status_code == 200
    assert not client.get("/api/health").json()["invalid_templates"]


def test_a_drafts_sources_name_inherited_chains_publicly(client, tmp_path):
    """The draft view says which chain a field comes from; one reached through
    a child add-on's hidden level reads as the ancestor's own id."""
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
