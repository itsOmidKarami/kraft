"""V1 chain-node builders shared by more than one forge test file. Plain
helpers, not fixtures -- pytest advises against importing from conftest, so
they live here instead, beside outputs.py."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from http import HTTPStatus

from support.harness import v1_resolved

#: A route value meaning "this subcommand exits 1".
FAIL = None


class FailWith(str):
    """A route value meaning "this subcommand exits 1 saying this on stderr"."""


@dataclass
class Api:
    """A route value meaning "answer as `<cli> api` does": with `-i` an HTTP
    envelope (status line, headers, blank line, body), without it the bare body
    and, from 400 up, `<cli>: <message> (HTTP <status>)` on stderr. The exit code
    is `rc`, else 1 from 400 up, else 0."""

    status: int = 200
    body: str = "{}"
    headers: dict[str, str] = field(default_factory=dict)
    rc: int | None = None

    @property
    def exit_code(self) -> int:
        return self.rc if self.rc is not None else int(self.status >= 400)

    def envelope(self) -> str:
        head = f"HTTP/2.0 {self.status} {HTTPStatus(self.status).phrase}"
        lines = [head, *(f"{k}: {v}" for k, v in self.headers.items())]
        return "\n".join(lines) + f"\n\n{self.body}"

    def message(self, cli: str) -> str:
        try:
            said = json.loads(self.body).get("message", "")
        except (ValueError, AttributeError):
            said = ""
        return f"{cli}: {said or self.body} (HTTP {self.status})"


#: What `glab api` says of an endpoint with nothing at it.
NOT_FOUND = FailWith("glab: 404 Not Found")


def forge_node(node_id: str, target: str, **task) -> dict:
    """A one-task V1 exec node running forge `target`."""
    return {
        "id": node_id,
        "kind": "exec",
        "tasks": [{"id": node_id, "kind": "forge", "target": target, **task}],
    }


def back_half(*extra: dict, **ci_task) -> object:
    """open_mr -> mr_checks -> merge (plus `extra` nodes after it), the forge
    half of a chain. `ci_task` goes on the mr_checks task (`wait=...`)."""
    return v1_resolved(
        [
            forge_node("open_mr", "mr.open_draft"),
            forge_node("mr_checks", "mr.ci", **ci_task),
            forge_node("merge", "mr.merge"),
            *extra,
        ]
    )
