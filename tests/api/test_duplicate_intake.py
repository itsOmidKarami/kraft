"""Duplicate detection at intake (Kraft-s7c04.30): filing an item that looks
like an open one is warned, not refused. The rule is exact, not fuzzy: the
same title in the same repo, or a bead both items say they implement."""

from __future__ import annotations

import asyncio

import pytest
from support.api import _set_status
from support.harness import make_repo

from kraft import client as kraft_client


def _create(client, repo, title="Container isolation", **body):
    r = client.post(
        "/api/work-items",
        json={"title": title, "repo": str(repo), "autostart": False, **body},
    )
    assert r.status_code == 201, r.text
    return r.json()


@pytest.mark.parametrize(
    ("first", "second", "says"),
    [
        ({}, {"title": "  container ISOLATION "}, "same title"),
        (
            {"implements_beads": ["Kraft-rki"]},
            {"title": "isolate the container", "implements_beads": ["Kraft-x", "Kraft-rki"]},
            "also implements Kraft-rki",
        ),
    ],
    ids=["title", "implements-beads"],
)
def test_filing_what_an_open_item_already_covers_is_filed_with_a_warning_naming_it(
    client, repo, first, second, says
):
    original = _create(client, repo, **first)["id"]

    again = _create(client, repo, **second)

    assert again["status"] == "paused"
    warning = again["duplicate_warning"]
    assert original in warning
    assert says in warning
    assert "set-attachments" in warning


@pytest.mark.parametrize(
    "other",
    ["abandoned", "completed", "another-repo", "different"],
)
def test_no_warning_without_an_open_item_in_the_same_repo(client, repo, tmp_path, other):
    if other == "another-repo":
        _create(client, make_repo(tmp_path, name="elsewhere"))
    elif other == "different":
        _create(client, repo, title="something else", implements_beads=["Kraft-other"])
    else:
        _set_status(_create(client, repo)["id"], other)

    again = _create(client, repo, implements_beads=["Kraft-rki"])

    assert "duplicate_warning" not in again


def test_the_warning_reaches_the_client_and_so_the_cli_and_mcp(app, repo):
    """`client.create_work_item` rebuilds its result from a few fields; a
    warning it dropped would reach nobody who files from a terminal or an
    agent, which is where the re-filing happened."""
    asyncio.run(kraft_client.ensure_repo(str(repo)))
    first = asyncio.run(kraft_client.create_work_item("Container isolation", str(repo)))

    again = asyncio.run(kraft_client.create_work_item("Container isolation", str(repo)))

    assert first["id"] in again["duplicate_warning"]
