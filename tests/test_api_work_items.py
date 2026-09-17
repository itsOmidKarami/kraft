"""Work item creation and patch: POST /work-items (including attachment intake
and title/description/chain-template/agent-override validation) and the
worktree-head fields on GET."""

from __future__ import annotations

import dataclasses
import os
import sqlite3
import subprocess
from pathlib import Path

from support.api import _FAKE_CLAUDE, _await_gate, _client, _poll_events, _post_default, _set_status
from support.harness import fake_templates_dir, make_repo, make_repo_with_engineering

from kraft.adapters import beads


def test_post_invalid_template_422(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        r = client.post(
            "/api/work-items", json={"title": "x", "repo": "/tmp", "chain_template": "nope"}
        )
        assert r.status_code == 422


def test_autostart_create_lands_paused_when_all_slots_are_busy(tmp_path, monkeypatch):
    """Kraft-m43g, Kraft-nxht: `create_work_item`'s autostart path used to hand
    `status="active"` straight to `intake`'s `INSERT` with no capacity check at
    all -- an autostart create always won a slot. It now loses this race the
    same non-error way an explicit `autostart: False` already does: paused,
    not rejected."""
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
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


def test_post_missing_repo_422(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        r = client.post("/api/work-items", json={"title": "x"})
        assert r.status_code == 422


def test_post_nonexistent_repo_422(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        r = client.post("/api/work-items", json={"title": "x", "repo": "/no/such/dir"})
        assert r.status_code == 422


def test_a_title_over_the_tracker_limit_is_refused_before_bd(tmp_path, monkeypatch):
    """Half of Kraft-cy30 landed already: `beads.intake` raises bd's own stderr
    (Kraft-ibwj) and `executor.intake` catches every bd failure into a
    `bead_warning` rather than a 502 (Kraft-7gy). What is left is quieter and
    worse -- an over-long title now *succeeds*, and the item exists with no bead
    and a warning line the caller may never read. Refuse it at the door.

    The offending title is not echoed back: the length and the limit are the
    actionable part, and this `detail` is printed straight through by
    `kraft item create`.
    """
    client = _client(tmp_path, monkeypatch)
    with client:
        repo = make_repo(tmp_path)
        long_title = "x" * 711
        r = client.post(
            "/api/work-items", json={"title": long_title, "repo": str(repo), "autostart": False}
        )
        assert r.status_code == 422
        detail = r.json()["detail"]
        assert "711" in detail
        assert str(beads.MAX_TITLE) in detail
        assert long_title not in detail

        assert client.get("/api/work-items").json()["items"] == []

        # the boundary itself is accepted
        ok = client.post(
            "/api/work-items",
            json={"title": "x" * beads.MAX_TITLE, "repo": str(repo), "autostart": False},
        )
        assert ok.status_code == 201, ok.text


def test_get_unknown_work_item_404(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        assert client.get("/api/work-items/does-not-exist").status_code == 404
        assert client.get("/api/worker-sessions/nope/log").status_code == 404


def test_create_accepts_a_description_and_both_payloads_return_it(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    with client:
        repo = make_repo(tmp_path)
        r = client.post(
            "/api/work-items",
            json={
                "title": "short label",
                "description": "the brief the spec is written from",
                "repo": str(repo),
                "autostart": False,
            },
        )
        assert r.status_code == 201
        wid = r.json()["id"]

        detail = client.get(f"/api/work-items/{wid}").json()
        assert detail["description"] == "the brief the spec is written from"

        listed = client.get("/api/work-items").json()["items"]
        assert [i["description"] for i in listed if i["id"] == wid] == [
            "the brief the spec is written from"
        ]


def test_create_without_a_description_returns_null(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    with client:
        repo = make_repo(tmp_path)
        wid = client.post(
            "/api/work-items", json={"title": "t", "repo": str(repo), "autostart": False}
        ).json()["id"]
        assert client.get(f"/api/work-items/{wid}").json()["description"] is None
        listed = client.get("/api/work-items").json()["items"]
        assert [i["description"] for i in listed if i["id"] == wid] == [None]


def test_create_work_item_accepts_auto_gate(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    with client:
        repo = make_repo(tmp_path)
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


def test_patch_updates_the_description_and_records_an_event(tmp_path, monkeypatch):
    """The description feeds every agent prompt, so an edit has to be answerable
    from the timeline: `events` is the authoritative log."""
    client = _client(tmp_path, monkeypatch)
    with client:
        repo = make_repo(tmp_path)
        wid = client.post(
            "/api/work-items",
            json={"title": "t", "description": "first", "repo": str(repo), "autostart": False},
        ).json()["id"]
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


def test_patch_404s_on_an_unknown_work_item(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    with client:
        assert client.patch("/api/work-items/nope", json={"description": "x"}).status_code == 404


def test_patch_can_clear_the_description(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    with client:
        repo = make_repo(tmp_path)
        wid = client.post(
            "/api/work-items",
            json={"title": "t", "description": "first", "repo": str(repo), "autostart": False},
        ).json()["id"]
        assert client.patch(f"/api/work-items/{wid}", json={"description": ""}).status_code == 200
        assert client.get(f"/api/work-items/{wid}").json()["description"] is None


def test_patch_sets_the_title_without_clobbering_the_description(tmp_path, monkeypatch):
    """Absent means untouched, in both directions. A screen that patches only
    the title must not blank the brief, and vice versa."""
    client = _client(tmp_path, monkeypatch)
    with client:
        repo = make_repo(tmp_path)
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


def test_patch_refuses_an_empty_body_and_a_blank_title(tmp_path, monkeypatch):
    """`{}` is a caller bug, not a no-op to absorb; a blank title would leave the
    board with an unlabelled row and nothing to search on."""
    client = _client(tmp_path, monkeypatch)
    with client:
        repo = make_repo(tmp_path)
        wid = client.post(
            "/api/work-items", json={"title": "t", "repo": str(repo), "autostart": False}
        ).json()["id"]

        empty = client.patch(f"/api/work-items/{wid}", json={})
        assert empty.status_code == 422
        assert "nothing to patch" in empty.json()["detail"]

        blank = client.patch(f"/api/work-items/{wid}", json={"title": "   "})
        assert blank.status_code == 422
        assert "title cannot be empty" in blank.json()["detail"]

        assert client.get(f"/api/work-items/{wid}").json()["title"] == "t"


def test_patch_switches_chain_template_before_the_chain_starts(tmp_path, monkeypatch):
    """Kraft-gwn6: a not-yet-started item can switch onto a different
    template's own materialized chain."""
    client = _client(tmp_path, monkeypatch)
    with client:
        repo = make_repo(tmp_path)
        wid = client.post(
            "/api/work-items",
            json={
                "title": "t",
                "repo": str(repo),
                "chain_template": "quick-task",
                "autostart": False,
            },
        ).json()["id"]
        before = client.get(f"/api/work-items/{wid}").json()
        assert [n["id"] for n in before["chain_definition"]["nodes"]] == [
            "env_setup",
            "implementation",
            "verify",
        ]

        r = client.patch(f"/api/work-items/{wid}", json={"chain_template": "default"})
        assert r.status_code == 200, r.text
        assert r.json() == {"id": wid, "chain_template": "default"}

        after = client.get(f"/api/work-items/{wid}").json()
        assert after["chain_template"] == "default"
        assert [n["id"] for n in after["chain_definition"]["nodes"]] == [
            "spec",
            "plan",
            "chain_review",
            "env_setup",
            "implementation",
            "repos_scan",
            "verify",
            "pre_mr_rebase",
            "mr_meta",
            "open_mr",
            "mr_checks",
            "human_review",
            "mr_sync",
            "merge",
            "post_merge_watch",
        ]

        evs = client.get(f"/api/work-items/{wid}/events").json()
        changed = [e for e in evs if e["type"] == "chain_template_changed"]
        assert [e["payload"] for e in changed] == [{"from": "quick-task", "to": "default"}]


def test_patch_refuses_chain_template_once_started(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    with client:
        repo = make_repo(tmp_path)
        wid = client.post(
            "/api/work-items",
            json={
                "title": "t",
                "repo": str(repo),
                "chain_template": "quick-task",
                "autostart": False,
            },
        ).json()["id"]
        db_path = Path(os.environ["KRAFT_RUN_DIR"]) / "orchestrator.db"
        conn = sqlite3.connect(db_path)
        conn.execute("UPDATE work_items SET current_node_id = 'env_setup' WHERE id = ?", (wid,))
        conn.commit()
        conn.close()

        r = client.patch(f"/api/work-items/{wid}", json={"chain_template": "default"})
        assert r.status_code == 409, r.text

        after = client.get(f"/api/work-items/{wid}").json()
        assert after["chain_template"] == "quick-task"


def test_patch_refuses_an_unknown_chain_template(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    with client:
        repo = make_repo(tmp_path)
        wid = client.post(
            "/api/work-items", json={"title": "t", "repo": str(repo), "autostart": False}
        ).json()["id"]

        r = client.patch(f"/api/work-items/{wid}", json={"chain_template": "does-not-exist"})
        assert r.status_code == 404, r.text

        after = client.get(f"/api/work-items/{wid}").json()
        assert after["chain_template"] is None


def test_patch_switching_chain_template_preserves_attachment_gate_trim(tmp_path, monkeypatch):
    """An item that attached a spec at intake has already trimmed
    spec_approval out of its chain (Kraft-dgh); switching template must not
    force it to reattach to get that trim back."""
    templates_dir = fake_templates_dir(tmp_path, str(_FAKE_CLAUDE))
    (templates_dir / "custom.yaml").write_text(
        "id: custom\n"
        "nodes:\n"
        "  - { id: spec, tasks: [on.spec.requested], gate_after: spec_approval }\n"
        "  - { id: verify, tasks: [on.test.run], gate_after: null }\n"
    )
    client = _client(tmp_path, monkeypatch, templates_dir=templates_dir)
    with client:
        repo = make_repo(tmp_path)
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
        assert "spec" not in [n["id"] for n in before["chain_definition"]["nodes"]]

        r = client.patch(f"/api/work-items/{wid}", json={"chain_template": "custom"})
        assert r.status_code == 200, r.text

        after = client.get(f"/api/work-items/{wid}").json()
        assert [n["id"] for n in after["chain_definition"]["nodes"]] == ["verify"]


def test_patch_sets_agent_overrides_and_records_an_event(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    with client:
        repo = make_repo(tmp_path)
        wid = client.post(
            "/api/work-items", json={"title": "t", "repo": str(repo), "autostart": False}
        ).json()["id"]

        r = client.patch(
            f"/api/work-items/{wid}",
            json={"agent_overrides": {"model": "opus", "effort": "high"}},
        )
        assert r.status_code == 200, r.text

        evs = client.get(f"/api/work-items/{wid}/events").json()
        changed = [e for e in evs if e["type"] == "agent_overrides_changed"]
        assert [e["payload"] for e in changed] == [
            {"overrides": {"model": "opus", "effort": "high"}}
        ]


def test_patch_clears_agent_overrides_with_an_empty_object(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    with client:
        repo = make_repo(tmp_path)
        wid = client.post(
            "/api/work-items", json={"title": "t", "repo": str(repo), "autostart": False}
        ).json()["id"]
        client.patch(f"/api/work-items/{wid}", json={"agent_overrides": {"model": "opus"}})

        r = client.patch(f"/api/work-items/{wid}", json={"agent_overrides": {}})
        assert r.status_code == 200, r.text

        row = client.get(f"/api/work-items/{wid}").json()
        assert row["agent_overrides"] is None


def test_patch_rejects_invalid_agent_overrides_with_422(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    with client:
        repo = make_repo(tmp_path)
        wid = client.post(
            "/api/work-items", json={"title": "t", "repo": str(repo), "autostart": False}
        ).json()["id"]

        r = client.patch(f"/api/work-items/{wid}", json={"agent_overrides": {"effort": "turbo"}})
        assert r.status_code == 422, r.text


def test_patch_accepts_agent_overrides_on_a_started_or_paused_item(tmp_path, monkeypatch):
    """Unlike chain_template, a model/effort dial has no current_node_id
    restriction -- it is the door to make a stuck item cheaper before its
    next retry."""
    client = _client(tmp_path, monkeypatch)
    with client:
        repo = make_repo(tmp_path)
        wid = client.post(
            "/api/work-items", json={"title": "t", "repo": str(repo), "autostart": False}
        ).json()["id"]
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


def test_no_explicit_chain_template_still_runs_default_and_is_distinguishable(
    tmp_path, monkeypatch
):
    """Kraft-cd47: an item created with no `chain_template` runs the `default`
    template's chain like it always did, but its row stores that nothing was
    chosen -- not the string "default", which an item that named that template
    outright also stores. The two must not collide."""
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        unset_wid = client.post("/api/work-items", json={"title": "t", "repo": str(repo)}).json()[
            "id"
        ]
        named_wid = client.post(
            "/api/work-items", json={"title": "t", "repo": str(repo), "chain_template": "default"}
        ).json()["id"]
        unset = client.get(f"/api/work-items/{unset_wid}").json()
        named = client.get(f"/api/work-items/{named_wid}").json()
        assert unset["chain_template"] is None
        assert named["chain_template"] == "default"
        assert unset["chain_definition"]["nodes"] == named["chain_definition"]["nodes"]


def test_get_work_item_reports_the_worktree_head(tmp_path, monkeypatch):
    """Kraft-lu2: the gate compares a measurement's sha against this. Without it
    the frontend has nothing to compare to."""
    repo = make_repo(tmp_path)
    from kraft import executor

    async def noop(*a, **kw):
        return "completed"

    monkeypatch.setattr(executor, "run", noop)
    with _client(tmp_path, monkeypatch) as client:
        wid = client.post("/api/work-items", json={"title": "x", "repo": str(repo)}).json()["id"]
        body = client.get(f"/api/work-items/{wid}").json()
        assert "head_sha" in body


def test_get_work_item_head_sha_is_none_before_the_worktree_exists(tmp_path, monkeypatch):
    """A paused item has no worktree yet; `git_read` returns None rather than
    raising, and the field must carry that through instead of 500ing."""
    repo = make_repo(tmp_path)
    from kraft import executor

    async def noop(*a, **kw):
        return "completed"

    monkeypatch.setattr(executor, "run", noop)
    with _client(tmp_path, monkeypatch) as client:
        wid = client.post("/api/work-items", json={"title": "x", "repo": str(repo)}).json()["id"]
        body = client.get(f"/api/work-items/{wid}").json()
        assert body["head_sha"] is None


def test_post_refused_when_policy_invalid(tmp_path, monkeypatch):
    bad = fake_templates_dir(tmp_path, str(_FAKE_CLAUDE))
    (bad / "policy.yaml").write_text("default: { attempts: 0, wall_clock_s: 1 }\n")
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch, templates_dir=bad) as client:
        r = client.post(
            "/api/work-items",
            json={"title": "x", "repo": str(repo), "chain_template": "default"},
        )
        assert r.status_code != 201
        assert "policy" in r.json()["detail"].lower()


def test_post_materializes_chain(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
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
        assert body["current_node_id"] == "env_setup"
        assert [n["id"] for n in body["chain_definition"]["nodes"]] == [
            "env_setup",
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


def test_cross_repo_intake_records_submodules_but_the_repos_panel_waits_for_the_worktree(
    tmp_path, monkeypatch
):
    """Design 1g's cross-repo disclosure: `submodules`/`root_merge_policy` are
    recorded at intake. The repos panel itself (design 3a) is empty until
    `ensure_worktree` writes `work_item_repos` rows (Kraft-qlsf) -- a
    deliberate trade-off over an intake-time preview, not a bug: `repos_for`
    now reports real per-repo merge state instead of a placeholder derived
    from `merge` node completion."""
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = client.post(
            "/api/work-items",
            json={
                "repo": str(repo),
                "title": "bump the pointers",
                "chain_template": "quick-task",
                "submodules": ["libs/a", "vendor/deep/b"],
                "root_merge_policy": "skip",
            },
        ).json()["id"]
        body = client.get(f"/api/work-items/{wid}").json()
        assert body["repos"] == []
        assert body["root_merge_policy"] == "skip"

        bad = client.post(
            "/api/work-items",
            json={"repo": str(repo), "title": "x", "root_merge_policy": "nonsense"},
        )
        assert bad.status_code == 422


def test_a_single_repo_item_has_no_repos_panel(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = client.post(
            "/api/work-items",
            json={"repo": str(repo), "title": "solo", "chain_template": "quick-task"},
        ).json()["id"]
        assert client.get(f"/api/work-items/{wid}").json()["repos"] == []


def test_intake_with_a_plan_attachment_trims_the_chain_and_reports_it(tmp_path, monkeypatch):
    repo = make_repo_with_engineering(tmp_path, {".engineering/plans/p.md": "# plan\n"})
    with _client(tmp_path, monkeypatch) as client:
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
        assert [
            {k: v for k, v in a.items() if k != "source"} for a in item["attachments"]
        ] == expected
        assert Path(item["attachments"][0]["source"]).is_relative_to(stored_dir)
        assert "plan_approval" not in [n["gate_after"] for n in item["chain_definition"]["nodes"]]
        listed = next(i for i in client.get("/api/work-items").json()["items"] if i["id"] == wid)
        assert [
            {k: v for k, v in a.items() if k != "source"} for a in listed["attachments"]
        ] == expected


def test_intake_with_a_plan_attachment_never_runs_the_plan_node(tmp_path, monkeypatch):
    """The trimmed node must be absent from the run, not merely from the
    chain_definition the UI reads (see the _trims_the_chain_and_reports_it
    test above for that check)."""
    repo = make_repo_with_engineering(tmp_path, {".engineering/plans/p.md": "# plan\n"})
    with _client(tmp_path, monkeypatch) as client:
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
        events = _poll_events(client, wid, "node_started", count=2)
        started = [e["payload"]["node_id"] for e in events if e["type"] == "node_started"]
        assert "plan" not in started
        assert "chain_review" in started


def test_intake_rejects_a_traversing_attachment_path(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        r = client.post(
            "/api/work-items",
            json={
                "title": "t",
                "repo": str(repo),
                "attachments": [{"kind": "plan", "path": "../outside.md"}],
            },
        )
        assert r.status_code == 422
        assert "escapes" in r.text


def test_intake_rejects_an_absolute_attachment_path(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    # Absolute and outside the repo, but real — proves rejection is about
    # location, not existence (an absolute path to a missing file would 422
    # for the wrong reason).
    outside = tmp_path / "outside.md"
    outside.write_text("# outside\n")
    with _client(tmp_path, monkeypatch) as client:
        r = client.post(
            "/api/work-items",
            json={
                "title": "t",
                "repo": str(repo),
                "attachments": [{"kind": "plan", "path": str(outside)}],
            },
        )
        assert r.status_code == 422
        assert "escapes" in r.text


def test_intake_rejects_a_symlink_that_escapes_the_repo(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text("# outside\n")
    # The only thing that makes this path escape is the symlink target; the
    # path string itself is repo-relative, so this fails only if .resolve()
    # actually follows the symlink before the is_relative_to check.
    escape = repo / ".engineering" / "plans" / "escape.md"
    escape.parent.mkdir(parents=True)
    escape.symlink_to(outside)
    with _client(tmp_path, monkeypatch) as client:
        r = client.post(
            "/api/work-items",
            json={
                "title": "t",
                "repo": str(repo),
                "attachments": [{"kind": "plan", "path": ".engineering/plans/escape.md"}],
            },
        )
        assert r.status_code == 422
        assert "escapes" in r.text


def test_intake_rejects_a_missing_attachment_and_a_duplicate_kind(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        missing = client.post(
            "/api/work-items",
            json={
                "title": "t",
                "repo": str(repo),
                "attachments": [{"kind": "plan", "path": ".engineering/plans/nope.md"}],
            },
        )
        assert missing.status_code == 422
        (repo / ".engineering" / "plans").mkdir(parents=True)
        (repo / ".engineering" / "plans" / "p.md").write_text("# p\n")
        dupe = client.post(
            "/api/work-items",
            json={
                "title": "t",
                "repo": str(repo),
                "attachments": [
                    {"kind": "plan", "path": ".engineering/plans/p.md"},
                    {"kind": "plan", "path": ".engineering/plans/p.md"},
                ],
            },
        )
        assert dupe.status_code == 422


def test_intake_accepts_an_uncommitted_attachment(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    plan = repo / ".engineering" / "plans" / "p.md"
    plan.parent.mkdir(parents=True)
    plan.write_text("# uncommitted\n")
    with _client(tmp_path, monkeypatch) as client:
        r = client.post(
            "/api/work-items",
            json={
                "title": "t",
                "repo": str(repo),
                "attachments": [{"kind": "plan", "path": ".engineering/plans/p.md"}],
            },
        )
        assert r.status_code == 201, r.text


def test_intake_accepts_an_attachment_from_another_worktree_of_the_repo(tmp_path, monkeypatch):
    """Kraft-85wk. The document exists only in the working tree the caller is
    standing in — which for a Kraft worker is always true, because the
    registered repo is the main checkout by construction (config.probe_repo
    normalizes a worktree to it). Resolving under the repo alone 422s the exact
    handoff attachments exist for."""
    repo = make_repo(tmp_path)
    worktree = _linked_worktree(repo, tmp_path)
    spec = worktree / ".engineering" / "specs" / "s.md"
    spec.parent.mkdir(parents=True)
    spec.write_text("# written in the worktree\n")
    assert not (repo / ".engineering" / "specs" / "s.md").exists()
    with _client(tmp_path, monkeypatch) as client:
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


def test_intake_without_a_cwd_still_scopes_attachments_to_the_repo(tmp_path, monkeypatch):
    """The browser sends no cwd, so its reachable set stays exactly the repo,
    as Kraft-7izl set it. Same file, same request, minus one field."""
    repo = make_repo(tmp_path)
    worktree = _linked_worktree(repo, tmp_path)
    spec = worktree / ".engineering" / "specs" / "s.md"
    spec.parent.mkdir(parents=True)
    spec.write_text("# written in the worktree\n")
    with _client(tmp_path, monkeypatch) as client:
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


def test_intake_ignores_a_cwd_in_a_different_repo(tmp_path, monkeypatch):
    """`cwd` is not "read any file on this machine": a second root is added only
    when it is another working tree of the *same* repository. An unrelated repo
    holding a file at the same relative path proves the check is the shared
    `.git` and not the path string."""
    repo = make_repo(tmp_path)
    stranger = make_repo(tmp_path, name="stranger")
    spec = stranger / ".engineering" / "specs" / "s.md"
    spec.parent.mkdir(parents=True)
    spec.write_text("# not this repo's spec\n")
    with _client(tmp_path, monkeypatch) as client:
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


def test_trigger_creates_a_paused_work_item_and_resolves_default_template(tmp_path, monkeypatch):
    """POST /triggers is the HTTP twin of a policy.yaml cron trigger
    (Kraft-859): it always files the item paused, regardless of policy or
    template -- fire_trigger never reads body.autostart because TriggerBody
    has no such field. `chain_template: null` resolves to the `default`
    template the same way create_work_item's Kraft-cd47 fix does: the row
    stores None (distinguishable from an item that named "default" outright)
    but the materialized chain is the default template's."""
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        unset = client.post("/api/triggers", json={"title": "t", "repo": str(repo)})
        assert unset.status_code == 201, unset.text
        unset_body = unset.json()
        assert unset_body["status"] == "paused"
        assert unset_body["chain_template"] is None

        named = client.post(
            "/api/triggers",
            json={"title": "t", "repo": str(repo), "chain_template": "default"},
        )
        assert named.status_code == 201, named.text
        named_body = named.json()
        assert named_body["status"] == "paused"
        assert named_body["chain_template"] == "default"

        assert unset_body["chain_definition"]["nodes"] == named_body["chain_definition"]["nodes"]


def test_trigger_rejects_an_unknown_chain_template_422(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        r = client.post(
            "/api/triggers",
            json={"title": "t", "repo": str(repo), "chain_template": "nope"},
        )
        assert r.status_code == 422
        assert "unknown or invalid template" in r.json()["detail"]


def test_trigger_rejects_a_nonexistent_repo_422(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        r = client.post("/api/triggers", json={"title": "t", "repo": "/no/such/dir"})
        assert r.status_code == 422
        assert "repo path does not exist" in r.json()["detail"]


def test_trigger_rejects_a_title_over_the_tracker_limit_422(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        long_title = "x" * (beads.MAX_TITLE + 1)
        r = client.post("/api/triggers", json={"title": long_title, "repo": str(repo)})
        assert r.status_code == 422
        detail = r.json()["detail"]
        assert str(beads.MAX_TITLE) in detail
        assert long_title not in detail

        # the boundary itself is accepted
        ok = client.post("/api/triggers", json={"title": "x" * beads.MAX_TITLE, "repo": str(repo)})
        assert ok.status_code == 201, ok.text


def test_trigger_refuses_with_503_when_policy_is_invalid(tmp_path, monkeypatch):
    """Same posture as create_work_item (spec §9): a re-run we cannot bound is
    not started. Set app.state.invalid_policy directly rather than writing a
    malformed policy.yaml to disk -- test_get_work_item_survives_an_invalid_policy
    (tests/test_api_board.py) mutates app.state the same way, for the same
    reason: it isolates the one code path under test from startup.py's parsing."""
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        client.app.state.invalid_policy = ["boom: not a number"]
        r = client.post("/api/triggers", json={"title": "t", "repo": str(repo)})
        assert r.status_code == 503
        assert "policy config invalid" in r.json()["detail"]
