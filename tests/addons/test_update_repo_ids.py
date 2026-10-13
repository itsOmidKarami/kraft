"""`addons.update` and connected repository ids: a repository's id is a
reserved namespace from connect onward, so an add-on's skill may not name it."""

import pytest
from support.addons import AGENT, instance, make_collection

from kraft.addons import update
from kraft.config import write_yaml


@pytest.mark.parametrize(
    "connected, outcome",
    [
        pytest.param("app", "refused", id="connected-id"),
        pytest.param("web", "applied", id="unconnected-name"),
    ],
)
def test_an_addon_skill_may_not_name_a_connected_repository(tmp_path, connected, outcome):
    task = {**AGENT, "skill": "app:notes"}
    collection = make_collection(tmp_path, {"tools": {"library": {"tasks": {"t": task}}}})
    config_dir, addons_dir = instance(tmp_path, {"acme": {"git": collection.as_uri()}})
    write_yaml(config_dir / "repos.yaml", {"repos": [{"path": "/r", "id": connected}]})

    (result,) = update.update(
        ["tools@acme"],
        accept=lambda review: True,
        entries={"tools@acme": True},
        config_dir=config_dir,
        addons_root=addons_dir,
    )

    assert result.outcome == outcome, result.problems
    if outcome == "refused":
        assert any("repository app's repo library namespace" in p for p in result.problems)
