"""The wire's envelope: what a refused call reads as at either front door."""

from __future__ import annotations

import pytest

from kraft import client


@pytest.mark.parametrize(
    "exc, line",
    [
        (ValueError("kraft 404: unknown work item"), "kraft: 404: unknown work item"),
        (ValueError("kraft: no gate is pending on w1"), "kraft: no gate is pending on w1"),
        (
            PermissionError("a worker session cannot act on its own work item (w1)."),
            "kraft: a worker session cannot act on its own work item (w1).",
        ),
    ],
    ids=["api-status", "client-check", "worker-guard"],
)
def test_a_refusal_reads_kraft_once(exc, line):
    """The CLI prints this line and MCP hands it to the agent. A message that
    already led with `kraft` once printed as `kraft: kraft 404: ...`."""
    assert client.refusal(exc) == line
