"""Settings changed on an item, and a notification that failed."""

from __future__ import annotations

from enum import StrEnum

from kraft.vocab.events.traits import EventTraits
from kraft.vocab.total import total

__all__ = ["SettingsEvent", "SETTINGS_EVENT_TRAITS"]


class SettingsEvent(StrEnum):
    CHAIN_TEMPLATE_CHANGED = "chain_template_changed"
    ATTACHMENTS_CHANGED = "attachments_changed"
    AGENT_OVERRIDES_CHANGED = "agent_overrides_changed"
    NODE_OVERRIDES_CHANGED = "node_overrides_changed"
    POLICY_OVERRIDE_CHANGED = "policy_override_changed"
    NOTIFICATION_FAILED = "notification_failed"


_PLAIN = EventTraits()

SETTINGS_EVENT_TRAITS = total(
    SettingsEvent,
    {
        SettingsEvent.CHAIN_TEMPLATE_CHANGED: _PLAIN,
        SettingsEvent.ATTACHMENTS_CHANGED: _PLAIN,
        SettingsEvent.AGENT_OVERRIDES_CHANGED: _PLAIN,
        SettingsEvent.NODE_OVERRIDES_CHANGED: _PLAIN,
        SettingsEvent.POLICY_OVERRIDE_CHANGED: _PLAIN,
        SettingsEvent.NOTIFICATION_FAILED: _PLAIN,
    },
    name="SETTINGS_EVENT_TRAITS",
)
