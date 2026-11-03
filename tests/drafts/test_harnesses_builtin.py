"""Settings > Harnesses on a home whose `harnesses.yaml` lacks the profiles the
built-in needs: the package supplies them, labelled in the view only."""

from __future__ import annotations

import pytest
import yaml

from .test_harnesses import URL, access, ops

pytestmark = pytest.mark.api_client(default_setup=False)


def test_settings_labels_packaged_profiles(client, templates_dir):
    path = templates_dir / "harnesses.yaml"
    data = yaml.safe_load(path.read_text())
    data.pop("profiles")
    data["harnesses"]["claude"]["provider"] = "claude"  # the packaged tiers name real providers
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    client.post("/api/templates/reload")

    resolved = ops(client, *access({"codex": "override"})).json()["result"]["resolved"]
    assert resolved["profiles"]["strong"]["source"] == "package"

    refused = ops(client, {"op": "remove_profile", "name": "strong"})
    assert refused.status_code == 422
    assert refused.json()["detail"] == (
        "strong comes from Kraft's package; define it in harnesses.yaml to change it"
    )

    r = client.post(f"{URL}/publish")
    assert r.status_code == 200, r.text
    assert "strong" not in (yaml.safe_load(path.read_text()).get("profiles") or {})


MESSAGE = "{} comes from Kraft's package; define it in harnesses.yaml to change it"


def _only_b(client, templates_dir):
    """A harnesses.yaml with one harness and no profiles: the package fills the rest."""
    (templates_dir / "harnesses.yaml").write_text(
        yaml.safe_dump({"harnesses": {"b": {"provider": "codex"}}})
    )
    client.post("/api/templates/reload")


@pytest.mark.parametrize(
    "op",
    [
        {"op": "set_profile", "name": "strong", "patch": {"effort": "low"}},
        {"op": "rename_profile", "name": "strong", "to": "stronger"},
        {"op": "remove_profile", "name": "strong"},
        {"op": "add_profile", "name": "mine", "copy_from": "strong"},
    ],
    ids=["set", "rename", "remove", "copy"],
)
def test_every_op_on_a_packaged_profile_is_refused(client, templates_dir, op):
    _only_b(client, templates_dir)
    refused = ops(client, op)
    assert refused.status_code == 422
    assert refused.json()["detail"] == MESSAGE.format("strong")


def test_a_packaged_harness_is_labelled_and_refuses_edits(client, templates_dir):
    _only_b(client, templates_dir)
    listed = {p["id"]: p for p in client.get("/api/harnesses/profiles").json()["profiles"]}
    assert listed["claude"]["source"] == "package"
    assert "source" not in listed["b"]

    resolved = ops(client).json()["result"]["resolved"]
    seen = {h["id"]: h for h in resolved["harnesses"]}
    assert seen["claude"]["source"] == "package"
    assert "source" not in seen["b"]

    for op in (
        {"op": "set_harness", "id": "claude", "patch": {"enabled": False}},
        *access({"claude": "never"}),
    ):
        refused = ops(client, op)
        assert refused.status_code == 422
        assert refused.json()["detail"] == MESSAGE.format("claude")
