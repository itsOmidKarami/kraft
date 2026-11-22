"""The reply limits are limits, so an add-on's `policy:` may set one (Part 2, item 3a1)."""

import pytest
import yaml
from support.addons import AGENT, addon_json, chain

from kraft.addons import manifest, update


@pytest.mark.parametrize(
    "key",
    ["reply_timeout_minutes", "reply_cap_per_thread", "reply_rounds_per_head"],
    ids=["reply-timeout", "reply-cap", "reply-rounds"],
)
def test_an_addon_may_set_a_reply_limit(key):
    data = addon_json("release", requires={"kraft": "2", "harnesses": ["codex"]})
    library = {"tasks": {"base": {**AGENT, "policy": {key: 3}}}}
    files = {
        "library.yaml": ("100644", yaml.safe_dump(library).encode()),
        "chains/ship.yaml": ("100644", yaml.safe_dump(chain("base")).encode()),
    }

    assert update.problems(manifest.addon(data, "addon.json"), files) == []
