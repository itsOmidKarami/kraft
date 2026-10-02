"""`kraft admin doctor`'s rows about the server process itself: is it running
the version on disk, and does its pidfile name it."""

from __future__ import annotations

import pytest

from kraft import doctor, update


@pytest.mark.parametrize(
    ("payload", "says"),
    [
        (
            {"version": "1.5.0", "installed": "1.5.1", "pid": 7},
            "runs 1.5.0, but 1.5.1 is installed",
        ),
        # A server from before `installed` was reported: its `version` was read
        # from disk, so it already names the new release.
        ({"version": "1.5.1", "pid": 7}, "runs a release older than the installed 1.5.1"),
    ],
    ids=["reports-both", "predates-the-field"],
)
def test_a_server_still_running_the_old_version_is_told_to_restart(monkeypatch, payload, says):
    monkeypatch.setattr(update, "installed", lambda: "1.5.1")

    row = doctor._restart_check(payload)

    assert row["ok"] and row["warn"]
    assert says in row["detail"] and "kraft admin restart" in row["detail"]
    assert "pid 7" in row["detail"]


def test_a_server_on_the_installed_version_needs_no_restart(monkeypatch):
    monkeypatch.setattr(update, "installed", lambda: "1.5.1")

    row = doctor._restart_check({"version": "1.5.1", "installed": "1.5.1", "pid": 7})

    assert row["ok"] and not row["warn"]
    assert "1.5.1" in row["detail"]
