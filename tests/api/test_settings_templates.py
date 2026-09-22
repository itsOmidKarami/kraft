"""The Chains screen's routes on Template Schema V1: list the saved chains,
read one as its author wrote it, save it only if the library still resolves it,
and reload the library from disk."""

from __future__ import annotations

import asyncio

import pytest
import yaml
from support.api import _client
from support.harness import connect_repo, fake_templates_dir

from kraft import store
from kraft.templates.library import TemplateLibrary

#: No default repo entry for an unconnected repo (`support.api._client`): these read real config.
pytestmark = pytest.mark.api_client(default_setup=False)

#: A chain file as an operator writes one: a comment, one subprocess task.
SCRATCH = """# my chain -- the comment survives a save
id: scratch
nodes:
  - id: run
    kind: exec
    tasks:
      - {id: t, kind: subprocess, command: "true"}
"""


def test_template_put_validates_before_it_writes(client, templates_dir):
    dangling = SCRATCH.replace(
        '{id: t, kind: subprocess, command: "true"}', "{id: t, extends: nope}"
    )
    r = client.put("/api/templates/chains/scratch", json={"text": dangling})
    assert r.status_code == 422
    assert "nope" in r.json()["detail"]
    assert not (templates_dir / "chains" / "scratch.yaml").exists()

    assert client.put("/api/templates/chains/scratch", json={"text": SCRATCH}).status_code == 200
    # Written verbatim, into chains/, and live without a restart.
    assert (templates_dir / "chains" / "scratch.yaml").read_text() == SCRATCH
    assert any(t["id"] == "scratch" for t in client.get("/api/templates/chains").json())
    got = client.get("/api/templates/chains/scratch").json()
    assert got["text"] == SCRATCH
    assert got["chain"]["nodes"][0]["id"] == "run"
    assert client.get("/api/templates/chains/nope").status_code == 404


@pytest.mark.parametrize("tid", ["library", "lint", "resolve", "parse", "reload"])
def test_a_chain_may_take_the_name_of_a_templates_route(client, templates_dir, tid):
    """Ruling 204: chains live under `/templates/chains/`, so no id shadows the
    library or an inspection route, and none needs reserving."""
    text = SCRATCH.replace("id: scratch", f"id: {tid}")
    assert client.put(f"/api/templates/chains/{tid}", json={"text": text}).status_code == 200
    assert (templates_dir / "chains" / f"{tid}.yaml").read_text() == text
    assert client.get(f"/api/templates/chains/{tid}").json()["text"] == text
    assert client.get(f"/api/templates/chains/{tid}/resolved").json()["id"] == tid
    assert tid in {t["id"] for t in client.get("/api/templates/chains").json()}


def test_the_pre_ruling_204_chain_paths_are_gone(client):
    """No compatibility alias: a chain is not reachable at its old path."""
    assert client.get("/api/templates").status_code in (404, 405)
    assert client.get("/api/templates/default").status_code == 404
    assert client.get("/api/templates/default/resolved").status_code == 404


def test_template_put_refuses_an_id_that_is_not_the_files(client, templates_dir):
    assert client.put("/api/templates/chains/other", json={"text": SCRATCH}).status_code == 422
    assert client.put("/api/templates/chains/Bad..id", json={"text": SCRATCH}).status_code == 400
    assert not (templates_dir / "chains" / "other.yaml").exists()


def test_a_saved_chain_survives_a_get_then_put_round_trip(client, templates_dir):
    """Open the page, save: an untouched round trip is byte-for-byte a no-op,
    comments and layout included."""
    before = (templates_dir / "chains" / "default.yaml").read_text()
    text = client.get("/api/templates/chains/default").json()["text"]
    assert client.put("/api/templates/chains/default", json={"text": text}).status_code == 200
    assert (templates_dir / "chains" / "default.yaml").read_text() == before


def test_reload_picks_up_a_chain_added_on_disk_without_a_restart(client, templates_dir):
    assert not any(t["id"] == "scratch" for t in client.get("/api/templates/chains").json())
    (templates_dir / "chains" / "scratch.yaml").write_text(SCRATCH)

    r = client.post("/api/templates/reload")
    assert r.status_code == 200
    assert "scratch" in r.json()["valid"]
    assert r.json()["invalid_templates"] == {}
    assert any(t["id"] == "scratch" for t in client.get("/api/templates/chains").json())


def test_reload_of_a_broken_library_reports_it_and_degrades(client, templates_dir):
    (templates_dir / "library.yaml").write_text("tasks: [unclosed\n")

    r = client.post("/api/templates/reload")
    assert r.status_code == 200
    assert r.json()["valid"] == []
    assert "library.yaml" in r.json()["invalid_templates"]
    assert client.get("/api/health").json()["status"] == "degraded"


def test_reload_applies_a_hand_edited_policy(client, templates_dir):
    """Kraft-m86uq: reload rereads policy.yaml too, validated as at startup,
    so a hand edit needs no restart."""
    path = templates_dir / "policy.yaml"
    policy = yaml.safe_load(path.read_text())
    policy["max_concurrent"] = 7
    policy["maxima"] = {"max_attempts": 4}
    path.write_text(yaml.safe_dump(policy))

    r = client.post("/api/templates/reload").json()

    assert r["refused_policy"] is None
    assert client.app.state.policy.max_concurrent == 7
    assert client.app.state.instance_policy.maxima.max_attempts == 4


def test_reload_refuses_a_bad_policy_and_keeps_the_running_one(client, templates_dir):
    running = client.app.state.policy
    (templates_dir / "policy.yaml").write_text("default: [unclosed\n")

    r = client.post("/api/templates/reload").json()

    assert "policy.yaml" in r["refused_policy"]
    assert client.app.state.policy is running
    assert client.app.state.invalid_policy == []
    assert client.get("/api/health").json()["status"] == "ok"


def test_templates_lists_the_v1_chains_intake_materializes(tmp_path, monkeypatch):
    """Kraft-pplyo: the intake preview reads this list, so its nodes are the V1
    chain's own, in `ChainNode` shape, and a chain that does not resolve is
    listed with its error rather than hidden from the screen that fixes it."""
    templates = fake_templates_dir(tmp_path, "true")
    (templates / "chains" / "broken.yaml").write_text(
        yaml.safe_dump({"id": "broken", "nodes": [{"id": "n", "extends": "no_such_node"}]})
    )
    with _client(tmp_path, monkeypatch, templates_dir=templates, default_setup=False) as client:
        got = {t["id"]: t for t in client.get("/api/templates/chains").json()}

    assert list(got) == sorted(got)
    default = TemplateLibrary.from_yaml_dir(templates).resolve_chain("default")
    assert got["default"]["nodes"] == [store.node_view(n) for n in default.nodes]
    # spec, plan, chain revision (Kraft-oydes), local review, chain review.
    assert got["default"]["gates"] == 5
    assert got["default"]["error"] is None
    assert got["broken"]["nodes"] == []
    assert "no_such_node" in got["broken"]["error"]


def test_parse_template_yaml_returns_the_mapping(client):
    body = client.post("/api/templates/parse", json={"text": SCRATCH}).json()
    assert body["error"] is None
    assert body["chain"]["id"] == "scratch"


def test_parse_template_yaml_reports_a_syntax_error(client):
    body = client.post("/api/templates/parse", json={"text": "nodes: [unterminated"}).json()
    assert body["chain"] is None
    assert body["error"]


def test_parse_template_yaml_reports_a_shape_error(client):
    body = client.post("/api/templates/parse", json={"text": "- just\n- a list\n"}).json()
    assert body["chain"] is None
    assert "mapping" in body["error"]


def _broken_chain_file(tdir):
    (tdir / "chains" / "broken.yaml").write_text("id: broken\nnodes: [ unclosed\n")


@pytest.mark.api_client(edit_templates=_broken_chain_file)
def test_one_unparseable_chain_file_degrades_the_instance_instead_of_lying(client, repo):
    """`TemplateLibrary.from_yaml_dir` raises on any one bad file, so a single
    malformed `chains/*.yaml` leaves no library at all and every chain id
    unresolvable. Answering 422 "unknown or invalid template" then tells the
    operator their chain id is wrong when the truth is that one file does not
    parse -- and that is the one thing a person reading it will act on.

    503 naming the file instead, the same posture `invalid_policy` already has,
    and `/health` says `degraded` so a monitor sees it without anyone filing a
    work item first.
    """
    health = client.get("/api/health").json()
    assert health["status"] == "degraded"
    assert "broken.yaml" in health["invalid_templates"].get("library.yaml", "")

    r = client.post(
        "/api/work-items",
        json={"title": "t", "repo": str(repo), "chain_template": "default"},
    )
    assert r.status_code == 503, r.text
    assert "broken.yaml" in r.json()["detail"]
    # And not the misleading answer: the chain id it named is a real one.
    assert "unknown or invalid template" not in r.json()["detail"]


def test_every_door_names_the_broken_file_rather_than_the_chain_id(client, repo, caplog):
    """N4. Two of four doors named the file; the other two said "unknown chain
    template", which sends the operator to look at a chain id that is fine.

    `PATCH` 404'd *before* reaching the 503, because its `chain_ids` membership
    check ran first -- and with no library there is nothing to be a member of,
    so every id was "unknown". The two background doors (auto-intake, cron
    triggers) logged the same misleading line.
    """
    from types import SimpleNamespace

    from kraft import intake as intake_mod
    from kraft import triggers as triggers_mod

    connect_repo(repo)
    # An item filed while the library was still readable -- the state a
    # PATCH arrives in after an operator hand-edits a chain file badly.
    wid = client.post(
        "/api/work-items",
        json={"title": "t", "repo": str(repo), "chain_template": "default", "autostart": False},
    ).json()["id"]

    # Now the library does not parse.
    client.app.state.library = None
    client.app.state.invalid_library = ["chains/broken.yaml: cannot read/parse"]

    # The create door.
    created = client.post(
        "/api/work-items",
        json={"title": "t", "repo": str(repo), "chain_template": "default"},
    )
    assert created.status_code == 503 and "broken.yaml" in created.json()["detail"]

    # The PATCH door: it used to 404 "unknown chain template 'default'",
    # because the `chain_ids` membership check ran before the 503 and with no
    # library there is nothing to be a member of.
    r = client.patch(f"/api/work-items/{wid}", json={"chain_template": "default"})
    assert r.status_code == 503, r.text
    assert "broken.yaml" in r.json()["detail"]

    # An unknown *work item* is still a 404, ahead of everything.
    assert (
        client.patch("/api/work-items/nope", json={"chain_template": "default"}).status_code == 404
    )

    # The two background doors: a bare state is enough, because both guards run
    # before either touches the database.
    state = SimpleNamespace(
        library=None,
        invalid_library=["chains/broken.yaml: cannot read/parse: while parsing a flow sequence"],
        policy=SimpleNamespace(triggers=[SimpleNamespace(cron="* * * * *", chain="default")]),
        trigger_last_fired={},
    )
    app = SimpleNamespace(state=state)
    with caplog.at_level("WARNING"):
        assert asyncio.run(intake_mod._start(app, {"path": "/r"}, {"id": "B"})) is None
        assert asyncio.run(triggers_mod.tick(app)) == []
    # Scoped to the two background loggers: `deps.load_library` also names the
    # file when it first fails to read it, which is a third, correct mention.
    named = {
        r.name
        for r in caplog.records
        if "broken.yaml" in r.getMessage() and r.name in ("kraft.intake", "kraft.triggers")
    }
    assert named == {"kraft.intake", "kraft.triggers"}, caplog.text
    assert not any("unknown chain template" in r.getMessage() for r in caplog.records), caplog.text


def _unresolvable_chain(tdir):
    """Parses, so the library loads -- and does not resolve."""
    (tdir / "chains" / "broken.yaml").write_text(
        yaml.safe_dump({"id": "broken", "nodes": [{"id": "n", "extends": "no_such_node"}]})
    )


@pytest.mark.api_client(edit_templates=_unresolvable_chain)
def test_a_chain_that_does_not_resolve_is_visible_everywhere(client, repo):
    """Kraft-n1zp9: the library loads, so nothing used to say the one chain is
    broken until an intake on it got a bare "unknown or invalid template".
    `/health` degrades naming the chain, reload reports it, intake on it carries
    the resolver's own message -- and every other chain still runs."""
    health = client.get("/api/health").json()
    assert health["status"] == "degraded"
    assert "no_such_node" in health["invalid_templates"]["chain broken"]

    reload = client.post("/api/templates/reload").json()
    assert "no_such_node" in reload["invalid_templates"]["chain broken"]
    assert "broken" not in reload["valid"] and "default" in reload["valid"]

    connect_repo(repo)
    r = client.post(
        "/api/work-items", json={"title": "t", "repo": str(repo), "chain_template": "broken"}
    )
    assert r.status_code == 422, r.text
    assert "no_such_node" in r.json()["detail"]

    ok = client.post(
        "/api/work-items",
        json={"title": "t", "repo": str(repo), "chain_template": "default", "autostart": False},
    )
    assert ok.status_code == 201, ok.text


def test_reload_notices_a_chain_that_stopped_resolving(client, templates_dir):
    assert client.get("/api/health").json()["invalid_templates"] == {}
    _unresolvable_chain(templates_dir)
    assert "chain broken" in client.post("/api/templates/reload").json()["invalid_templates"]
    assert client.get("/api/health").json()["status"] == "degraded"


def _capped_chain(tdir):
    """Resolves under the default policy; past a `maxima: {max_attempts: 2}`."""
    sub = {"kind": "subprocess", "command": "true"}
    node = {
        "id": "n",
        "kind": "exec",
        "tasks": [{"id": "t", **sub}],
        "fix_loop": {"tasks": [{"id": "f", **sub}], "max_attempts": 5},
    }
    (tdir / "chains" / "capped.yaml").write_text(yaml.safe_dump({"id": "capped", "nodes": [node]}))


@pytest.mark.api_client(edit_templates=_capped_chain)
def test_a_policy_save_that_strands_a_chain_degrades_health(client):
    """A chain past a `maxima:` ceiling is one of lint's issues, so a
    `policy.yaml` save that lowers the ceiling re-lints what is loaded."""
    assert client.get("/api/health").json()["invalid_templates"] == {}
    body = client.get("/api/policy").json()
    body["maxima"] = {"max_attempts": 2}
    assert client.put("/api/policy", json=body).status_code == 200
    invalid = client.get("/api/health").json()["invalid_templates"]
    assert "max_attempts" in invalid["chain capped"]


#: A chain saved with a wait's timeout where Ruling 196 retired it.
RETIRED_WAIT = """id: scratch
nodes:
  - id: feedback
    kind: exec
    tasks:
      - {id: ci, kind: forge, target: mr.ci, wait: {timeout: 90m}}
"""


def test_a_chain_saved_with_a_retired_wait_timeout_is_refused_naming_its_replacement(
    client, templates_dir
):
    """It still reads from a file already on disk (Ruling 196); a save that
    writes one is refused, so no new file carries it."""
    refused = client.put("/api/templates/chains/scratch", json={"text": RETIRED_WAIT})

    assert refused.status_code == 422
    assert "nodes[0].tasks[0].wait.timeout is retired" in refused.json()["detail"]
    assert "total_time_cap_minutes" in refused.json()["detail"]
    assert not (templates_dir / "chains" / "scratch.yaml").exists()


def test_a_policy_save_writes_a_retired_wait_maximum_under_its_new_name(client, templates_dir):
    body = client.get("/api/policy").json()
    body["maxima"] = {"wait_timeout_minutes": 600}

    assert client.put("/api/policy", json=body).status_code == 200

    saved = yaml.safe_load((templates_dir / "policy.yaml").read_text())["maxima"]
    assert saved == {"tasks": {"total_time_cap_minutes": 600}}
    assert client.app.state.instance_policy.maxima.tasks.total_time_cap_minutes == 600
