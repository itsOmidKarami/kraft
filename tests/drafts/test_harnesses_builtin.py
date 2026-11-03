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
