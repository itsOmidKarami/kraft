"""The Chains screen's routes on Template Schema V1: list the saved chains,
read one as its author wrote it, save it only if the library still resolves it,
and reload the library from disk."""

from __future__ import annotations

import asyncio

import pytest
import yaml
from support.api import _client
from support.harness import fake_templates_dir

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
    r = client.put("/api/templates/scratch", json={"text": dangling})
    assert r.status_code == 422
    assert "nope" in r.json()["detail"]
    assert not (templates_dir / "chains" / "scratch.yaml").exists()

    assert client.put("/api/templates/scratch", json={"text": SCRATCH}).status_code == 200
    # Written verbatim, into chains/, and live without a restart.
    assert (templates_dir / "chains" / "scratch.yaml").read_text() == SCRATCH
    assert any(t["id"] == "scratch" for t in client.get("/api/templates").json())
    got = client.get("/api/templates/scratch").json()
    assert got["text"] == SCRATCH
    assert got["chain"]["nodes"][0]["id"] == "run"
    assert client.get("/api/templates/nope").status_code == 404


def test_template_put_refuses_an_id_that_is_not_the_files(client, templates_dir):
    assert client.put("/api/templates/other", json={"text": SCRATCH}).status_code == 422
    assert client.put("/api/templates/Bad..id", json={"text": SCRATCH}).status_code == 400
    assert not (templates_dir / "chains" / "other.yaml").exists()


def test_a_saved_chain_survives_a_get_then_put_round_trip(client, templates_dir):
    """Open the page, save: an untouched round trip is byte-for-byte a no-op,
    comments and layout included."""
    before = (templates_dir / "chains" / "default.yaml").read_text()
    text = client.get("/api/templates/default").json()["text"]
    assert client.put("/api/templates/default", json={"text": text}).status_code == 200
    assert (templates_dir / "chains" / "default.yaml").read_text() == before


def test_reload_picks_up_a_chain_added_on_disk_without_a_restart(client, templates_dir):
    assert not any(t["id"] == "scratch" for t in client.get("/api/templates").json())
    (templates_dir / "chains" / "scratch.yaml").write_text(SCRATCH)

    r = client.post("/api/templates/reload")
    assert r.status_code == 200
    assert "scratch" in r.json()["valid"]
    assert r.json()["invalid_templates"] == {}
    assert any(t["id"] == "scratch" for t in client.get("/api/templates").json())


def test_reload_of_a_broken_library_reports_it_and_degrades(client, templates_dir):
    (templates_dir / "library.yaml").write_text("tasks: [unclosed\n")

    r = client.post("/api/templates/reload")
    assert r.status_code == 200
    assert r.json()["valid"] == []
    assert "library.yaml" in r.json()["invalid_templates"]
    assert client.get("/api/health").json()["status"] == "degraded"


def test_templates_lists_the_v1_chains_intake_materializes(tmp_path, monkeypatch):
    """Kraft-pplyo: the intake preview reads this list, so its nodes are the V1
    chain's own, in `ChainNode` shape, and a chain that does not resolve is
    listed with its error rather than hidden from the screen that fixes it."""
    templates = fake_templates_dir(tmp_path, "true")
    (templates / "chains" / "broken.yaml").write_text(
        yaml.safe_dump({"id": "broken", "nodes": [{"id": "n", "extends": "no_such_node"}]})
    )
    with _client(tmp_path, monkeypatch, templates_dir=templates, default_setup=False) as client:
        got = {t["id"]: t for t in client.get("/api/templates").json()}

    assert list(got) == sorted(got)
    default = TemplateLibrary.from_yaml_dir(templates).resolve_chain("default")
    assert got["default"]["nodes"] == [store.node_view(n) for n in default.nodes]
    assert got["default"]["gates"] == 4
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
