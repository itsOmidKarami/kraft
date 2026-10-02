"""The docs site's synced tab groups agree on their tabs.

Docus syncs a `::tabs{sync="..."}` group by tab index (localStorage
`tabs-<key>`), not by label, so two groups with the same key and a different
order show different tabs for one choice: picking Antigravity in one group
showed Gemini CLI's setup in the other.
"""

import re
from pathlib import Path

CONTENT = Path(__file__).resolve().parent.parent / "docsite" / "content"

OPEN = re.compile(r'^\s*(:{2,})tabs\{[^}]*\bsync="([^"]+)"')


def synced_groups(text: str) -> list[tuple[str, int, list[str]]]:
    """Each synced tabs group in a page: its sync key, line and tab labels."""
    lines = text.splitlines()
    groups = []
    for start, line in enumerate(lines):
        opened = OPEN.match(line)
        if not opened:
            continue
        depth = len(opened.group(1))
        item = re.compile(rf'^\s*:{{{depth + 1}}}tabs-item\{{[^}}]*\blabel="([^"]+)"')
        labels = []
        for inner in lines[start + 1 :]:
            if inner.strip() == ":" * depth:
                break
            if found := item.match(inner):
                labels.append(found.group(1))
        groups.append((opened.group(2), start + 1, labels))
    return groups


def test_synced_groups_reads_labels_in_order():
    page = "\n".join(
        [
            '::tabs{sync="agent"}',
            ':::tabs-item{label="A"}',
            ":::",
            ':::tabs-item{label="B"}',
            ":::",
            "::",
            ':::tabs-item{label="after the group"}',
        ]
    )
    assert synced_groups(page) == [("agent", 1, ["A", "B"])]


def test_synced_groups_reads_an_indented_group():
    # A group nested in a list item or another component is indented.
    page = "\n".join(
        [
            '  ::tabs{sync="agent"}',
            '  :::tabs-item{label="A"}',
            "  :::",
            "  ::",
        ]
    )
    assert synced_groups(page) == [("agent", 1, ["A"])]


def test_every_synced_tab_group_lists_the_same_labels_in_the_same_order():
    by_key: dict[str, list[tuple[str, list[str]]]] = {}
    for page in sorted(CONTENT.rglob("*.md")):
        for key, line, labels in synced_groups(page.read_text()):
            by_key.setdefault(key, []).append((f"{page.relative_to(CONTENT)}:{line}", labels))
    assert "agent" in by_key, "no sync=agent group found; the parser no longer matches the pages"
    for key, groups in by_key.items():
        for where, labels in groups:
            # A group whose labels all fail to parse would agree with any other such group.
            assert labels, f'sync="{key}": {where} has no tab labels the parser can read'
        first_where, first = groups[0]
        for where, labels in groups[1:]:
            assert labels == first, f'sync="{key}": {where} has {labels}, {first_where} has {first}'
