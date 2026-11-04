"""The version an installed Kraft reports.

`--version` exists to diagnose an install (cli.py:255), so the number it prints
has to come from the installed distribution rather than a literal that a release
can leave behind.
"""

from __future__ import annotations

from importlib import metadata

from kraft import update
from kraft.cli import admin


def test_version_is_not_the_placeholder():
    assert metadata.version("kraft-sdlc") != "0.0.0"


def test_cli_version_matches_the_installed_distribution():
    assert admin._version() == metadata.version("kraft-sdlc")


def test_version_survives_not_being_installed(monkeypatch):
    """A source checkout that was never installed answers rather than tracebacks."""

    def missing(_name):
        raise metadata.PackageNotFoundError

    monkeypatch.setattr(admin, "_pkg_version", missing)
    assert admin._version() == "0.0.0+source"


class _Dist:
    def __init__(self, text: str) -> None:
        self.text, self.parsed = text, 0

    def read_text(self, name):
        return self.text

    @property
    def version(self) -> str:
        self.parsed += 1
        return self.text


def test_the_metadata_is_parsed_again_only_when_it_changes(monkeypatch):
    """One server start asks for the version about sixty times; an upgrade in place still shows."""
    dist = _Dist("1.0")
    monkeypatch.setattr(update, "distribution", lambda name: dist)
    monkeypatch.setattr(update, "_version_memo", None)

    assert [update.installed(), update.installed()] == ["1.0", "1.0"]
    assert dist.parsed == 1
    dist.text = "2.0"
    assert update.installed() == "2.0"


def test_an_uninstalled_checkout_has_a_version_for_the_update_checks(monkeypatch):
    def missing(name):
        raise metadata.PackageNotFoundError

    monkeypatch.setattr(update, "distribution", missing)
    assert update.installed() == "0.0.0+source"
