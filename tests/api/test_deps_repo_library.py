"""`api.deps` and a connected repository's repo library: the health key of one
that did not load, and the chain an item with no chain runs."""

from __future__ import annotations

import json

import pytest
import yaml
from support.addons import AGENT, accept_repo_library, repo_library_files, repo_with_library
from support.harness import entry_of

from kraft.api import deps


def test_health_names_a_repo_library_that_did_not_load(client, tmp_path):
    """A repo library is keyed by its repository, not by an add-on id."""
    st = client.app.state
    accept_repo_library(st.templates_dir, st.run_dirs.addons, repo_with_library(tmp_path), "app")
    deps._reload_templates(st)
    assert "app:ship" in st.library.chain_ids
    assert client.get("/api/health").json()["status"] == "ok"

    (st.templates_dir / "harnesses.yaml").write_text(yaml.safe_dump({"harnesses": {}}))
    deps._reload_templates(st)

    body = client.get("/api/health").json()
    assert body["status"] == "degraded"
    assert "requires harness 'codex'" in body["invalid_templates"]["repo library app"]


def test_an_opted_in_repo_takes_a_text_change_when_the_server_checks(client, tmp_path):
    st = client.app.state
    repo = repo_with_library(tmp_path)
    accept_repo_library(st.templates_dir, st.run_dirs.addons, repo, "app")
    repos = yaml.safe_load((st.templates_dir / "repos.yaml").read_text())
    repos["repos"][0]["library_auto_update"] = True
    (st.templates_dir / "repos.yaml").write_text(yaml.safe_dump(repos))
    deps._reload_templates(st)
    repo_library_files(repo, library={"tasks": {"base": {**AGENT, "prompt": "new words"}}})

    client.portal.call(deps.auto_update_addons, client.app)

    chain = st.library.resolve_chain("app:ship").chain.model_dump(mode="json")
    assert "new words" in json.dumps(chain)


class _Library:
    def __init__(self, *chains):
        self.chain_ids = chains


@pytest.mark.parametrize(
    "entry, asked, chains, stored",
    [
        pytest.param({"id": "app"}, None, ("app:default",), "app:default", id="the-repos-default"),
        pytest.param(
            {"id": "app", "default_chain": "default"},
            None,
            ("app:default",),
            "app:default",
            id="default-counts-as-unset",
        ),
        pytest.param(
            {"id": "app", "default_chain": "quick"},
            None,
            ("app:default",),
            "quick",
            id="the-entrys-own-wins",
        ),
        pytest.param({"id": "app"}, "other", ("app:default",), "other", id="asked-wins"),
        pytest.param({"id": "app"}, None, ("app:ship",), None, id="library-without-default"),
        pytest.param({}, None, ("app:default",), None, id="no-id"),
        pytest.param({"id": "app"}, None, None, None, id="no-library"),
    ],
)
def test_which_chain_an_item_with_no_chain_runs(tmp_path, entry, asked, chains, stored):
    repo_entry = entry_of({"path": str(tmp_path), **entry})
    library = None if chains is None else _Library(*chains)

    assert deps.chain_template_for(repo_entry, asked, library) == stored
