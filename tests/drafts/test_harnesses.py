"""The `harnesses` draft area's ops and its resolve (W13 D): the access
mapping onto `policy.yaml`'s `allowed_harnesses` lists, the escalation and
tools ops, and the agent-profile ops."""

from __future__ import annotations

import pytest
import yaml

from kraft.drafts import harnesses as area

pytestmark = pytest.mark.api_client(default_setup=False)

URL = "/api/drafts/harnesses/harnesses"
UNIVERSE = ["codex", "claude"]


def ops(client, *batch, query=""):
    return client.post(f"{URL}/ops{query}", json={"ops": list(batch)})


def access(state_of: dict[str, str]):
    return [{"op": "set_access", "harness": h, "state": s} for h, s in state_of.items()]


def written(client, name):
    return yaml.safe_load(client.get(URL).json()["files"][name])


def policy_lists(client):
    policy = written(client, "policy.yaml")
    return (
        policy.get("defaults", {}).get("allowed_harnesses"),
        policy.get("maxima", {}).get("allowed_harnesses"),
    )


# ── the mapping, with no client ──


@pytest.mark.parametrize(
    ("policy", "expected"),
    [
        ({}, ["available", "available"]),
        ({"maxima": {"allowed_harnesses": ["claude"]}}, ["never", "available"]),
        ({"defaults": {"allowed_harnesses": ["claude"]}}, ["override", "available"]),
        (
            {
                "defaults": {"allowed_harnesses": ["claude"]},
                "maxima": {"allowed_harnesses": ["codex", "claude"]},
            },
            ["override", "available"],
        ),
        (
            {
                "defaults": {"allowed_harnesses": []},
                "maxima": {"allowed_harnesses": ["claude"]},
            },
            ["never", "override"],
        ),
    ],
)
def test_access_reads_the_two_lists(policy, expected):
    assert list(area.access(policy, UNIVERSE).values()) == expected


# ── set_access ──


def test_each_state_is_a_pair_of_lists(client):
    ops(client, *access({"codex": "override"}))
    # No maximum: nothing is bound, so codex is allowed, just not a default.
    assert policy_lists(client) == (["claude", "fake"], None)

    ops(client, *access({"codex": "never"}))
    assert policy_lists(client) == (["claude", "fake"], ["claude", "fake"])

    ops(client, *access({"codex": "available"}))
    assert policy_lists(client) == (None, None)


def test_never_is_an_explicit_maximum_not_just_a_default(client):
    """Mutate: write `never` into `defaults` only, and a task's own `policy:` could
    still widen to it, so the harness would not be never at all."""
    ops(client, *access({"codex": "never"}))
    defaults, maxima = policy_lists(client)
    assert maxima == defaults == ["claude", "fake"]


def test_a_list_the_published_policy_had_stays_written_and_keeps_its_order(client, templates_dir):
    path = templates_dir / "policy.yaml"
    path.write_text(
        path.read_text()
        + "\ndefaults:\n  allowed_harnesses: [fake, claude, codex]"
        + "\nmaxima:\n  allowed_harnesses: [fake, claude, codex]\n"
    )
    ops(client, *access({"codex": "never"}))
    assert policy_lists(client) == (["fake", "claude"], ["fake", "claude"])
    ops(client, *access({"codex": "available"}))
    assert policy_lists(client) == (["fake", "claude", "codex"], ["fake", "claude", "codex"])


def test_nothing_bound_and_nothing_published_changes_nothing(client):
    r = ops(client, *access({"codex": "available"}))
    assert r.status_code == 200
    assert policy_lists(client) == (None, None)
    assert r.json()["result"]["changes"] == []


def test_set_access_refuses_an_unknown_harness_and_state(client):
    assert ops(client, *access({"nosuch": "never"})).status_code == 422
    assert ops(client, *access({"codex": "sometimes"})).status_code == 422


def test_the_resolved_view_lists_each_harness_with_its_state(client):
    r = ops(client, *access({"codex": "never"}))
    view = r.json()["result"]["resolved"]["harnesses"]
    assert [(h["id"], h["state"]) for h in view] == [
        ("codex", "never"),
        ("claude", "available"),
        ("fake", "available"),
    ]
    assert {c["path"] for c in r.json()["result"]["changes"]} == {"access.codex", "policy.yaml"}


# ── what a never harness breaks ──


def test_a_task_selecting_a_never_harness_is_a_problem_naming_it(client):
    r = ops(client, *access({"claude": "never"}))
    problems = r.json()["result"]["problems"]
    assert (
        "spec.main.author: harness 'claude' is not in its allowed_harnesses"
        in problems[0]["message"]
    )
    assert problems[0]["chain"] == "default"
    assert client.post(f"{URL}/publish").status_code == 422


def test_override_blocks_nothing_a_task_does_not_select_a_never_does(client):
    """Mutate: lint against the on-disk policy instead of the draft's, and
    the `never` above shows no problem."""
    assert ops(client, *access({"codex": "override"})).json()["result"]["problems"] == []
    assert client.post(f"{URL}/publish").status_code == 200


def test_a_fallback_to_a_never_harness_is_a_problem_at_the_fallbacks_path(client, templates_dir):
    path = templates_dir / "library.yaml"
    library = yaml.safe_load(path.read_text())
    task = next(t for t in library["tasks"].values() if t.get("harness") == "claude")
    task["fallback"] = [{"harness": "codex"}]
    path.write_text(yaml.safe_dump(library, sort_keys=False))
    client.post("/api/templates/reload")

    problems = ops(client, *access({"codex": "never"})).json()["result"]["problems"]
    assert [p["message"] for p in problems if p["chain"] == "default"] == [
        "spec.main.author: fallback harness 'codex' is not in its "
        "allowed_harnesses ['claude', 'fake']"
    ]


def test_a_policy_saved_since_the_draft_began_is_a_409(client, templates_dir):
    ops(client, *access({"codex": "override"}))
    path = templates_dir / "policy.yaml"
    path.write_text(path.read_text() + "\n# saved elsewhere\n")
    r = client.post(f"{URL}/publish")
    assert r.status_code == 409
    assert "policy.yaml" in r.json()["files"]


# ── escalation and tools ──


def test_escalation_and_tools_are_set_and_cleared(client):
    ops(
        client,
        {"op": "set_escalation", "harness": "claude", "grants": ["Bash"]},
        {"op": "set_allowed_tools", "tools": ["Read", "Edit"]},
    )
    policy = written(client, "policy.yaml")
    assert policy["defaults"]["escalation_harness"] == "claude"
    assert policy["defaults"]["escalation_grants"] == ["Bash"]
    assert policy["maxima"]["allowed_tools"] == ["Read", "Edit"]

    r = ops(
        client,
        {"op": "set_escalation", "harness": None, "grants": None},
        {"op": "set_allowed_tools", "tools": None},
    )
    resolved = r.json()["result"]["resolved"]
    assert resolved["escalation"] == {"harness": None, "grants": None}
    assert resolved["allowed_tools"] is None
    assert "escalation_harness" not in str(written(client, "policy.yaml"))


def test_tools_must_be_a_list_of_names(client):
    assert ops(client, {"op": "set_allowed_tools", "tools": "Read"}).status_code == 422


# ── profiles ──


def test_set_profile_in_both_shapes(client):
    r = ops(
        client,
        {
            "op": "set_profile",
            "name": "fast",
            "patch": {"model": {"claude": "haiku"}, "effort": "low"},
        },
        {
            "op": "set_profile",
            "name": "deep",
            "patch": {"providers": {"codex": {"model": "gpt-5", "effort": "high"}}},
        },
    )
    assert r.status_code == 200, r.text
    profiles = written(client, "harnesses.yaml")["profiles"]
    assert profiles["fast"]["model"]["claude"] == "haiku"
    # A `providers` patch turned the older shape into the new one, per provider.
    assert profiles["deep"]["providers"]["codex"] == {"model": "gpt-5", "effort": "high"}
    assert "model" not in profiles["deep"]


def test_set_profile_refuses_both_shapes_at_once(client):
    batch = {"op": "set_profile", "name": "fast", "patch": {"providers": {}, "model": {}}}
    assert ops(client, batch).status_code == 422


def test_add_profile_copies_another(client):
    ops(client, {"op": "add_profile", "name": "mine", "copy_from": "fast"})
    profiles = written(client, "harnesses.yaml")["profiles"]
    assert profiles["mine"] == profiles["fast"]
    assert ops(client, {"op": "add_profile", "name": "mine"}).status_code == 422


def test_remove_profile_refuses_a_used_one_and_removes_a_free_one(client):
    used = ops(client, {"op": "remove_profile", "name": "strong"})
    assert used.status_code == 422
    assert "used by" in used.json()["detail"]
    ops(client, {"op": "add_profile", "name": "spare"}, {"op": "remove_profile", "name": "spare"})
    assert "spare" not in written(client, "harnesses.yaml")["profiles"]


def test_rename_profile_retargets_fallbacks_and_reports_the_tasks_it_broke(client):
    ops(client, {"op": "set_profile", "name": "fast", "patch": {"fallback": [{"profile": "deep"}]}})
    r = ops(client, {"op": "rename_profile", "name": "deep", "to": "heavy"})
    assert r.status_code == 200, r.text
    profiles = written(client, "harnesses.yaml")["profiles"]
    assert "deep" not in profiles and "heavy" in profiles
    assert profiles["fast"]["fallback"] == [{"profile": "heavy"}]
    [result] = [o for o in r.json()["ops"] if o["op"] == "rename_profile"]
    assert isinstance(result["result"]["broken"], list)
