"""The shared add-on flow (spec §4.3): what `update` makes of the updater's
refusals, for both front doors."""

from __future__ import annotations

import pytest

from kraft.addons import flow
from kraft.addons import update as addon_update
from kraft.config import ConfigError
from kraft.policy import PolicyError


@pytest.mark.parametrize(
    ("raised", "said"),
    [
        (
            addon_update.Refused(["a.yaml: x is bad", "b.yaml: y is bad"]),
            "a.yaml: x is bad; b.yaml: y is bad",
        ),
        (ConfigError("addons.yaml is broken"), "addons.yaml is broken"),
        (PolicyError("policy says no"), "policy says no"),
        (ConfigError("https://user:secret@host/x failed"), "https://host/x failed"),
    ],
    ids=["several-refused", "config", "policy", "redacted"],
)
def test_update_says_every_refusal_as_one_refusal(monkeypatch, raised, said):
    def update(*args, **kwargs):
        raise raised

    monkeypatch.setattr(addon_update, "update", update)
    with pytest.raises(flow.Refusal) as caught:
        flow.update(None, lambda _: False)
    assert str(caught.value) == said
