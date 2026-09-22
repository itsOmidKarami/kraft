"""UI v2 · 04 — per-item node overrides, reset to template, per-item budget
cap, raise-and-continue, and intake `skip_nodes`/`budget_usd`/`node_overrides`.
"""

from __future__ import annotations

import os
import sqlite3
import uuid
from pathlib import Path

import pytest
import yaml
from support.harness import isolated_bd, v1_seeded_chain, write_harness_profiles

from kraft import events, executor, policy, store


def _mark_started(wid: str, node_id: str) -> None:
    """Write the `node_started` event `store.chain.node_started` looks for,
    without walking the chain for real."""
    conn = sqlite3.connect(Path(os.environ["KRAFT_RUN_DIR"]) / "orchestrator.db")
    try:
        conn.execute("UPDATE work_items SET current_node_id = ? WHERE id = ?", (node_id, wid))
        events.append(conn, wid, "node_started", {"node_id": node_id})
        conn.commit()
    finally:
        conn.close()


def _paused_item(client, repo) -> str:
    """A `default`-chain item filed paused (`autostart: False`); its id."""
    return client.post(
        "/api/work-items",
        json={"title": "t", "repo": str(repo), "chain_template": "default", "autostart": False},
    ).json()["id"]


# --- point 1: per-node overrides, locked once a node has started -----------


def test_patch_sets_a_node_override_and_the_detail_reports_it(client, repo):
    """V1: `auto_escalate` can only confirm or suppress a gate's own declared
    reviewer, and the shipped `default` chain declares none, so the override
    this pins the mechanics with is a `model` -- any node, no declaration
    needed. The refusal itself is pinned in tests/executor/test_gates.py."""
    wid = _paused_item(client, repo)

    r = client.patch(
        f"/api/work-items/{wid}",
        json={"node_overrides": {"plan": {"model": "opus"}}},
    )
    assert r.status_code == 200, r.text

    detail = client.get(f"/api/work-items/{wid}").json()
    assert detail["node_overrides"] == {"plan": {"model": "opus"}}
    assert detail["node_overrides_count"] == 1
    plan_node = next(n for n in detail["effective_chain"]["nodes"] if n["id"] == "plan")
    assert plan_node["model"] == "opus"
    # chain_definition itself is untouched -- the override is a layer, not a mutation
    plan_raw = next(n for n in detail["chain_definition"]["nodes"] if n["id"] == "plan")
    assert plan_raw.get("model") != "opus"


def test_patch_node_overrides_merges_per_node_not_whole_object(client, repo):
    wid = _paused_item(client, repo)
    first = client.patch(
        f"/api/work-items/{wid}", json={"node_overrides": {"plan": {"model": "opus"}}}
    )
    assert first.status_code == 200, first.text
    r = client.patch(
        f"/api/work-items/{wid}",
        json={"node_overrides": {"implementation": {"effort": "high"}}},
    )
    assert r.status_code == 200, r.text
    overrides = client.get(f"/api/work-items/{wid}").json()["node_overrides"]
    assert overrides == {
        "plan": {"model": "opus"},
        "implementation": {"effort": "high"},
    }


@pytest.mark.parametrize(
    ("overrides", "names"),
    [
        ({"nope": {"auto_escalate": True}}, None),
        ({"plan": {"bogus_field": "x"}}, None),
        ({"implementation": {"effort": "turbo"}}, "effort"),
        ({"implementation": {"auto_escalate_stuck": "yes"}}, None),
        ({"implementation": {"auto_escalate_delay_s": -1}}, None),
        ({"implementation": {"attempts": 0}}, None),
        ({"implementation": {"wall_clock_s": -1}}, None),
    ],
    ids=[
        "unknown-node",
        "unsupported-field",
        "bad-effort",
        "non-bool-auto-escalate-stuck",
        "negative-auto-escalate-delay",
        "non-positive-attempts",
        "non-positive-wall-clock",
    ],
)
def test_patch_rejects_a_bad_node_override(client, repo, overrides, names):
    wid = _paused_item(client, repo)
    r = client.patch(f"/api/work-items/{wid}", json={"node_overrides": overrides})
    assert r.status_code == 422, r.text
    if names:
        assert names in r.json()["detail"]


def test_patch_sets_a_node_model_override(client, repo):
    """Kraft-df4tc point 2: model/escalate_model/effort join the per-node
    override fields, reusing the same field-level checks agent_overrides has."""
    wid = _paused_item(client, repo)
    r = client.patch(
        f"/api/work-items/{wid}",
        json={"node_overrides": {"implementation": {"model": "opus", "effort": "high"}}},
    )
    assert r.status_code == 200, r.text
    row = client.get(f"/api/work-items/{wid}").json()
    assert row["node_overrides"]["implementation"] == {"model": "opus", "effort": "high"}


def test_patch_sets_a_node_extra_prompt_and_409s_once_the_node_started(client, repo):
    """Kraft-a7ers: `extra_prompt` is one more per-node field, behind the same
    lock as the rest."""
    wid = _paused_item(client, repo)
    fields = {"model": "opus", "effort": "high", "extra_prompt": "Mind the migration order."}
    r = client.patch(f"/api/work-items/{wid}", json={"node_overrides": {"implementation": fields}})
    assert r.status_code == 200, r.text
    assert client.get(f"/api/work-items/{wid}").json()["node_overrides"]["implementation"] == fields

    _mark_started(wid, "spec")
    r = client.patch(
        f"/api/work-items/{wid}", json={"node_overrides": {"spec": {"extra_prompt": "late"}}}
    )
    assert r.status_code == 409, r.text


@pytest.mark.parametrize(
    ("fields", "refused"),
    [
        ({"effort": "max"}, "'effort'"),
        ({"model": "opus"}, "'model'"),
        ({"escalate_model": "opus"}, "'escalate_model'"),
        ({"model": "gpt-5", "escalate_model": "gpt-5-pro", "effort": "high"}, None),
    ],
    ids=["effort", "model", "escalate_model", "accepted"],
)
def test_a_node_override_the_node_harness_refuses_is_refused_at_both_doors(
    client, repo, templates_dir, fields, refused
):
    """Kraft-a7ers: a node's model/effort is held to the harness each of its
    agent tasks launches on, at the override rather than at launch. The
    `claude` profile the default chain's tasks name is put on the bundled
    `codex` harness, whose `values:` refuse `max` and a non-OpenAI model."""
    wid = _paused_item(client, repo)
    write_harness_profiles(templates_dir, {"claude": {"provider": "codex"}})

    patched = client.patch(
        f"/api/work-items/{wid}", json={"node_overrides": {"implementation": fields}}
    )
    filed = client.post(
        "/api/work-items",
        json={
            "title": "t",
            "repo": str(repo),
            "autostart": False,
            "node_overrides": {"implementation": fields},
        },
    )
    for r in (patched, filed):
        if refused is None:
            assert r.status_code in (200, 201), r.text
        else:
            assert r.status_code == 422, r.text
            assert refused in r.json()["detail"] and "codex" in r.json()["detail"]


@pytest.mark.parametrize(
    "fields",
    [
        {"auto_escalate_stuck": False},
        {"auto_escalate_delay_s": 120},
        {"attempts": 2, "wall_clock_s": 600},
    ],
    ids=["auto-escalate-stuck", "auto-escalate-delay", "attempts-and-wall-clock"],
)
def test_patch_sets_a_node_override_the_effective_chain_reads(client, repo, fields):
    wid = _paused_item(client, repo)
    r = client.patch(f"/api/work-items/{wid}", json={"node_overrides": {"implementation": fields}})
    assert r.status_code == 200, r.text

    detail = client.get(f"/api/work-items/{wid}").json()
    node = next(n for n in detail["effective_chain"]["nodes"] if n["id"] == "implementation")
    assert {k: node[k] for k in fields} == fields


def test_patch_node_overrides_409s_on_a_node_that_has_started(client, repo):
    wid = _paused_item(client, repo)
    _mark_started(wid, "spec")
    r = client.patch(f"/api/work-items/{wid}", json={"node_overrides": {"spec": {"model": "opus"}}})
    assert r.status_code == 409, r.text
    assert "locked" in r.json()["detail"]
    # a node that has NOT started is still free to override
    r2 = client.patch(
        f"/api/work-items/{wid}", json={"node_overrides": {"plan": {"model": "opus"}}}
    )
    assert r2.status_code == 200, r2.text


# --- point 2: reset to template ---------------------------------------------


def test_patch_reset_to_template_clears_overrides_but_keeps_attachment_trim(client, repo):
    """An override layer, not a re-materialize (this MR's design): reset is
    just clearing `node_overrides`, so the attachment-trimmed
    `chain_definition` -- untouched by overrides all along -- needs no
    special-casing to come back intact."""
    spec = repo / ".engineering" / "specs" / "s.md"
    spec.parent.mkdir(parents=True)
    spec.write_text("# spec\n")
    wid = client.post(
        "/api/work-items",
        json={
            "title": "t",
            "repo": str(repo),
            "chain_template": "default",
            "attachments": [{"kind": "spec", "path": ".engineering/specs/s.md"}],
            "cwd": str(repo),
            "autostart": False,
        },
    ).json()["id"]
    assert "spec" not in [
        n["id"] for n in client.get(f"/api/work-items/{wid}").json()["chain_definition"]["nodes"]
    ]

    client.patch(
        f"/api/work-items/{wid}", json={"node_overrides": {"plan": {"auto_escalate": True}}}
    )
    r = client.patch(f"/api/work-items/{wid}", json={"node_overrides": {}})
    assert r.status_code == 200, r.text

    detail = client.get(f"/api/work-items/{wid}").json()
    assert detail["node_overrides"] == {}
    assert detail["node_overrides_count"] == 0
    assert "spec" not in [n["id"] for n in detail["chain_definition"]["nodes"]]


def test_patch_reset_to_template_409s_once_the_item_has_started(client, repo):
    wid = _paused_item(client, repo)
    client.patch(
        f"/api/work-items/{wid}", json={"node_overrides": {"plan": {"auto_escalate": True}}}
    )
    _mark_started(wid, "spec")
    r = client.patch(f"/api/work-items/{wid}", json={"node_overrides": {}})
    assert r.status_code == 409, r.text


# --- point 6: intake skip_nodes / node_overrides / budget_usd --------------


def test_intake_skip_nodes_removes_them_from_the_materialized_chain(client, repo):
    r = client.post(
        "/api/work-items",
        json={
            "title": "t",
            "repo": str(repo),
            "chain_template": "default",
            "skip_nodes": ["spec"],
            "autostart": False,
        },
    )
    assert r.status_code == 201, r.text
    wid = r.json()["id"]
    node_ids = [
        n["id"] for n in client.get(f"/api/work-items/{wid}").json()["chain_definition"]["nodes"]
    ]
    assert "spec" not in node_ids
    assert "plan" in node_ids  # a gated node may be skipped without dragging its neighbors


def test_intake_skip_nodes_rejects_an_unknown_node(client, repo):
    r = client.post(
        "/api/work-items",
        json={
            "title": "t",
            "repo": str(repo),
            "chain_template": "default",
            "skip_nodes": ["nope"],
        },
    )
    assert r.status_code == 422


def test_intake_skip_nodes_rejects_a_node_a_kept_node_bounces_to(client, repo):
    """default.yaml's pre_mr_rebase has rebase_bounce_to: verify. Skipping
    verify while keeping pre_mr_rebase would leave walk.py's bounce-target
    lookup with nothing to find -- a StopIteration crash mid-run -- so intake
    must reject the combination instead of accepting it."""
    r = client.post(
        "/api/work-items",
        json={
            "title": "t",
            "repo": str(repo),
            "chain_template": "default",
            "skip_nodes": ["verify"],
        },
    )
    assert r.status_code == 422
    assert "verify" in r.json()["detail"]


def test_intake_skip_nodes_rejects_emptying_the_whole_chain(client, repo):
    """Every node skipped at once (code-review): `materialize` would hand
    `intake` an empty chain, and `create_work_item`/`executor.run_once` both
    index `nodes[0]` unguarded -- after the bead is already filed and the run
    already spawned. Reject at intake instead."""
    # The V1 chain the intake door resolves `default` to, not the legacy
    # template list: the skip is validated against the resolved chain.
    all_ids = [n.id for n in client.app.state.library.resolve_chain("default").nodes]
    r = client.post(
        "/api/work-items",
        json={
            "title": "t",
            "repo": str(repo),
            "chain_template": "default",
            "skip_nodes": all_ids,
        },
    )
    assert r.status_code == 422, r.text
    assert "empty" in r.json()["detail"] or "no nodes" in r.json()["detail"]


def test_intake_node_overrides_and_budget_round_trip(client, repo):
    r = client.post(
        "/api/work-items",
        json={
            "title": "t",
            "repo": str(repo),
            "chain_template": "default",
            "node_overrides": {"plan": {"auto_escalate_stuck": False}},
            "budget_usd": 7.5,
            "autostart": False,
        },
    )
    assert r.status_code == 201, r.text
    wid = r.json()["id"]
    detail = client.get(f"/api/work-items/{wid}").json()
    assert detail["node_overrides"] == {"plan": {"auto_escalate_stuck": False}}
    assert detail["budget_cap"] == {"cap_usd": 7.5, "source": "item", "spent_usd": 0.0}


def test_intake_node_overrides_accepts_attempts_and_wall_clock_s(client, repo):
    r = client.post(
        "/api/work-items",
        json={
            "title": "t",
            "repo": str(repo),
            "chain_template": "default",
            "node_overrides": {"implementation": {"attempts": 2, "wall_clock_s": 600}},
            "autostart": False,
        },
    )
    assert r.status_code == 201, r.text
    wid = r.json()["id"]
    detail = client.get(f"/api/work-items/{wid}").json()
    assert detail["node_overrides"] == {"implementation": {"attempts": 2, "wall_clock_s": 600}}


def test_intake_budget_usd_null_is_an_explicit_no_cap(client, repo):
    wid = client.post(
        "/api/work-items",
        json={"title": "t", "repo": str(repo), "budget_usd": None, "autostart": False},
    ).json()["id"]
    detail = client.get(f"/api/work-items/{wid}").json()
    assert detail["budget_cap"] == {"cap_usd": None, "source": "item", "spent_usd": 0.0}


def test_no_budget_usd_at_intake_defers_to_the_policy_default(client, repo):
    wid = client.post(
        "/api/work-items", json={"title": "t", "repo": str(repo), "autostart": False}
    ).json()["id"]
    detail = client.get(f"/api/work-items/{wid}").json()
    assert detail["budget_cap"]["source"] == "policy"


# --- point 4/5: budget precedence and raise-and-continue, at the store layer


_FAKE_AGENT = Path(__file__).parents[0] / "support" / "fake_agent.py"


def _budget_template(tmp_path):
    return v1_seeded_chain(
        tmp_path / "templates",
        [
            {
                "id": "work",
                "kind": "exec",
                "tasks": [
                    {"id": "implement", "kind": "agent", "harness": "fake", "prompt": "Do it."}
                ],
            }
        ],
        agent_command=f"{__import__('sys').executable} {_FAKE_AGENT}",
    )


def _policy(*, work_item_usd=None, daily_usd=None) -> policy.Policy:
    return policy.Policy(
        loops={},
        default=policy.Cap(attempts=3, wall_clock_s=3600),
        budget=policy.Budget(work_item_usd=work_item_usd, daily_usd=daily_usd),
        # Kraft-lpdd: this suite is about per-item budget overrides, not the
        # unrelated auto-escalate trigger a budget breach would otherwise
        # also fire.
        auto_escalate_stuck=False,
    )


async def _spend(database, wid: str, usd: float) -> None:
    sid = uuid.uuid4().hex
    await database.write(
        lambda c: store.create_session(
            c,
            id=sid,
            work_item_id=wid,
            node_id="prior",
            hook_point="on.implementation.start",
            log_path="/tmp/l",
            result_path="/tmp/r",
        )
    )
    await database.write(
        lambda c: c.execute(
            "UPDATE worker_sessions SET cost_usd = ?, status = 'done' WHERE id = ?", (usd, sid)
        )
    )


async def test_an_item_budget_overrides_a_looser_policy_default(
    tmp_path, monkeypatch, database, run_dirs, repo
):
    """Point 4: item > policy. The policy allows $20; the item capped itself at $5."""
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "fix")
    tracker = isolated_bd(tmp_path)

    wid = await executor.intake(
        database,
        run_dirs,
        title="t",
        repo=str(repo),
        chain=_budget_template(tmp_path),
        bd_cwd=str(tracker),
        budget_set=True,
        budget_usd=5.0,
    )
    await _spend(database, wid, 6.0)
    result = await executor.run(
        database,
        run_dirs,
        work_item_id=wid,
        bd_cwd=str(tracker),
        policy=_policy(work_item_usd=20.0),
    )
    assert result == "needs_human"
    evts = database.read(lambda c: events.read_after(c, 0, wid))
    payload = next(e["payload"] for e in reversed(evts) if e["type"] == "work_item_needs_human")
    assert payload["budget"] == {"scope": "work_item", "spent_usd": 6.0, "cap_usd": 5.0}


async def test_an_item_explicit_no_cap_overrides_a_capped_policy(
    tmp_path, monkeypatch, database, run_dirs, repo
):
    """Point 4: `budget_set=True, budget_usd=None` beats a policy cap, but the
    daily cap -- policy-only -- still applies underneath it."""
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "fix")
    tracker = isolated_bd(tmp_path)

    wid = await executor.intake(
        database,
        run_dirs,
        title="t",
        repo=str(repo),
        chain=_budget_template(tmp_path),
        bd_cwd=str(tracker),
        budget_set=True,
        budget_usd=None,
    )
    await _spend(database, wid, 1000.0)
    await executor.run(
        database,
        run_dirs,
        work_item_id=wid,
        bd_cwd=str(tracker),
        policy=_policy(work_item_usd=5.0),
    )
    evts = database.read(lambda c: events.read_after(c, 0, wid))
    assert not any(e["type"] == "work_item_needs_human" for e in evts)

    # a fresh item, same no-cap override, still stops on the daily cap
    wid2 = await executor.intake(
        database,
        run_dirs,
        title="t2",
        repo=str(repo),
        chain=_budget_template(tmp_path),
        bd_cwd=str(tracker),
        budget_set=True,
        budget_usd=None,
    )
    result = await executor.run(
        database,
        run_dirs,
        work_item_id=wid2,
        bd_cwd=str(tracker),
        policy=_policy(daily_usd=100.0),
    )
    assert result == "needs_human"


# --- point 5: raise budget and continue, via the API ------------------------


def test_raise_budget_endpoint_continues_a_budget_stopped_item(monkeypatch, client, repo):
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "fix")
    wid = client.post(
        "/api/work-items",
        json={
            "title": "t",
            "repo": str(repo),
            "chain_template": "quick-task",
            "autostart": False,
        },
    ).json()["id"]
    conn = sqlite3.connect(Path(os.environ["KRAFT_RUN_DIR"]) / "orchestrator.db")
    conn.execute(
        "UPDATE work_items SET status = 'needs_human', current_node_id = 'implementation' "
        "WHERE id = ?",
        (wid,),
    )
    conn.commit()
    conn.close()

    r = client.post(f"/api/work-items/{wid}/budget/raise", json={"budget_usd": 50.0})
    assert r.status_code == 200, r.text

    detail = client.get(f"/api/work-items/{wid}").json()
    assert detail["budget_cap"] == {"cap_usd": 50.0, "source": "item", "spent_usd": 0.0}
    evts = client.get(f"/api/work-items/{wid}/events").json()
    assert any(e["type"] == "budget_raised" and e["payload"]["budget_usd"] == 50.0 for e in evts)


def test_raise_budget_endpoint_409s_when_the_item_is_not_stopped(client, repo):
    wid = client.post(
        "/api/work-items",
        json={
            "title": "t",
            "repo": str(repo),
            "chain_template": "quick-task",
            "autostart": False,
        },
    ).json()["id"]
    r = client.post(f"/api/work-items/{wid}/budget/raise", json={"budget_usd": 50.0})
    assert r.status_code == 409


def test_patch_budget_usd_sets_and_clears_the_cap(client, repo):
    wid = client.post(
        "/api/work-items",
        json={
            "title": "t",
            "repo": str(repo),
            "chain_template": "quick-task",
            "autostart": False,
        },
    ).json()["id"]
    assert client.get(f"/api/work-items/{wid}").json()["budget_cap"]["source"] == "policy"

    r = client.patch(f"/api/work-items/{wid}", json={"budget_usd": 12.0})
    assert r.status_code == 200, r.text
    detail = client.get(f"/api/work-items/{wid}").json()
    assert detail["budget_cap"] == {"cap_usd": 12.0, "source": "item", "spent_usd": 0.0}

    # explicit null: an item-set "no cap", distinct from "never customized"
    r2 = client.patch(f"/api/work-items/{wid}", json={"budget_usd": None})
    assert r2.status_code == 200, r2.text
    detail2 = client.get(f"/api/work-items/{wid}").json()
    assert detail2["budget_cap"] == {"cap_usd": None, "source": "item", "spent_usd": 0.0}


def _reviewed_chain(tdir):
    """A chain whose `approve` gate declares a reviewer and whose `hold` gate
    declares none."""
    work = {
        "id": "work",
        "kind": "exec",
        "tasks": [{"id": "t", "kind": "subprocess", "command": "true"}],
    }
    reviewer = {"id": "r", "kind": "agent", "harness": "codex", "prompt": "review"}
    (tdir / "chains" / "reviewed.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "reviewed",
                "nodes": [
                    work,
                    {"id": "approve", "kind": "gate", "auto_review": reviewer},
                    {"id": "hold", "kind": "gate"},
                ],
            }
        )
    )


@pytest.mark.api_client(edit_templates=_reviewed_chain)
def test_intake_refuses_auto_escalate_on_a_gate_with_no_reviewer(client, repo):
    """Review E #3: intake accepted `auto_escalate: true` on a gate declaring
    no `auto_review`, returned 201, and nothing ever reviewed it. The same 422
    `PATCH` gives, from the same check; arming a declared reviewer, or
    suppressing one nobody declared, is still accepted."""

    def file(overrides):
        return client.post(
            "/api/work-items",
            json={
                "title": "t",
                "repo": str(repo),
                "chain_template": "reviewed",
                "autostart": False,
                "node_overrides": overrides,
            },
        )

    r = file({"hold": {"auto_escalate": True}})
    assert r.status_code == 422, r.text
    assert "node 'hold' declares no 'auto_review' task" in r.json()["detail"]
    assert file({"approve": {"auto_escalate": True}}).status_code == 201
    assert file({"hold": {"auto_escalate": False}}).status_code == 201
