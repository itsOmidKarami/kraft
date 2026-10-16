"""What Kraft says back, worded once for both front doors: the CLI prints it,
the console draws it (spec §4.3). Plain text, no colour, no terminal."""

from __future__ import annotations


def running_agents_warning(doing: str, items: list[dict]) -> list[str]:
    """The lines naming the active `items` whose agents a server stop ends,
    `doing` being what ends them ("restarting"); none when nothing is active."""
    if not items:
        return []
    return [
        f"{doing} the server ends the agent of any active item ({len(items)}):",
        *(
            f"  {item['id']}  {item.get('current_node_id') or '-'}  {item['title']}"
            for item in items
        ),
        "  each one stops when Kraft starts again; retry it then. To avoid that, "
        "pause them first (kraft item pause ID) and resume them after.",
    ]
