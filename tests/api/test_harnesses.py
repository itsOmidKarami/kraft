"""Harness profiles as their own resource (Kraft-archr, Ruling 206): each
`harnesses.yaml` profile with the library tasks and chains that select it, the
providers' read-only capability surfaces, and a save of one profile that goes
through the loader's own parse and refuses, writing nothing, an edit that
stops a chain's agent task from launching (`harness-api-lists-and-guards-profiles`)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml


def snapshot(root: Path) -> dict[str, bytes]:
    """Every file under `root` with its bytes: equal before and after means
    nothing was written."""
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


#: A chain whose one agent task asks the `claude` profile for `effort: max`,
#: which the claude provider accepts and codex does not.
MAX_EFFORT_CHAIN = {
    "id": "max-effort",
    "nodes": [
        {
            "id": "run",
            "kind": "exec",
            "tasks": [
                {"id": "t", "kind": "agent", "harness": "claude", "prompt": "p", "effort": "max"}
            ],
        }
    ],
}


def _max_effort_chain(templates_dir):
    (templates_dir / "chains" / "max-effort.yaml").write_text(yaml.safe_dump(MAX_EFFORT_CHAIN))
    # The library's claude model pins would be refused by codex first; drop
    # them so `effort: max` is the one thing the provider change breaks.
    path = templates_dir / "library.yaml"
    library = yaml.safe_load(path.read_text())
    for task in library["tasks"].values():
        task.pop("model", None)
    path.write_text(yaml.safe_dump(library, sort_keys=False))


def _chain_on_missing_profile(templates_dir):
    task = {"id": "t", "kind": "agent", "harness": "gone", "prompt": "p"}
    chain = {"id": "orphan", "nodes": [{"id": "run", "kind": "exec", "tasks": [task]}]}
    (templates_dir / "chains" / "orphan.yaml").write_text(yaml.safe_dump(chain))


def _extending_task(templates_dir):
    path = templates_dir / "library.yaml"
    library = yaml.safe_load(path.read_text())
    library["tasks"]["child"] = {"extends": "implementer", "prompt": "p"}
    path.write_text(yaml.safe_dump(library, sort_keys=False))


def _profiles(client) -> dict[str, dict]:
    response = client.get("/api/harnesses/profiles")
    assert response.status_code == 200, response.text
    return {p["id"]: p for p in response.json()["profiles"]}


# ── GET /harnesses/profiles ──


def test_every_profile_is_listed_with_the_library_tasks_and_chains_selecting_it(
    client, templates_dir
):
    body = client.get("/api/harnesses/profiles").json()
    profiles = {p["id"]: p for p in body["profiles"]}
    on_disk = yaml.safe_load((templates_dir / "harnesses.yaml").read_text())["harnesses"]

    assert body["file"] == str(templates_dir / "harnesses.yaml")
    assert body["error"] is None
    assert set(profiles) == set(on_disk)
    claude = profiles["claude"]
    assert claude["provider"] == on_disk["claude"]["provider"]
    assert claude["enabled"] is True
    assert claude["defaults"] == {"model": "sonnet"}
    # Every agent task the shipped library declares selects `claude` (R1).
    assert "tasks.implementer" in claude["used_by"]
    assert "tasks.spec_author" in claude["used_by"]
    assert "tasks.verify_changed_scopes" not in claude["used_by"]
    assert claude["chains"] == ["default", "quick-task"]
    assert profiles["codex"]["used_by"] == []
    assert profiles["codex"]["chains"] == []


@pytest.mark.api_client(edit_templates=_extending_task)
def test_a_library_task_uses_the_profile_it_inherits(client):
    assert "tasks.child" in _profiles(client)["claude"]["used_by"]


def test_one_profile_by_id_and_an_unknown_one_is_404(client):
    assert client.get("/api/harnesses/profiles/claude").json()["defaults"] == {"model": "sonnet"}
    response = client.get("/api/harnesses/profiles/nope")
    assert response.status_code == 404
    assert "nope" in response.json()["detail"]


def test_a_file_that_does_not_load_is_named_not_a_500(client, templates_dir):
    (templates_dir / "harnesses.yaml").write_text("harnesses: [a list]\n")
    body = client.get("/api/harnesses/profiles").json()
    assert body["profiles"] == []
    assert "must be a mapping keyed by id" in body["error"]


# ── GET /harnesses/providers ──


def test_providers_are_each_packages_capability_surface(client):
    providers = client.get("/api/harnesses/providers").json()
    codex = providers["valid"]["codex"]
    assert codex["command"] == ["codex", "exec"]
    assert codex["capabilities"]["effort"]["values"] == [
        "minimal",
        "low",
        "medium",
        "high",
        "xhigh",
    ]
    assert codex["capabilities"]["permission_mode"]["always"] == "workspace-write"
    assert {"claude", "codex", "gemini"} <= set(providers["valid"])
    assert providers["invalid"] == {}


def test_a_capability_says_what_it_becomes_and_where_its_file_is(client):
    """The Harnesses page renders each capability as the flag it becomes, or
    what reads it, and says whether the definition is packaged or an override."""
    claude = client.get("/api/harnesses/providers/claude").json()
    pm = claude["capabilities"]["permission_mode"]
    assert pm["cli"] == ["--permission-mode", "{value}"]
    assert pm["under_allowlist"] == "manual"
    usage = claude["capabilities"]["usage"]
    assert (usage["source"], usage["reader"]) == ("envelope", "claude-stream-json")
    codex = client.get("/api/harnesses/providers/codex").json()
    assert codex["capabilities"]["resume"]["via"] == "command_resume"
    # The suite overlays claude with a fake agent (support.harness); codex is packaged.
    assert codex["override"] is False


def test_a_provider_from_kraft_home_is_marked_an_override(client):
    from kraft.harness import BUNDLED
    from kraft.paths import default_harnesses_dir

    overlay = default_harnesses_dir()
    overlay.mkdir(parents=True, exist_ok=True)
    (overlay / "codex.yaml").write_text((BUNDLED / "codex.yaml").read_text())
    codex = client.get("/api/harnesses/providers/codex").json()
    assert codex["override"] is True
    assert codex["path"] == str(overlay / "codex.yaml")


def test_one_provider_by_id_and_an_unknown_one_is_404(client):
    assert client.get("/api/harnesses/providers/codex").json()["command"] == ["codex", "exec"]
    response = client.get("/api/harnesses/providers/nope")
    assert response.status_code == 404
    assert "nope" in response.json()["detail"]


@pytest.mark.parametrize("pid", ["providers", "profiles"])
def test_a_profile_may_take_the_name_of_a_harnesses_route(client, pid):
    """Profiles and providers each have their own prefix, so no id is reserved."""
    response = client.put(f"/api/harnesses/profiles/{pid}", json={"provider": "codex"})
    assert response.status_code == 200, response.text
    assert client.get(f"/api/harnesses/profiles/{pid}").json()["provider"] == "codex"
    assert pid in _profiles(client)


@pytest.mark.parametrize(
    "method, path",
    [("get", "/api/harnesses"), ("get", "/api/harnesses/claude"), ("put", "/api/harnesses/claude")],
)
def test_the_flat_profile_paths_are_gone(client, method, path):
    kwargs = {"json": {"provider": "codex"}} if method == "put" else {}
    assert getattr(client, method)(path, **kwargs).status_code in (404, 405)


# ── PUT /harnesses/profiles/{id} ──


def test_a_profile_save_is_written_and_read_back(client, templates_dir):
    body = {"provider": "codex", "executable": "codex", "defaults": {"effort": "high"}}
    response = client.put("/api/harnesses/profiles/codex", json=body)

    assert response.status_code == 200, response.text
    assert response.json()["defaults"] == {"effort": "high"}
    on_disk = yaml.safe_load((templates_dir / "harnesses.yaml").read_text())["harnesses"]
    assert on_disk["codex"] == body  # as sent: no defaults filled in
    assert "claude" in on_disk  # the other profiles are kept
    assert _profiles(client)["codex"]["defaults"] == {"effort": "high"}


def test_a_new_profile_is_added(client):
    response = client.put("/api/harnesses/profiles/gem", json={"provider": "gemini"})
    assert response.status_code == 200, response.text
    assert _profiles(client)["gem"]["provider"] == "gemini"


@pytest.mark.parametrize(
    "pid, body, reason",
    [
        ("codex", {"provider": "codex", "defaults": {"effort": "bogus"}}, "not a valid value"),
        ("codex", {"provider": "codex", "defaults": {"autocompact": "x"}}, "not a capability"),
        ("codex", {"provider": "nope"}, "not an installed harness"),
        ("codex", {"provider": "codex", "colour": "red"}, "colour"),
        ("codex", {"provider": "codex", "enabled": "yes"}, "enabled"),
        ("a.b", {"provider": "codex"}, "must match"),
    ],
    ids=[
        "bad-value",
        "undeclared",
        "no-provider",
        "unknown-key",
        "not-strict",
        "bad-id",
    ],
)
def test_a_save_the_loader_refuses_is_refused_and_writes_nothing(
    client, templates_dir, pid, body, reason
):
    before = snapshot(templates_dir)
    response = client.put(f"/api/harnesses/profiles/{pid}", json=body)
    assert response.status_code == 422
    assert reason in response.json()["detail"]
    assert snapshot(templates_dir) == before


@pytest.mark.parametrize(
    "body, reason",
    [
        ({"provider": "fake", "enabled": False, "defaults": {"model": "sonnet"}}, "disabled"),
        # Declared by the provider, so the loader takes it, but no launch applies it.
        ({"provider": "fake", "defaults": {"autocompact": "on"}}, "does not apply"),
    ],
    ids=["disabled", "unapplied-default"],
)
def test_a_save_that_stops_a_chain_launching_is_refused(client, templates_dir, body, reason):
    before = snapshot(templates_dir)
    response = client.put("/api/harnesses/profiles/claude", json=body)
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert reason in detail
    assert "chain 'default'" in detail
    assert snapshot(templates_dir) == before


@pytest.mark.api_client(edit_templates=_max_effort_chain)
def test_a_provider_change_the_selecting_task_cannot_run_on_is_refused(client, templates_dir):
    """codex takes no `effort: max`, which `max-effort`'s task asks for."""
    before = snapshot(templates_dir)
    response = client.put("/api/harnesses/profiles/claude", json={"provider": "codex"})
    assert response.status_code == 422
    assert "max-effort" in response.json()["detail"]
    assert "'max'" in response.json()["detail"]
    assert snapshot(templates_dir) == before


@pytest.mark.api_client(edit_templates=_chain_on_missing_profile)
def test_a_chain_already_unlaunchable_does_not_block_an_unrelated_save(client):
    """`orphan` selects a profile that does not exist before the edit or after
    it: the edit is not what broke it -- though adding a profile changes how
    that failure reads ("known are [...]")."""
    response = client.put("/api/harnesses/profiles/gem", json={"provider": "gemini"})
    assert response.status_code == 200, response.text


def test_a_save_over_a_file_that_does_not_parse_is_refused(client, templates_dir):
    (templates_dir / "harnesses.yaml").write_text("harnesses: [unclosed\n")
    before = snapshot(templates_dir)
    response = client.put("/api/harnesses/profiles/codex", json={"provider": "codex"})
    assert response.status_code == 409
    assert "by hand" in response.json()["detail"]
    assert snapshot(templates_dir) == before


def _chain_with_a_fallback(templates_dir):
    """`fb`'s task runs on claude and falls back to codex at `effort: max`."""
    _max_effort_chain(templates_dir)
    task = {
        "id": "t",
        "kind": "agent",
        "harness": "claude",
        "prompt": "p",
        "fallback": [{"harness": "codex", "effort": "max"}],
    }
    chain = {"id": "fb", "nodes": [{"id": "run", "kind": "exec", "tasks": [task]}]}
    (templates_dir / "chains" / "fb.yaml").write_text(yaml.safe_dump(chain))


@pytest.mark.api_client(edit_templates=_chain_with_a_fallback)
def test_a_save_a_fallback_entry_cannot_pair_with_is_refused_naming_the_entry(
    client, templates_dir
):
    before = snapshot(templates_dir)
    response = client.put("/api/harnesses/profiles/codex", json={"provider": "codex"})
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "chain 'fb' task 'run.main.t': fallback entry 0 (the task's list)" in detail
    assert "takes no effort 'max'" in detail
    assert snapshot(templates_dir) == before


@pytest.mark.api_client(edit_templates=_chain_with_a_fallback)
def test_disabling_a_fallback_harness_is_not_a_pairing_problem(client):
    """A disabled entry is skipped at launch (`unavailable-candidate-falls-back-
    to-next`), so turning a fallback harness off breaks no chain."""
    response = client.put(
        "/api/harnesses/profiles/codex", json={"provider": "fake", "enabled": False}
    )
    assert response.status_code == 200, response.text
