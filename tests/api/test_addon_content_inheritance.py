"""Copying a child add-on's chains and components into the local library:
what a copy writes, what it runs, and what it refuses."""

import json

import pytest
import yaml
from support.addons import (
    AGENT,
    COMPANY,
    DEPT,
    TEAM,
    chain,
    exec_node,
    load_release,
    load_tree,
    publish,
    update_addons,
)

from kraft.api import deps


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


def _tree_node_escalation(client, tmp_path, team_escalation, *, chain=False, **company_esc):
    """The dept's node `B` escalates to the company's task `E`; the team's `B`
    (or, with `chain`, the team's chain `own`'s node on the dept's) names
    `team_escalation` for the same slot, which the resolver merges onto the
    dept's raw slot before it expands it."""
    library = COMPANY["library"]
    esc = {**AGENT, "prompt": "company esc", **company_esc}
    company = {**COMPANY, "library": {**library, "tasks": {**library["tasks"], "E": esc}}}
    dept_b = {
        "kind": "exec",
        "tasks": [{"id": "t", "extends": "company:hotfix"}],
        "escalation": {"id": "e", "extends": "company:E"},
    }
    dept = {"library": {**DEPT["library"], "nodes": {"B": dept_b}}}
    team_b = {"extends": "platform-dept:B", "escalation": team_escalation}
    team = _team_tasks(other={**AGENT, "prompt": "team other"})
    if chain:
        team["chains"] = {**TEAM["chains"], "own": {"nodes": [{"id": "n", **team_b}]}}
    else:
        team["library"] = {**team["library"], "nodes": {"B": team_b}}
    load_tree(client, tmp_path, company=company, dept=dept, team=team)


def _tree_node_escalation_other(client, tmp_path, *, chain=False):
    """The team's `B` escalates to its own `other` instead."""
    _tree_node_escalation(client, tmp_path, {"id": "e", "extends": "other"}, chain=chain)


def _tree_chain_node_escalation_other(client, tmp_path):
    _tree_node_escalation_other(client, tmp_path, chain=True)


def _tree_node_escalation_profile(client, tmp_path):
    """The company's `E` picks a model and effort; the team's `B` routes the
    slot to a profile, which displaces them."""
    escalation = {"id": "e", "profile": "company:slow"}
    _tree_node_escalation(client, tmp_path, escalation, model="m-esc", effort="high")


def _team_tasks(**tasks):
    """`TEAM` with `tasks` added to its library, or in place of its own."""
    library = TEAM["library"]
    return {**TEAM, "library": {**library, "tasks": {**library["tasks"], **tasks}}}


#: `team-payments:default` copied: the parent's nodes and the child's, each
#: naming the child's own entries, so the team's `security_scan` runs.
TEAM_DEFAULT = {
    "id": "mine",
    "nodes": [
        exec_node("verification", "v", "team-payments:verify"),
        exec_node("pci_review", "p", "team-payments:pci_review"),
        exec_node("scan", "s", "team-payments:security_scan"),
    ],
}
COPY_TEAM_DEFAULT = {"op": "new_chain", "from": "team-payments:default"}
COPY_TEAM_B = {"op": "copy_component", "ref": "nodes.team-payments:B", "name": "mine"}
#: The dept's node `B` as a team copy writes it, before its escalation.
DEPT_B = {"kind": "exec", "tasks": [{"id": "t", **AGENT, "prompt": "company hotfix"}]}
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


def _component_runs(client, ref):
    """What component `ref` (`tasks.<name>` or `nodes.<name>`) runs as a local
    chain's task, or node, extending it."""
    section, name = ref.split(".", 1)
    st = client.app.state
    node = {"id": "n", "extends": name} if section == "nodes" else exec_node("n", "t", name)
    probe = {"id": "probe", "nodes": [node]}
    library, id = st.library.with_chain(st.templates_dir / "chains" / "probe.yaml", probe)
    runs = _runs(client, id, library)
    return runs if section == "nodes" else runs["n.main.t"]


@pytest.mark.parametrize(
    "load, op, written",
    [
        pytest.param(
            _release_and_team,
            {"op": "new_chain", "from": "team:own"},
            {"id": "mine", "nodes": [exec_node("n", "t", "team:base")]},
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
            {"id": "mine", "nodes": [exec_node("n", "b", "team-payments:build")]},
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
        pytest.param(
            _tree_node_escalation_other,
            COPY_TEAM_B,
            {**DEPT_B, "escalation": {"id": "e", "extends": "team-payments:other"}},
            id="tree-node-escalation-own-task",
        ),
        pytest.param(
            _tree_node_escalation_profile,
            COPY_TEAM_B,
            {
                **DEPT_B,
                "escalation": {
                    "id": "e",
                    **AGENT,
                    "prompt": "company esc",
                    "profile": "company:slow",
                },
            },
            id="tree-node-escalation-own-route",
        ),
        pytest.param(
            _tree_chain_node_escalation_other,
            {"op": "new_chain", "from": "team-payments:own"},
            {
                "id": "mine",
                "nodes": [
                    {
                        "id": "n",
                        **DEPT_B,
                        "escalation": {"id": "e", "extends": "team-payments:other"},
                    }
                ],
            },
            id="tree-chain-node-escalation-own-task",
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

    section = op["ref"].split(".", 1)[0] if kind == "library" else None
    assert (found if kind == "chains" else found[section]["mine"]) == written
    assert "^" not in text
    assert client.post(f"/api/drafts/{kind}/{key}/publish").status_code == 200
    assert not client.get("/api/health").json()["invalid_templates"]
    if kind == "chains":
        assert _runs(client, "mine") == _runs(client, op["from"])
    else:
        assert _component_runs(client, f"{section}.mine") == _component_runs(client, op["ref"])
    if op == COPY_TEAM_DEFAULT:
        assert _runs(client, "mine")["scan.main.s"]["model"] == "pci-model"


def test_a_child_addons_copy_keeps_its_nodes_across_a_parent_update(client, tmp_path):
    """The copy is the operator's own chain: an update that adds a node to the
    parent's chain does not reach it, and it still runs the child's override."""
    collection = load_tree(client, tmp_path)
    answer = client.post("/api/drafts/chains/mine/ops", json={"ops": [COPY_TEAM_DEFAULT]})
    assert answer.status_code == 200, answer.text
    assert client.post("/api/drafts/chains/mine/publish").status_code == 200
    audit = exec_node("audit", "a", "hotfix")
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
#: task extending a missing task, a task changing its parent's kind, a node
#: extending a task, and a chain on that chain.
BROKEN_TEAM = {
    **_team_tasks(
        broken={"extends": "platform-dept:nothing"},
        rekinded={"extends": "platform-dept:security_scan", "kind": "subprocess", "command": "x"},
    ),
    "chains": {
        "uses": {"nodes": [exec_node("n", "t", "platform-dept:nothing")]},
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
            {"op": "copy_component", "ref": "tasks.team-payments:rekinded", "name": "mine"},
            "extends 'platform-dept:security_scan' and cannot change kind 'agent' to 'subprocess'",
            id="component-changes-kind",
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


#: The dept ships a node `stage` beside its task `security_scan`, for the team's
#: slots to name in the wrong namespace.
SLOT_DEPT = {
    "library": {
        **DEPT["library"],
        "nodes": {"stage": {"kind": "exec", "tasks": [{"id": "t", "extends": "security_scan"}]}},
    }
}


def _on_a_node(id):
    return {"id": id, "extends": "platform-dept:stage"}


@pytest.mark.parametrize(
    "written",
    [
        pytest.param({"kind": "exec", "tasks": [_on_a_node("t")]}, id="tasks"),
        pytest.param(
            {"kind": "exec", "steps": [{"id": "s", "extends": "platform-dept:security_scan"}]},
            id="steps",
        ),
        pytest.param({"kind": "exec", "escalation": _on_a_node("e")}, id="escalation"),
        pytest.param({"kind": "exec", "fix_loop": {"judge": _on_a_node("j")}}, id="judge"),
        pytest.param({"kind": "gate", "auto_review": _on_a_node("r")}, id="auto_review"),
    ],
)
def test_a_child_addons_copy_reads_each_slot_in_its_namespace(client, tmp_path, written):
    """A task slot extends a task and `steps` a step, as the resolver reads
    them: one naming another kind of hidden ancestor entry (a node, a task) is
    copied as written, by its public name, not merged onto that entry."""
    team = {**TEAM, "library": {**TEAM["library"], "nodes": {"slotted": written}}}
    load_tree(client, tmp_path, dept=SLOT_DEPT, team=team)
    op = {"op": "copy_component", "ref": "nodes.team-payments:slotted", "name": "mine"}
    answer = client.post("/api/drafts/library/library/ops", json={"ops": [op]})
    assert answer.status_code == 200, answer.text
    text = client.get("/api/drafts/library/library").json()["files"]["library.yaml"]

    assert yaml.safe_load(text)["nodes"]["mine"] == written
    assert "^" not in text


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
