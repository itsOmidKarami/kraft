"""The config draft routes: a draft is kept server-side, published over the
files only when none changed since it began and it has no problem, and
discarded for good."""

from __future__ import annotations

import pytest
import yaml

pytestmark = pytest.mark.api_client(default_setup=False)

DEFAULT = "/api/drafts/chains/default"
SCRATCH = """# mine
id: scratch
nodes:
  - id: run
    kind: exec
    tasks:
      - {id: t, kind: subprocess, command: "true"}
"""


def put(client, key, text):
    return client.put(f"/api/drafts/chains/{key}/files/chains/{key}.yaml", json={"text": text})


def test_get_answers_the_published_files_until_a_put_makes_a_draft(client, templates_dir):
    shipped = (templates_dir / "chains" / "default.yaml").read_text()
    got = client.get(DEFAULT).json()
    assert (got["draft"], got["files"]) == (False, {"chains/default.yaml": shipped})
    assert got["result"]["resolved"]["id"] == "default"
    assert got["result"]["problems"] == got["result"]["changes"] == []

    edited = shipped.replace("Review and approve the specification.", "Read the spec.")
    r = put(client, "default", edited)
    assert r.status_code == 200
    assert r.json()["draft"] is True
    assert r.json()["result"]["changes"] == [
        {"path": "spec_approval", "kind": "change", "summary": "message", "fields": ["message"]}
    ]
    assert client.get(DEFAULT).json()["files"] == {"chains/default.yaml": edited}
    [listed] = client.get("/api/drafts").json()
    assert (listed["key"], listed["files"], listed["changes"]) == (
        "default",
        ["chains/default.yaml"],
        1,
    )
    # Nothing on disk until a publish.
    assert (templates_dir / "chains" / "default.yaml").read_text() == shipped


def test_publish_writes_the_files_reloads_and_drops_the_draft(client, templates_dir):
    assert client.get("/api/drafts/chains/scratch").status_code == 404
    put(client, "scratch", SCRATCH)

    r = client.post("/api/drafts/chains/scratch/publish")
    assert r.status_code == 200
    assert r.json()["published"] == ["chains/scratch.yaml"]
    assert (templates_dir / "chains" / "scratch.yaml").read_text() == SCRATCH
    assert "scratch" in {t["id"] for t in client.get("/api/templates/chains").json()}
    assert client.get("/api/drafts").json() == []


def test_publish_over_a_file_saved_since_answers_409_with_the_diff(client, templates_dir):
    path = templates_dir / "chains" / "default.yaml"
    shipped = path.read_text()
    put(client, "default", shipped.replace("the implementation plan.", "the plan."))
    saved = shipped.replace("Review and approve the specification.", "Saved elsewhere.")
    assert client.put("/api/templates/chains/default", json={"text": saved}).status_code == 200

    r = client.post(f"{DEFAULT}/publish")
    assert r.status_code == 409
    diff = r.json()["files"]["chains/default.yaml"]
    assert diff["published"] == saved
    assert "-    message: Saved elsewhere.\n" in diff["diff"]
    assert path.read_text() == saved
    assert client.get(DEFAULT).json()["draft"] is True


def test_rebase_after_a_409_lets_the_next_publish_overwrite(client, templates_dir):
    path = templates_dir / "chains" / "default.yaml"
    shipped = path.read_text()
    mine = shipped.replace("the implementation plan.", "the plan.")
    put(client, "default", mine)
    saved = shipped.replace("Review and approve the specification.", "Saved elsewhere.")
    assert client.put("/api/templates/chains/default", json={"text": saved}).status_code == 200
    assert client.post(f"{DEFAULT}/publish").status_code == 409

    r = client.post(f"{DEFAULT}/rebase")
    assert r.status_code == 200
    assert r.json()["files"] == {"chains/default.yaml": mine}
    assert path.read_text() == saved  # rebase writes nothing
    assert client.post(f"{DEFAULT}/publish").status_code == 200
    assert path.read_text() == mine


def test_rebase_keeps_the_draft_text_and_pushes_no_undo_entry(client, templates_dir):
    shipped = (templates_dir / "chains" / "default.yaml").read_text()
    mine = shipped.replace("the implementation plan.", "the plan.")
    put(client, "default", mine)
    client.put("/api/templates/chains/default", json={"text": shipped + "\n"})
    assert client.post(f"{DEFAULT}/rebase").json()["files"] == {"chains/default.yaml": mine}
    # One entry (the PUT's): a second would leave the draft after this undo.
    assert client.post(f"{DEFAULT}/undo").json()["draft"] is False


def test_rebase_without_a_draft_is_404(client):
    assert client.post(f"{DEFAULT}/rebase").status_code == 404


def test_fragment_is_the_components_yaml_from_the_draft_else_the_published_file(client):
    published = client.get(f"{DEFAULT}/fragment", params={"path": "spec"})
    assert published.status_code == 200
    assert published.json()["path"] == "spec"
    assert yaml.safe_load(published.json()["text"])["id"] == "spec"

    shipped = client.get(DEFAULT).json()["files"]["chains/default.yaml"]
    put(client, "default", shipped.replace("Review and approve the specification.", "Read it."))
    drafted = client.get(f"{DEFAULT}/fragment", params={"path": "spec_approval"}).json()["text"]
    assert "Read it." in drafted
    # What `set_fragment` writes back is what this serves.
    r = post_ops(client, {"op": "set_fragment", "path": "spec_approval", "yaml": drafted})
    assert r.status_code == 200
    assert (
        client.get(f"{DEFAULT}/fragment", params={"path": "spec_approval"}).json()["text"]
        == drafted
    )


def test_fragment_of_an_unknown_path_is_404_and_a_missing_path_is_400(client):
    assert client.get(f"{DEFAULT}/fragment", params={"path": "nope"}).status_code == 404
    assert client.get(f"{DEFAULT}/fragment").status_code == 400


def test_publish_with_a_problem_answers_422_and_writes_nothing(client, templates_dir):
    put(client, "scratch", SCRATCH.replace('command: "true"}', 'command: "true", bogus: 1}'))

    r = client.post("/api/drafts/chains/scratch/publish")
    assert r.status_code == 422
    assert [p["field"] for p in r.json()["problems"]] == ["bogus"]
    assert not (templates_dir / "chains" / "scratch.yaml").exists()


def test_a_library_draft_reports_the_chains_it_breaks_and_publishes(client, templates_dir):
    path = templates_dir / "library.yaml"
    shipped = path.read_text()
    broken = shipped.replace("  spec_author:", "  renamed_author:")
    problems = client.put(
        "/api/drafts/library/library/files/library.yaml", json={"text": broken}
    ).json()["result"]["problems"]
    assert {p["file"] for p in problems} == {"chains/default.yaml"}

    edited = shipped.replace("steering:\n", "steering:\n  terse: {instructions: Be brief.}\n", 1)
    client.put("/api/drafts/library/library/files/library.yaml", json={"text": edited})
    assert client.post("/api/drafts/library/library/publish").status_code == 200
    assert path.read_text() == edited


def test_discard_is_final(client):
    put(client, "scratch", SCRATCH)
    assert client.delete("/api/drafts/chains/scratch").status_code == 204
    assert client.delete("/api/drafts/chains/scratch").status_code == 404
    assert client.post("/api/drafts/chains/scratch/undo").status_code == 409


@pytest.mark.parametrize(
    ("method", "url", "status"),
    [
        ("get", "/api/drafts/nope/x", 404),
        ("get", "/api/drafts/harnesses/x", 400),
        ("get", "/api/drafts/intake/policy", 400),
        ("put", "/api/drafts/policy/policy/files/repos.yaml", 422),
        ("put", "/api/drafts/repos/repos/files/policy.yaml", 422),
        ("get", "/api/drafts/chains/Bad..id", 400),
        ("put", "/api/drafts/chains/default/files/library.yaml", 422),
        ("put", "/api/drafts/library/library/files/chains/default.yaml", 422),
    ],
    ids=[
        "unknown-area",
        "harnesses-key",
        "intake-key",
        "policy-area-other-file",
        "repos-area-other-file",
        "bad-chain-id",
        "chain-outside-file",
        "library-outside-file",
    ],
)
def test_a_request_outside_an_area_is_refused(client, method, url, status):
    body = {"json": {"text": "x: 1\n"}} if method == "put" else {}
    assert getattr(client, method)(url, **body).status_code == status


ADD_GATE = {"op": "add_node", "at": 0, "id": "first", "kind": "gate"}


def post_ops(client, *batch, key="default", query=""):
    return client.post(f"/api/drafts/chains/{key}/ops{query}", json={"ops": list(batch)})


def test_ops_are_one_undo_step_and_rewrite_the_file_whole(client, templates_dir):
    shipped = (templates_dir / "chains" / "default.yaml").read_text()
    r = post_ops(client, ADD_GATE, {"op": "add_node", "at": 1, "id": "second", "kind": "gate"})
    assert r.status_code == 200
    assert r.json()["ops"] == [{"op": "add_node"}, {"op": "add_node"}]
    assert [c["path"] for c in r.json()["result"]["changes"]] == ["first", "second"]
    # The shipped file has comments; the op wrote it without them.
    assert r.json()["result"]["warnings"] == [
        {"file": "chains/default.yaml", "message": "comments in this file will be dropped"}
    ]
    # A PUT is the text as typed: the file is no longer one an op wrote.
    text = r.json()["files"]["chains/default.yaml"]
    assert put(client, "default", text).json()["result"]["warnings"] == []

    client.post(f"{DEFAULT}/undo")
    undone = client.post(f"{DEFAULT}/undo").json()
    assert (undone["draft"], undone["files"]) == (False, {"chains/default.yaml": shipped})


def test_a_preview_saves_nothing(client):
    r = post_ops(client, ADD_GATE, query="?preview=1")
    assert [c["path"] for c in r.json()["result"]["changes"]] == ["first"]
    assert client.get(DEFAULT).json()["draft"] is False


def test_a_failing_op_answers_422_with_its_index_and_saves_nothing(client):
    r = post_ops(client, ADD_GATE, {"op": "add_node", "at": 0, "id": "first", "kind": "gate"})
    assert (r.status_code, r.json()) == (422, {"detail": "'first' is already taken here", "op": 1})
    assert client.get(DEFAULT).json()["draft"] is False


def test_ops_on_a_draft_with_a_yaml_error_answer_409(client):
    put(client, "default", "nodes: [\n")
    r = post_ops(client, ADD_GATE)
    assert (r.status_code, r.json()["detail"]) == (409, "fix the YAML first")


def test_new_chain_creates_a_key_that_had_neither_a_file_nor_a_draft(client):
    assert client.get("/api/drafts/chains/fresh").status_code == 404
    assert post_ops(client, {"op": "new_chain"}, ADD_GATE, key="fresh").status_code == 200
    got = client.get("/api/drafts/chains/fresh").json()
    assert got["draft"] is True
    assert got["result"]["model"]["chains/fresh.yaml"]["nodes"] == [{"id": "first", "kind": "gate"}]


def test_a_renamed_chain_keeps_its_draft_and_publishes_under_the_new_id(client, templates_dir):
    rename = {"op": "rename", "path": "", "id": "quick"}
    assert post_ops(client, rename, key="quick-task").status_code == 200
    # The next request edits the moved file, by op and as typed.
    assert post_ops(client, ADD_GATE, key="quick-task").status_code == 200
    text = client.get("/api/drafts/chains/quick-task").json()["files"]["chains/quick.yaml"]
    r = client.put("/api/drafts/chains/quick-task/files/chains/quick.yaml", json={"text": text})
    assert r.json()["result"]["resolved"]["id"] == "quick"

    assert client.post("/api/drafts/chains/quick-task/publish").status_code == 200
    assert not (templates_dir / "chains" / "quick-task.yaml").exists()
    assert (templates_dir / "chains" / "quick.yaml").read_text() == text


def test_a_fragment_yaml_error_answers_its_line_and_column(client):
    r = post_ops(client, {"op": "set_fragment", "path": "spec", "yaml": "id: spec\nkind: [\n"})
    assert (r.status_code, r.json()["line"], r.json()["col"]) == (422, 3, 1)


def test_a_move_to_library_publishes_the_chain_and_the_library_together(client, templates_dir):
    move = {"op": "move_to_library", "path": "spec", "name": "spec_node"}
    r = post_ops(client, move)
    assert sorted(r.json()["files"]) == ["chains/default.yaml", "library.yaml"]

    published = client.post(f"{DEFAULT}/publish").json()["published"]
    assert published == ["chains/default.yaml", "library.yaml"]
    library = yaml.safe_load((templates_dir / "library.yaml").read_text())
    assert library["nodes"]["spec_node"]["tasks"] == [{"id": "author", "extends": "spec_author"}]
    chain = yaml.safe_load((templates_dir / "chains" / "default.yaml").read_text())
    assert chain["nodes"][0] == {"id": "spec", "extends": "spec_node"}
    # Reloaded: the running library has the node and the chain uses it.
    assert client.get("/api/templates/library/nodes.spec_node").json()["used_by"] == ["default"]


def test_a_library_rename_joins_the_chains_it_rewrites_to_the_draft(client, templates_dir):
    library = "/api/drafts/library/library"
    rename = {"op": "rename", "path": "tasks.implementer", "id": "builder"}
    r = client.post(f"{library}/ops", json={"ops": [rename]})
    assert sorted(r.json()["files"]) == ["chains/quick-task.yaml", "library.yaml"]
    # Joined, the chain file is the draft's to write as typed.
    text = r.json()["files"]["chains/quick-task.yaml"]
    assert client.put(f"{library}/files/chains/quick-task.yaml", json={"text": text}).is_success

    assert client.post(f"{library}/publish").status_code == 200
    assert (templates_dir / "chains" / "quick-task.yaml").read_text() == text
    assert "extends: builder" in text


# ── the config-file areas (W13 B) ──

POLICY = "/api/drafts/policy/policy"


def put_file(client, area, file, text):
    return client.put(f"/api/drafts/{area}/{area}/files/{file}", json={"text": text})


def policy_text(templates_dir, old="rate_limit_retries: 5", new="rate_limit_retries: 6"):
    text = (templates_dir / "policy.yaml").read_text()
    assert old in text
    return text.replace(old, new)


@pytest.mark.parametrize(
    ("area", "file"),
    [
        ("harnesses", "harnesses.yaml"),
        ("harnesses", "policy.yaml"),
        ("repos", "repos.yaml"),
        ("policy", "policy.yaml"),
        ("intake", "intake.yaml"),
        ("intake", "policy.yaml"),
    ],
)
def test_each_config_area_keeps_a_draft_of_its_files(client, area, file):
    text = (
        "enabled: true\ninterval_s: 60\npriority_ceiling: 2\n"
        if file == "intake.yaml"
        else "x: 1\n"
    )
    got = put_file(client, area, file, text).json()
    assert (got["area"], got["draft"], got["files"][file]) == (area, True, text)
    assert [c["path"] for c in got["result"]["changes"]] == [file]
    assert client.get(f"/api/drafts/{area}/{area}").json()["files"][file] == text


def test_a_config_area_opens_before_its_file_exists(client):
    got = client.get("/api/drafts/repos/repos")
    assert got.status_code == 200
    assert (got.json()["draft"], got.json()["files"]) == (False, {})


def test_a_config_draft_shows_the_problems_the_put_route_would_refuse(client, templates_dir):
    shipped = (templates_dir / "policy.yaml").read_text()
    bad = put_file(client, "policy", "policy.yaml", shipped + "\nmax_concurrent: nope\n")
    problems = bad.json()["result"]["problems"]
    assert problems and problems[0]["file"] == "policy.yaml"
    r = client.post(f"{POLICY}/publish")
    assert (r.status_code, r.json()["problems"]) == (422, problems)
    assert (templates_dir / "policy.yaml").read_text() == shipped


def test_a_harnesses_draft_shows_a_problem_the_file_already_had(client, templates_dir):
    """`PUT /harnesses` only refuses what an edit newly breaks; a draft shows
    everything, so a profile that is already unusable blocks the publish."""
    path = templates_dir / "harnesses.yaml"
    path.write_text(path.read_text() + "\nprofiles:\n  broken: {model: {nosuch: x}}\n")
    got = put_file(client, "harnesses", "harnesses.yaml", path.read_text() + "\n# touched\n")
    assert [p["file"] for p in got.json()["result"]["problems"]] == ["harnesses.yaml"]


@pytest.fixture
def applied(client, monkeypatch):
    """What each area's publish applied: `reload_policy` and the intake restart."""
    from kraft import intake as intake_mod
    from kraft.api import deps

    calls = []
    real = deps.reload_policy

    def reload_policy(st):
        calls.append("policy")
        return real(st)

    async def restart(app):
        calls.append("intake")

    monkeypatch.setattr(deps, "reload_policy", reload_policy)
    monkeypatch.setattr(intake_mod, "restart", restart)
    return calls


def test_publishing_a_policy_draft_reloads_the_policy(client, templates_dir, applied):
    put_file(client, "policy", "policy.yaml", policy_text(templates_dir))
    assert client.post(f"{POLICY}/publish").status_code == 200
    assert applied == ["policy"]
    assert "rate_limit_retries: 6" in (templates_dir / "policy.yaml").read_text()
    assert client.app.state.policy.rate_limit_retries == 6


def test_publishing_an_intake_draft_restarts_the_poller(client, templates_dir, applied):
    text = "enabled: true\ninterval_s: 90\npriority_ceiling: 2\n"
    put_file(client, "intake", "intake.yaml", text)
    assert client.post("/api/drafts/intake/intake/publish").status_code == 200
    assert applied == ["intake"]
    assert (templates_dir / "intake.yaml").read_text() == text
    assert client.app.state.intake["interval_s"] == 90


def test_a_harnesses_draft_reloads_the_policy_only_when_it_wrote_policy_yaml(
    client, templates_dir, applied
):
    path = templates_dir / "harnesses.yaml"
    edited = path.read_text().replace("effort: low", "effort: medium")
    assert edited != path.read_text()
    put_file(client, "harnesses", "harnesses.yaml", edited)
    assert client.post("/api/drafts/harnesses/harnesses/publish").status_code == 200
    assert applied == []

    put_file(client, "harnesses", "policy.yaml", policy_text(templates_dir))
    assert client.post("/api/drafts/harnesses/harnesses/publish").status_code == 200
    assert applied == ["policy"]


def test_a_repos_draft_publishes_without_reloading_anything(client, templates_dir, applied):
    put_file(client, "repos", "repos.yaml", "repos: []\n")
    assert client.post("/api/drafts/repos/repos/publish").status_code == 200
    assert (templates_dir / "repos.yaml").read_text() == "repos: []\n"
    assert applied == []


def test_a_failing_apply_hook_answers_500_keeps_the_draft_and_the_retry_succeeds(
    client, templates_dir, monkeypatch
):
    from kraft.api import deps

    monkeypatch.setattr(deps, "reload_policy", lambda st: "refused")
    put_file(client, "policy", "policy.yaml", policy_text(templates_dir))
    r = client.post(f"{POLICY}/publish")
    assert r.status_code == 500
    assert "refused" in r.json()["detail"]
    assert "rate_limit_retries: 6" in (templates_dir / "policy.yaml").read_text()
    assert client.get(POLICY).json()["draft"] is True

    monkeypatch.undo()
    assert client.post(f"{POLICY}/publish").status_code == 200
    assert client.get(POLICY).json()["draft"] is False


def test_two_drafts_over_policy_yaml_the_second_publish_answers_409_and_writes_nothing(
    client, templates_dir
):
    put_file(client, "policy", "policy.yaml", policy_text(templates_dir))
    put_file(
        client, "harnesses", "policy.yaml", policy_text(templates_dir, new="rate_limit_retries: 7")
    )
    assert client.post(f"{POLICY}/publish").status_code == 200

    r = client.post("/api/drafts/harnesses/harnesses/publish")
    assert r.status_code == 409
    assert "rate_limit_retries: 7" in r.json()["files"]["policy.yaml"]["diff"]
    assert "rate_limit_retries: 6" in (templates_dir / "policy.yaml").read_text()
    # The way out: keep mine.
    assert client.post("/api/drafts/harnesses/harnesses/rebase").status_code == 200
    assert client.post("/api/drafts/harnesses/harnesses/publish").status_code == 200
    assert "rate_limit_retries: 7" in (templates_dir / "policy.yaml").read_text()
