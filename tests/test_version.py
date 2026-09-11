"""The version an installed Kraft reports.

`--version` exists to diagnose an install (cli.py:255), so the number it prints
has to come from the installed distribution rather than a literal that a release
can leave behind.
"""

from __future__ import annotations

from importlib import metadata

from kraft.cli import admin


def test_version_is_not_the_placeholder():
    assert metadata.version("kraft") != "0.0.0"


def test_cli_version_matches_the_installed_distribution():
    assert admin._version() == metadata.version("kraft")


def test_version_survives_not_being_installed(monkeypatch):
    """A source checkout that was never installed answers rather than tracebacks."""

    def missing(_name):
        raise metadata.PackageNotFoundError

    monkeypatch.setattr(admin, "_pkg_version", missing)
    assert admin._version() == "0.0.0+source"
