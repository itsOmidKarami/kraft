"""The `policy` draft area's ops and its resolve (W13 F): what each value
resolves to and from where, the loop precedence, the two dollar caps, the
live-loop-key check and the ops' written YAML."""

from __future__ import annotations

import pytest
import yaml
from support.harness import v1_fix_loop_node

URL = "/api/drafts/policy/policy"
MEASURE = {"id": "m", "kind": "agent", "harness": "fake", "prompt": "Check it."}


def _chain(chain_id: str, node_id: str, *, loop=None, node=None, chain=None) -> dict:
    n = v1_fix_loop_node(node_id, MEASURE, judge=False)
    if loop is not None:
        n["fix_loop"]["max_attempts"] = loop
    if node is not None:
        n["policy"] = node
    return {"id": chain_id, "nodes": [n], **({"policy": chain} if chain else {})}


CHAINS = [
    _chain("c-loop", "loopn", loop=2, node={"max_attempts": 4}, chain={"max_attempts": 5}),
    _chain(
        "c-node",
        "noden",
        node={"max_attempts": 4, "timeout_minutes": 10},
        chain={"max_attempts": 5},
    ),
    _chain("c-chain", "chainn", chain={"max_attempts": 5, "timeout_minutes": 20}),
    _chain("c-plain", "plainn"),
]
KEYS = {"c-loop": "loopn", "c-node": "noden", "c-chain": "chainn", "c-plain": "plainn"}


def _write_chains(templates_dir):
    for body in CHAINS:
        (templates_dir / "chains" / f"{body['id']}.yaml").write_text(yaml.safe_dump(body))


pytestmark = pytest.mark.api_client(default_setup=False, edit_templates=_write_chains)


def ops(client, *batch, query=""):
    return client.post(f"{URL}/ops{query}", json={"ops": list(batch)})


def resolved(client, *batch):
    r = ops(client, *batch)
    assert r.status_code == 200, r.text
    return r.json()["result"]


def set_value(scope, key, value):
    return {"op": "set_value", "scope": scope, "key": key, "value": value}


def live(body, node):
    return next(x for x in body["resolved"]["loops"]["live"] if x["node"] == node)


def written(client):
    return yaml.safe_load(client.get(URL).json()["files"]["policy.yaml"])


# ── loop precedence ──


@pytest.mark.parametrize(
    ("node", "attempts", "source"),
    [("loopn", 2, "loop"), ("noden", 4, "node"), ("chainn", 5, "chain")],
    ids=["loop-own", "node-policy", "chain-policy"],
)
def test_the_narrowest_layer_that_sets_attempts_wins(client, node, attempts, source):
    # Each chain also has the layers below it, so this is the winning and the losing.
    got = live(resolved(client), node)["attempts"]
    assert (got["value"], got["source"]) == (attempts, source)


def test_defaults_beat_loops_and_loops_beat_default(client):
    assert live(resolved(client), "plainn")["attempts"] == {"value": 3, "source": "default"}

    body = resolved(client, {"op": "set_loop", "key": "plainn.fix_loop", "max_attempts": 9})
    assert live(body, "plainn")["attempts"] == {"value": 9, "source": "loops"}

    body = resolved(client, set_value("limits", "defaults.max_attempts", 6))
    assert live(body, "plainn")["attempts"] == {"value": 6, "source": "defaults"}
    # A chain's own layer still beats `defaults:`.
    assert live(body, "chainn")["attempts"]["source"] == "chain"


def test_wall_clock_has_no_loop_layer(client):
    body = resolved(client)
    assert live(body, "noden")["wall_clock_s"] == {"value": 600, "source": "node"}
    assert live(body, "chainn")["wall_clock_s"] == {"value": 1200, "source": "chain"}
    # `loopn` sets no minutes anywhere: its own attempts are not a wall clock.
    assert live(body, "loopn")["wall_clock_s"] == {"value": 3600, "source": "default"}


# ── the dollar caps ──


def test_two_dollar_caps_resolve_to_the_lower_and_name_it(client):
    body = resolved(
        client,
        set_value("limits", "budget.work_item_usd", 10),
        set_value("limits", "maxima.work_item.budget_usd", 4),
    )
    cap = body["resolved"]["limits"]["work_item_usd"]
    assert (cap["value"], cap["binding"]) == (4, {"key": "maxima.work_item.budget_usd", "value": 4})

    body = resolved(client, set_value("limits", "maxima.work_item.budget_usd", None))
    cap = body["resolved"]["limits"]["work_item_usd"]
    assert (cap["value"], cap["binding"]["key"]) == (10.0, "budget.work_item_usd")


def test_an_unset_maximum_is_null_not_a_number(client):
    body = resolved(client, set_value("limits", "maxima.tasks.time_cap_minutes", 30))
    caps = body["resolved"]["limits"]["caps"]
    assert caps["tasks"]["time_cap_minutes"]["maximum"] == {"value": 30, "source": "tasks"}
    assert caps["steps"]["time_cap_minutes"]["maximum"] == {"value": None, "source": None}
    assert body["resolved"]["limits"]["max_attempts"]["maximum"]["value"] is None


def test_a_broader_levels_maximum_names_its_level_as_the_source(client):
    body = resolved(client, set_value("limits", "maxima.work_item.token_budget", 1000))
    assert body["resolved"]["limits"]["caps"]["steps"]["token_budget"]["maximum"] == {
        "value": 1000,
        "source": "work_item",
    }


# ── problems ──


def test_a_loops_key_that_names_no_loop_is_a_problem(client):
    body = resolved(client, {"op": "set_loop", "key": "typo.fix_loop", "max_attempts": 2})
    bad = [p for p in body["problems"] if p["scope"] == "loops"]
    assert [p["path"] for p in bad] == ["loops.typo.fix_loop"]
    entry = next(e for e in body["resolved"]["loops"]["entries"] if e["key"] == "typo.fix_loop")
    assert entry["live"] is False


def test_a_maximum_under_a_chains_value_is_a_problem_naming_the_chain(client):
    body = resolved(client, set_value("limits", "maxima.max_attempts", 3))
    chains = {p["chain"] for p in body["problems"] if p["scope"] == "limits"}
    assert {"c-loop", "c-node", "c-chain"} <= chains
    assert client.post(f"{URL}/publish").status_code == 422


def test_a_config_problem_carries_its_group_and_cap_level(client):
    # `defaults` over `maxima` at a level: the loader's own refusal.
    body = resolved(
        client,
        set_value("limits", "maxima.tasks.budget_usd", 1),
        set_value("limits", "defaults.tasks.budget_usd", 2),
    )
    p = next(p for p in body["problems"] if "budget_usd" in p["message"])
    assert (p["scope"], p["level"]) == ("limits", "tasks")


# ── ops ──


def test_set_value_writes_the_fragment_and_null_prunes_it(client):
    ops(client, set_value("limits", "maxima.tasks.budget_usd", 5))
    assert written(client)["maxima"] == {"tasks": {"budget_usd": 5}}

    ops(client, set_value("limits", "maxima.tasks.budget_usd", None))
    assert "maxima" not in written(client)


def test_set_value_refuses_a_key_outside_its_scope(client):
    assert ops(client, set_value("retries", "default.attempts", 1)).status_code == 422
    assert ops(client, set_value("harnesses", "maxima.allowed_harnesses", [])).status_code == 422


def test_set_loop_and_remove_loop_write_the_entry(client):
    ops(client, {"op": "set_loop", "key": "plainn.fix_loop", "max_attempts": 4})
    assert written(client)["loops"] == {"plainn.fix_loop": {"attempts": 4, "wall_clock_s": 3600}}

    ops(client, {"op": "set_loop", "key": "plainn.fix_loop", "wall_clock_s": 60})
    assert written(client)["loops"]["plainn.fix_loop"] == {"attempts": 4, "wall_clock_s": 60}

    ops(client, {"op": "remove_loop", "key": "plainn.fix_loop"})
    assert written(client)["loops"] == {}
    assert ops(client, {"op": "remove_loop", "key": "plainn.fix_loop"}).status_code == 422
