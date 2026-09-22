"""Work item creation and patch: POST /work-items (including attachment intake
and title/description/chain-template/agent-override validation) and the
worktree-head fields on GET."""

from __future__ import annotations

import dataclasses
import json
import os
import sqlite3
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError
from support.api import _await_gate, _poll_events, _post_default, _set_status
from support.harness import make_repo, make_repo_with_engineering

from kraft.adapters import beads
from kraft.api.routes.work_items import NewWorkItem


def _paused(client, repo, **body):
    """File a not-yet-started item (`autostart: False`); return its id."""
    r = client.post(
        "/api/work-items", json={"title": "t", "repo": str(repo), "autostart": False, **body}
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest.mark.parametrize("policy", ["nope", "skip"], ids=["unknown", "a-retired-v0-value"])
def test_new_work_item_schema_rejects_an_unknown_root_pointer_policy(policy):
    with pytest.raises(ValidationError):
        NewWorkItem.model_validate({"title": "x", "repo": "/r", "root_pointer_policy": policy})


def test_autostart_create_lands_paused_when_all_slots_are_busy(client, repo):
    """Kraft-m43g, Kraft-nxht: `create_work_item`'s autostart path used to hand
    `status="active"` straight to `intake`'s `INSERT` with no capacity check at
    all -- an autostart create always won a slot. It now loses this race the
    same non-error way an explicit `autostart: False` already does: paused,
    not rejected."""
    busy = _post_default(client, repo)
    _poll_events(client, busy, "gate_requested")
    _set_status(busy, "active")
    client.app.state.policy = dataclasses.replace(client.app.state.policy, max_concurrent=1)

    r = client.post(
        "/api/work-items",
        json={"title": "t", "repo": str(repo), "chain_template": "default"},
    )

    assert r.status_code == 201, r.text
    assert r.json()["status"] == "paused"
    wid = r.json()["id"]
    assert client.get(f"/api/work-items/{wid}").json()["status"] == "paused"
    assert client.get(f"/api/work-items/{busy}").json()["status"] == "active"


@pytest.mark.parametrize(
    ("route", "body", "detail"),
    [
        (
            "work-items",
            {"title": "x", "repo": "/tmp", "chain_template": "nope"},
            "no chain 'nope'",
        ),
        ("work-items", {"title": "x"}, None),
        ("work-items", {"title": "x", "repo": "/no/such/dir"}, "repo path does not exist"),
        (
            "triggers",
            {"title": "t", "repo": "REPO", "chain_template": "nope"},
            "no chain 'nope'",
        ),
        ("triggers", {"title": "t", "repo": "/no/such/dir"}, "repo path does not exist"),
    ],
    ids=[
        "an-unknown-template",
        "no-repo",
        "a-nonexistent-repo",
        "trigger-an-unknown-template",
        "trigger-a-nonexistent-repo",
    ],
)
def test_intake_refuses_a_bad_body_with_422(client, repo, route, body, detail):
    body = {k: str(repo) if v == "REPO" else v for k, v in body.items()}
    r = client.post(f"/api/{route}", json=body)
    assert r.status_code == 422
    if detail:
        assert detail in r.json()["detail"]


@pytest.mark.parametrize(
    ("route", "extra"),
    [("work-items", {"autostart": False}), ("triggers", {})],
    ids=["work-items", "triggers"],
)
def test_a_title_over_the_tracker_limit_is_refused_before_bd(client, repo, route, extra):
    """Half of Kraft-cy30 landed already: `beads.intake` raises bd's own stderr
    (Kraft-ibwj) and `executor.intake` catches every bd failure into a
    `bead_warning` rather than a 502 (Kraft-7gy). What is left is quieter and
    worse -- an over-long title now *succeeds*, and the item exists with no bead
    and a warning line the caller may never read. Refuse it at the door.

    The offending title is not echoed back: the length and the limit are the
    actionable part, and this `detail` is printed straight through by
    `kraft item create`.
    """
    long_title = "x" * (beads.MAX_TITLE + 1)
    r = client.post(f"/api/{route}", json={"title": long_title, "repo": str(repo), **extra})
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert str(beads.MAX_TITLE + 1) in detail
    assert str(beads.MAX_TITLE) in detail
    assert long_title not in detail

    assert client.get("/api/work-items").json()["items"] == []

    # the boundary itself is accepted
    ok = client.post(
        f"/api/{route}", json={"title": "x" * beads.MAX_TITLE, "repo": str(repo), **extra}
    )
    assert ok.status_code == 201, ok.text


def test_get_unknown_work_item_404(client):
    assert client.get("/api/work-items/does-not-exist").status_code == 404
    assert client.get("/api/worker-sessions/nope/log").status_code == 404


@pytest.mark.parametrize(
    ("body", "description"),
    [
        (
            {"description": "the brief the spec is written from"},
            "the brief the spec is written from",
        ),
        ({}, None),
    ],
    ids=["a-description", "no-description-is-null"],
)
def test_create_returns_the_description_in_both_payloads(client, repo, body, description):
    wid = _paused(client, repo, **body)
    assert client.get(f"/api/work-items/{wid}").json()["description"] == description
    listed = client.get("/api/work-items").json()["items"]
    assert [i["description"] for i in listed if i["id"] == wid] == [description]


def test_create_work_item_accepts_auto_gate(client, repo):
    wid = client.post(
        "/api/work-items",
        json={"title": "t", "repo": str(repo), "autostart": False, "auto_gate": False},
    ).json()["id"]
    assert client.get(f"/api/work-items/{wid}").json()["auto_gate"] == 0

    # On unless the caller opts out: the browser sends no `auto_gate` key.
    wid2 = client.post(
        "/api/work-items", json={"title": "t", "repo": str(repo), "autostart": False}
    ).json()["id"]
    assert client.get(f"/api/work-items/{wid2}").json()["auto_gate"] == 1


def test_patch_updates_the_description_and_records_an_event(client, repo):
    """The description feeds every agent prompt, so an edit has to be answerable
    from the timeline: `events` is the authoritative log."""
    wid = _paused(client, repo, description="first")
    before = client.get(f"/api/work-items/{wid}").json()["updated_at"]

    r = client.patch(f"/api/work-items/{wid}", json={"description": "second"})
    assert r.status_code == 200
    assert r.json()["description"] == "second"

    after = client.get(f"/api/work-items/{wid}").json()
    assert after["description"] == "second"
    assert after["updated_at"] >= before

    evs = client.get(f"/api/work-items/{wid}/events").json()
    edits = [e for e in evs if e["type"] == "work_item_description_edited"]
    assert [e["payload"]["description"] for e in edits] == ["second"]


def test_patch_404s_on_an_unknown_work_item(client):
    assert client.patch("/api/work-items/nope", json={"description": "x"}).status_code == 404


def test_patch_can_clear_the_description(client, repo):
    wid = _paused(client, repo, description="first")
    assert client.patch(f"/api/work-items/{wid}", json={"description": ""}).status_code == 200
    assert client.get(f"/api/work-items/{wid}").json()["description"] is None


def test_patch_sets_the_title_without_clobbering_the_description(client, repo):
    """Absent means untouched, in both directions. A screen that patches only
    the title must not blank the brief, and vice versa."""
    wid = client.post(
        "/api/work-items",
        json={
            "title": "typed in a hurry",
            "description": "the brief",
            "repo": str(repo),
            "autostart": False,
        },
    ).json()["id"]

    r = client.patch(f"/api/work-items/{wid}", json={"title": "a better label"})
    assert r.status_code == 200, r.text
    assert r.json() == {"id": wid, "title": "a better label"}
    after = client.get(f"/api/work-items/{wid}").json()
    assert after["title"] == "a better label"
    assert after["description"] == "the brief"

    r = client.patch(f"/api/work-items/{wid}", json={"description": "a better brief"})
    assert r.status_code == 200, r.text
    after = client.get(f"/api/work-items/{wid}").json()
    assert after["title"] == "a better label"
    assert after["description"] == "a better brief"

    evs = client.get(f"/api/work-items/{wid}/events").json()
    assert [e["payload"]["title"] for e in evs if e["type"] == "work_item_title_edited"] == [
        "a better label"
    ]


def test_patch_refuses_an_empty_body_and_a_blank_title(client, repo):
    """`{}` is a caller bug, not a no-op to absorb; a blank title would leave the
    board with an unlabelled row and nothing to search on."""
    wid = _paused(client, repo)

    empty = client.patch(f"/api/work-items/{wid}", json={})
    assert empty.status_code == 422
    assert "nothing to patch" in empty.json()["detail"]

    blank = client.patch(f"/api/work-items/{wid}", json={"title": "   "})
    assert blank.status_code == 422
    assert "title cannot be empty" in blank.json()["detail"]

    assert client.get(f"/api/work-items/{wid}").json()["title"] == "t"


def test_patch_switches_chain_template_before_the_chain_starts(client, repo):
    """Kraft-gwn6: a not-yet-started item can switch onto a different
    template's own materialized chain."""
    wid = _paused(client, repo, chain_template="quick-task")
    before = client.get(f"/api/work-items/{wid}").json()
    # V1 quick-task: `env_setup` is implicit preparation, not a node.
    assert [n["id"] for n in before["chain_definition"]["nodes"]] == ["implementation", "verify"]

    r = client.patch(f"/api/work-items/{wid}", json={"chain_template": "default"})
    assert r.status_code == 200, r.text
    assert r.json() == {"id": wid, "chain_template": "default"}

    after = client.get(f"/api/work-items/{wid}").json()
    assert after["chain_template"] == "default"
    # The V1 `default` chain: every gate is a node of its own.
    assert [n["id"] for n in after["chain_definition"]["nodes"]] == [
        "spec",
        "spec_approval",
        "plan",
        "plan_approval",
        "implementation",
        "verification",
        "work_brief",
        "local_review",
        "describe_merge_request",
        "draft_merge_request",
        "merge_request_feedback",
        "work_item_summary",
        "chain_review",
        "mark_ready",
        "external_approval",
        "merge",
        "post_merge_ci",
    ]

    evs = client.get(f"/api/work-items/{wid}/events").json()
    changed = [e for e in evs if e["type"] == "chain_template_changed"]
    assert [e["payload"] for e in changed] == [{"from": "quick-task", "to": "default"}]


def test_patch_refuses_chain_template_once_started(client, repo):
    wid = _paused(client, repo, chain_template="quick-task")
    db_path = Path(os.environ["KRAFT_RUN_DIR"]) / "orchestrator.db"
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE work_items SET current_node_id = 'env_setup' WHERE id = ?", (wid,))
    conn.commit()
    conn.close()

    r = client.patch(f"/api/work-items/{wid}", json={"chain_template": "default"})
    assert r.status_code == 409, r.text

    after = client.get(f"/api/work-items/{wid}").json()
    assert after["chain_template"] == "quick-task"


def test_patch_refuses_an_unknown_chain_template(client, repo):
    wid = _paused(client, repo)

    r = client.patch(f"/api/work-items/{wid}", json={"chain_template": "does-not-exist"})
    assert r.status_code == 404, r.text

    after = client.get(f"/api/work-items/{wid}").json()
    assert after["chain_template"] is None


def _custom_chain(tdir):
    """`custom`: spec, its approval gate, then verify."""
    (tdir / "chains" / "custom.yaml").write_text(
        "id: custom\n"
        "nodes:\n"
        "  - id: spec\n"
        "    kind: exec\n"
        "    tasks:\n"
        "      - { id: author, kind: agent, harness: fake, prompt: spec, produces: spec }\n"
        "  - { id: spec_approval, kind: gate, message: approve, artifact: spec }\n"
        "  - id: verify\n"
        "    kind: exec\n"
        "    tasks:\n"
        "      - { id: run, kind: agent, harness: fake, prompt: verify }\n"
    )


@pytest.mark.api_client(edit_templates=_custom_chain)
def test_patch_switching_chain_template_preserves_attachment_gate_trim(client, repo):
    """An item that attached a spec at intake has already trimmed
    spec_approval out of its chain (Kraft-dgh); switching template must not
    force it to reattach to get that trim back."""
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
    before = client.get(f"/api/work-items/{wid}").json()
    filed = [n["id"] for n in json.loads(before["materialized_chain"])["chain"]["nodes"]]
    assert "spec" not in filed and "spec_approval" not in filed

    r = client.patch(f"/api/work-items/{wid}", json={"chain_template": "custom"})
    assert r.status_code == 200, r.text

    after = client.get(f"/api/work-items/{wid}").json()
    assert [n["id"] for n in json.loads(after["materialized_chain"])["chain"]["nodes"]] == [
        "verify"
    ]


def test_patch_sets_agent_overrides_and_records_an_event(client, repo):
    wid = _paused(client, repo)

    r = client.patch(
        f"/api/work-items/{wid}",
        json={"agent_overrides": {"model": "opus", "effort": "high"}},
    )
    assert r.status_code == 200, r.text

    evs = client.get(f"/api/work-items/{wid}/events").json()
    changed = [e for e in evs if e["type"] == "agent_overrides_changed"]
    assert [e["payload"] for e in changed] == [{"overrides": {"model": "opus", "effort": "high"}}]


def test_patch_clears_agent_overrides_with_an_empty_object(client, repo):
    wid = _paused(client, repo)
    client.patch(f"/api/work-items/{wid}", json={"agent_overrides": {"model": "opus"}})

    r = client.patch(f"/api/work-items/{wid}", json={"agent_overrides": {}})
    assert r.status_code == 200, r.text

    row = client.get(f"/api/work-items/{wid}").json()
    assert row["agent_overrides"] is None


def test_patch_rejects_invalid_agent_overrides_with_422(client, repo):
    wid = _paused(client, repo)

    r = client.patch(f"/api/work-items/{wid}", json={"agent_overrides": {"effort": "turbo"}})
    assert r.status_code == 422, r.text


def test_patch_accepts_agent_overrides_on_a_started_or_paused_item(client, repo):
    """Unlike chain_template, a model/effort dial has no current_node_id
    restriction -- it is the door to make a stuck item cheaper before its
    next retry."""
    wid = _paused(client, repo)
    db_path = Path(os.environ["KRAFT_RUN_DIR"]) / "orchestrator.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "UPDATE work_items SET current_node_id = 'env_setup', status = 'paused' WHERE id = ?",
        (wid,),
    )
    conn.commit()
    conn.close()

    r = client.patch(f"/api/work-items/{wid}", json={"agent_overrides": {"model": "haiku"}})
    assert r.status_code == 200, r.text


def test_get_work_item_reports_the_worktree_head(client, repo, monkeypatch):
    """Kraft-lu2: the gate compares a measurement's sha against this. Without it
    the frontend has nothing to compare to."""
    from kraft import executor

    async def noop(*a, **kw):
        return "completed"

    monkeypatch.setattr(executor, "run", noop)
    wid = client.post("/api/work-items", json={"title": "x", "repo": str(repo)}).json()["id"]
    body = client.get(f"/api/work-items/{wid}").json()
    assert "head_sha" in body


def test_get_work_item_head_sha_is_none_before_the_worktree_exists(client, repo, monkeypatch):
    """A paused item has no worktree yet; `git_read` returns None rather than
    raising, and the field must carry that through instead of 500ing."""
    from kraft import executor

    async def noop(*a, **kw):
        return "completed"

    monkeypatch.setattr(executor, "run", noop)
    wid = client.post("/api/work-items", json={"title": "x", "repo": str(repo)}).json()["id"]
    body = client.get(f"/api/work-items/{wid}").json()
    assert body["head_sha"] is None


def _invalid_policy(tdir):
    (tdir / "policy.yaml").write_text("default: { attempts: 0, wall_clock_s: 1 }\n")


@pytest.mark.api_client(edit_templates=_invalid_policy)
def test_post_refused_when_policy_invalid(client, repo):
    r = client.post(
        "/api/work-items",
        json={"title": "x", "repo": str(repo), "chain_template": "default"},
    )
    assert r.status_code != 201
    assert "policy" in r.json()["detail"].lower()


def test_post_materializes_chain(client, repo):
    r = client.post(
        "/api/work-items",
        json={
            "title": "make the failing test pass",
            "repo": str(repo),
            "chain_template": "quick-task",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    # V1 quick-task: `env_setup` is implicit preparation, not a node.
    assert body["current_node_id"] == "implementation"
    assert [n["id"] for n in body["chain_definition"]["nodes"]] == [
        "implementation",
        "verify",
    ]
    assert body["bead_id"]


def _linked_worktree(repo, tmp_path, name="wt"):
    """A second working tree of `repo` — what an agent hands work over from."""
    worktree = tmp_path / name
    subprocess.run(
        ["git", "worktree", "add", "-q", str(worktree), "-b", name],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    return worktree


def test_a_single_repo_item_has_no_repos_panel(client, repo):
    wid = client.post(
        "/api/work-items",
        json={"repo": str(repo), "title": "solo", "chain_template": "quick-task"},
    ).json()["id"]
    assert client.get(f"/api/work-items/{wid}").json()["repos"] == []


def test_intake_with_a_plan_attachment_trims_the_chain_and_reports_it(client, tmp_path):
    repo = make_repo_with_engineering(tmp_path, {".engineering/plans/p.md": "# plan\n"})
    r = client.post(
        "/api/work-items",
        json={
            "title": "t",
            "repo": str(repo),
            "chain_template": "default",
            "attachments": [{"kind": "plan", "path": ".engineering/plans/p.md"}],
        },
    )
    assert r.status_code == 201, r.text
    wid = r.json()["id"]
    item = client.get(f"/api/work-items/{wid}").json()
    # Trimming a gate is irreversible, so intake copies the plan into
    # Kraft's own storage and reports that copy as `source` (Kraft-eqgn).
    stored_dir = client.app.state.run_dirs.attachments / wid
    expected = [{"kind": "plan", "path": ".engineering/plans/p.md"}]
    assert [{k: v for k, v in a.items() if k != "source"} for a in item["attachments"]] == expected
    assert Path(item["attachments"][0]["source"]).is_relative_to(stored_dir)
    # The gate whose `artifact` is the plan, and the node that would have
    # written it, are both gone from the frozen snapshot -- V1 declares the
    # trim on both ends instead of looking a kind up in a gate-name table.
    nodes = json.loads(item["materialized_chain"])["chain"]["nodes"]
    assert "plan_approval" not in [n["id"] for n in nodes]
    assert "plan" not in [n["id"] for n in nodes]
    listed = next(i for i in client.get("/api/work-items").json()["items"] if i["id"] == wid)
    assert [
        {k: v for k, v in a.items() if k != "source"} for a in listed["attachments"]
    ] == expected


def test_intake_with_a_plan_attachment_never_runs_the_plan_node(client, tmp_path):
    """The trimmed node must be absent from the run, not merely from the
    chain_definition the UI reads (see the _trims_the_chain_and_reports_it
    test above for that check). V1 trims the plan node and its gate together,
    so the node after the spec gate is `implementation`."""
    repo = make_repo_with_engineering(tmp_path, {".engineering/plans/p.md": "# plan\n"})
    wid = client.post(
        "/api/work-items",
        json={
            "title": "t",
            "repo": str(repo),
            "chain_template": "default",
            "attachments": [{"kind": "plan", "path": ".engineering/plans/p.md"}],
        },
    ).json()["id"]
    _await_gate(client, wid, "spec_approval")
    client.post(f"/api/work-items/{wid}/gates/spec_approval/approve")
    # spec, then spec_approval (a V1 gate is a node that starts too), then
    # whatever follows the gate.
    events = _poll_events(client, wid, "node_started", count=3)
    started = [e["payload"]["node_id"] for e in events if e["type"] == "node_started"]
    assert "plan" not in started
    assert started[2] == "implementation"


def _outside(repo, tmp_path):
    # Absolute and outside the repo, but real -- proves rejection is about
    # location, not existence (an absolute path to a missing file would 422
    # for the wrong reason).
    outside = tmp_path / "outside.md"
    outside.write_text("# outside\n")
    return [str(outside)]


def _symlink_out(repo, tmp_path):
    # Only the symlink target makes this path escape: the string itself is
    # repo-relative, so this fails only if .resolve() follows the symlink
    # before the is_relative_to check.
    escape = repo / ".engineering" / "plans" / "escape.md"
    escape.parent.mkdir(parents=True)
    escape.symlink_to(_outside(repo, tmp_path)[0])
    return [".engineering/plans/escape.md"]


def _twice(repo, tmp_path):
    (repo / ".engineering" / "plans").mkdir(parents=True)
    (repo / ".engineering" / "plans" / "p.md").write_text("# p\n")
    return [".engineering/plans/p.md"] * 2


@pytest.mark.parametrize(
    ("paths", "detail"),
    [
        (lambda repo, tmp_path: ["../outside.md"], "escapes"),
        (_outside, "escapes"),
        (_symlink_out, "escapes"),
        (lambda repo, tmp_path: [".engineering/plans/nope.md"], None),
        (_twice, None),
    ],
    ids=["traversing", "absolute", "a-symlink-that-escapes", "missing", "a-duplicate-kind"],
)
def test_intake_rejects_an_attachment(client, repo, tmp_path, paths, detail):
    attachments = [{"kind": "plan", "path": path} for path in paths(repo, tmp_path)]
    r = client.post(
        "/api/work-items", json={"title": "t", "repo": str(repo), "attachments": attachments}
    )
    assert r.status_code == 422
    if detail:
        assert detail in r.text


def test_intake_accepts_an_uncommitted_attachment(client, repo):
    plan = repo / ".engineering" / "plans" / "p.md"
    plan.parent.mkdir(parents=True)
    plan.write_text("# uncommitted\n")
    r = client.post(
        "/api/work-items",
        json={
            "title": "t",
            "repo": str(repo),
            "attachments": [{"kind": "plan", "path": ".engineering/plans/p.md"}],
        },
    )
    assert r.status_code == 201, r.text


def test_intake_accepts_an_attachment_from_another_worktree_of_the_repo(client, repo, tmp_path):
    """Kraft-85wk. The document exists only in the working tree the caller is
    standing in — which for a Kraft worker is always true, because the
    registered repo is the main checkout by construction (config.probe_repo
    normalizes a worktree to it). Resolving under the repo alone 422s the exact
    handoff attachments exist for."""
    worktree = _linked_worktree(repo, tmp_path)
    spec = worktree / ".engineering" / "specs" / "s.md"
    spec.parent.mkdir(parents=True)
    spec.write_text("# written in the worktree\n")
    assert not (repo / ".engineering" / "specs" / "s.md").exists()
    r = client.post(
        "/api/work-items",
        json={
            "title": "t",
            "repo": str(repo),
            "autostart": False,
            "cwd": str(worktree),
            "attachments": [{"kind": "spec", "path": ".engineering/specs/s.md"}],
        },
    )
    assert r.status_code == 201, r.text
    wid = r.json()["id"]
    stored = client.get(f"/api/work-items/{wid}").json()["attachments"]
    # repo-relative path, unchanged in shape: it is what the prompt note,
    # the board badge and index/service._attachment_docs all read.
    assert stored[0]["kind"] == "spec"
    assert stored[0]["path"] == ".engineering/specs/s.md"
    # and `source` is Kraft's own copy, not the worktree original --
    # a trimmed gate must be self-backed (Kraft-eqgn).
    assert Path(stored[0]["source"]).is_relative_to(client.app.state.run_dirs.attachments / wid)
    assert Path(stored[0]["source"]).read_text() == spec.read_text()


def test_intake_without_a_cwd_still_scopes_attachments_to_the_repo(client, repo, tmp_path):
    """The browser sends no cwd, so its reachable set stays exactly the repo,
    as Kraft-7izl set it. Same file, same request, minus one field."""
    worktree = _linked_worktree(repo, tmp_path)
    spec = worktree / ".engineering" / "specs" / "s.md"
    spec.parent.mkdir(parents=True)
    spec.write_text("# written in the worktree\n")
    r = client.post(
        "/api/work-items",
        json={
            "title": "t",
            "repo": str(repo),
            "autostart": False,
            "attachments": [{"kind": "spec", "path": ".engineering/specs/s.md"}],
        },
    )
    assert r.status_code == 422
    assert "not found" in r.text


def test_intake_ignores_a_cwd_in_a_different_repo(client, repo, tmp_path):
    """`cwd` is not "read any file on this machine": a second root is added only
    when it is another working tree of the *same* repository. An unrelated repo
    holding a file at the same relative path proves the check is the shared
    `.git` and not the path string."""
    stranger = make_repo(tmp_path, name="stranger")
    spec = stranger / ".engineering" / "specs" / "s.md"
    spec.parent.mkdir(parents=True)
    spec.write_text("# not this repo's spec\n")
    r = client.post(
        "/api/work-items",
        json={
            "title": "t",
            "repo": str(repo),
            "autostart": False,
            "cwd": str(stranger),
            "attachments": [{"kind": "spec", "path": ".engineering/specs/s.md"}],
        },
    )
    assert r.status_code == 422
    assert "not found" in r.text


@pytest.mark.parametrize(
    ("route", "status"),
    [("work-items", None), ("triggers", "paused")],
    ids=["work-items", "triggers"],
)
def test_no_chain_template_resolves_default_and_stays_distinguishable(client, repo, route, status):
    """Kraft-cd47: an item created with no `chain_template` runs the `default`
    template's chain like it always did, but its row stores that nothing was
    chosen -- not the string "default", which an item that named that template
    outright also stores. The two must not collide.

    POST /triggers is the HTTP twin of a policy.yaml cron trigger (Kraft-859):
    it always files the item paused, regardless of policy or template --
    fire_trigger never reads body.autostart because TriggerBody has no such
    field."""
    unset = client.post(f"/api/{route}", json={"title": "t", "repo": str(repo)})
    named = client.post(
        f"/api/{route}", json={"title": "t", "repo": str(repo), "chain_template": "default"}
    )
    assert (unset.status_code, named.status_code) == (201, 201), unset.text + named.text
    unset = client.get(f"/api/work-items/{unset.json()['id']}").json()
    named = client.get(f"/api/work-items/{named.json()['id']}").json()
    assert unset["chain_template"] is None
    assert named["chain_template"] == "default"
    assert unset["chain_definition"]["nodes"] == named["chain_definition"]["nodes"]
    if status:
        assert (unset["status"], named["status"]) == (status, status)


def test_trigger_refuses_with_503_when_policy_is_invalid(client, repo):
    """Same posture as create_work_item (spec §9): a re-run we cannot bound is
    not started. Set app.state.invalid_policy directly rather than writing a
    malformed policy.yaml to disk -- test_get_work_item_survives_an_invalid_policy
    (tests/test_api_board.py) mutates app.state the same way, for the same
    reason: it isolates the one code path under test from startup.py's parsing."""
    client.app.state.invalid_policy = ["boom: not a number"]
    r = client.post("/api/triggers", json={"title": "t", "repo": str(repo)})
    assert r.status_code == 503
    assert "policy config invalid" in r.json()["detail"]


def _policy_chains(templates_dir):
    """Three chains over one fake task, and an instance ceiling of
    `allowed_tools: [git, shell, editor]` (Kraft-ib2af, Kraft-yaq99)."""
    node = (
        "nodes:\n"
        "  - id: implementation\n"
        "    kind: exec\n"
        "    tasks:\n"
        "      - { id: run, kind: agent, harness: fake, prompt: go }\n"
    )
    chains = {
        "a": "policy: { allowed_tools: [git], timeout_minutes: 5 }\n",
        "b": "policy: { allowed_tools: [git, shell] }\n",
        "c": "",
        "wide": "policy: { allowed_tools: [git, rm_rf] }\n",
    }
    for id, policy in chains.items():
        (templates_dir / "chains" / f"{id}.yaml").write_text(f"id: {id}\n{policy}{node}")
    with (templates_dir / "policy.yaml").open("a") as f:
        f.write("maxima:\n  allowed_tools: [git, shell, editor]\n")


_POLICY_CHAINS = pytest.mark.api_client(edit_templates=_policy_chains)


def _snapshot_policy(client, wid):
    return json.loads(client.get(f"/api/work-items/{wid}").json()["materialized_chain"])["policy"]


@_POLICY_CHAINS
def test_a_chain_policy_past_the_ceiling_is_a_422_at_intake_not_a_500(client, repo):
    r = client.post(
        "/api/work-items",
        json={"title": "t", "repo": str(repo), "chain_template": "wide", "autostart": False},
    )
    assert r.status_code == 422, r.text
    assert "rm_rf" in r.json()["detail"]


@_POLICY_CHAINS
def test_switching_template_applies_only_the_new_chains_policy(client, repo):
    """Kraft-yaq99: the switch re-materializes from the instance policy, so the
    old chain's override does not stack under the new one's -- and a switch
    that is legal from the instance policy is not refused by the old chain's."""
    wid = client.post(
        "/api/work-items",
        json={"title": "t", "repo": str(repo), "chain_template": "a", "autostart": False},
    ).json()["id"]
    assert _snapshot_policy(client, wid)["allowed_tools"] == ["git"]

    r = client.patch(f"/api/work-items/{wid}", json={"chain_template": "c"})
    assert r.status_code == 200, r.text
    on_c = _snapshot_policy(client, wid)
    assert on_c["allowed_tools"] == ["git", "shell", "editor"]
    assert on_c["timeout_minutes"] is None

    client.patch(f"/api/work-items/{wid}", json={"chain_template": "a"})
    r = client.patch(f"/api/work-items/{wid}", json={"chain_template": "b"})
    assert r.status_code == 200, r.text
    assert _snapshot_policy(client, wid)["allowed_tools"] == ["git", "shell"]
